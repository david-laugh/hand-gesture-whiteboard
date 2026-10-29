"""Frame pipeline: latest-wins input slot → decode → inference → serialize → publish.

Each session has a single pending slot. If a new frame arrives before the previous one
started processing, the older one is replaced and counted in `dropped_frame_count`.
Dropped frames are never reported as "no hand": only frames that actually went through
inference produce a message.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .contract import FrameMeta, build_landmarks_message, serialize
from .detector import HandDetector, JpegDecodeError, decode_jpeg
from .metrics import VisionMetrics
from .publisher import GesturePublisher

log = logging.getLogger("vision.pipeline")


@dataclass(frozen=True)
class ProcessResult:
    message: dict[str, Any]
    payload: str
    decode_ms: float
    inference_ms: float
    serialization_ms: float
    image_width: int
    image_height: int


@dataclass
class _Pending:
    meta: FrameMeta
    jpeg: bytes
    received_at: float


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    replaced_frame_id: int | None


class FramePipeline:
    def __init__(self, detector: HandDetector, metrics: VisionMetrics, publisher: GesturePublisher | None) -> None:
        self._detector = detector
        self._metrics = metrics
        self._publisher = publisher
        self._pending: OrderedDict[str, _Pending] = OrderedDict()
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None

    # -- synchronous processing (runs in a worker thread) ---------------------------------
    def process(self, meta: FrameMeta, jpeg: bytes) -> ProcessResult:
        """Decode + infer + serialize one frame. Raises JpegDecodeError on bad input."""
        t0 = time.perf_counter()
        rgb = decode_jpeg(jpeg)
        decode_ms = (time.perf_counter() - t0) * 1000.0

        detection = self._detector.detect(meta.session_id, meta.ts, rgb)

        t1 = time.perf_counter()
        message = build_landmarks_message(meta, detection.hands)
        payload = serialize(message)
        serialization_ms = (time.perf_counter() - t1) * 1000.0

        height, width = rgb.shape[:2]
        if (meta.width and meta.width != width) or (meta.height and meta.height != height):
            log.debug("frame %s/%s size mismatch: meta %sx%s, decoded %sx%s",
                      meta.session_id, meta.frame_id, meta.width, meta.height, width, height)
        return ProcessResult(message, payload, decode_ms, detection.inference_ms, serialization_ms, width, height)

    def process_and_record(self, meta: FrameMeta, jpeg: bytes, queue_wait_ms: float | None = None) -> ProcessResult | None:
        m = self._metrics
        try:
            result = self.process(meta, jpeg)
        except JpegDecodeError as exc:
            m.incr("invalid_frame_count")
            m.record_frame(meta.session_id, meta.frame_id, status="invalid", error=str(exc))
            log.warning("frame %s/%s rejected: %s", meta.session_id, meta.frame_id, exc)
            return None
        except Exception as exc:
            m.incr("failed_frame_count")
            m.record_frame(meta.session_id, meta.frame_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            log.exception("inference failed for frame %s/%s", meta.session_id, meta.frame_id)
            return None

        num_hands = len(result.message["hands"])
        m.mark_processed()
        m.incr("detected_frame_count" if num_hands else "no_hand_frame_count")
        m.observe("decode_ms", result.decode_ms)
        m.observe("inference_ms", result.inference_ms)
        m.observe("serialization_ms", result.serialization_ms)
        m.observe("processing_ms", result.decode_ms + result.inference_ms + result.serialization_ms)
        if queue_wait_ms is not None:
            m.observe("queue_wait_ms", queue_wait_ms)
        m.record_frame(
            meta.session_id, meta.frame_id,
            status="processed", hands=num_hands, ts=meta.ts,
            decode_ms=round(result.decode_ms, 3),
            inference_ms=round(result.inference_ms, 3),
            serialization_ms=round(result.serialization_ms, 3),
            payload_bytes=len(result.payload.encode("utf-8")),
        )
        return result

    # -- async streaming path ---------------------------------------------------------------
    def submit(self, meta: FrameMeta, jpeg: bytes) -> SubmitResult:
        """Queue a frame for processing (event loop thread). Never blocks."""
        self._metrics.mark_input()
        self._metrics.record_frame(meta.session_id, meta.frame_id, status="queued", ts=meta.ts, jpeg_bytes=len(jpeg))
        replaced = self._pending.get(meta.session_id)
        replaced_id = None
        if replaced is not None:
            replaced_id = replaced.meta.frame_id
            self._metrics.incr("dropped_frame_count")
            self._metrics.record_frame(meta.session_id, replaced_id, status="dropped", replaced_by=meta.frame_id)
        self._pending[meta.session_id] = _Pending(meta, jpeg, time.perf_counter())
        self._wakeup.set()
        return SubmitResult(accepted=True, replaced_frame_id=replaced_id)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="frame-pipeline")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            if not self._pending:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            _, pending = self._pending.popitem(last=False)  # oldest session first (round robin)
            wait_ms = (time.perf_counter() - pending.received_at) * 1000.0
            result = await asyncio.to_thread(self.process_and_record, pending.meta, pending.jpeg, wait_ms)
            if result is None:
                continue
            if self._publisher is None:
                self._metrics.record_frame(pending.meta.session_id, pending.meta.frame_id, publish_status="disabled")
            else:
                self._publisher.publish(pending.meta.session_id, pending.meta.frame_id, result.payload)
