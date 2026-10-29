"""In-process performance counters (no external monitoring stack).

All timings are milliseconds. Each timing is a separate stage:
  decode_ms          JPEG decode + BGR→RGB
  inference_ms       HandLandmarker.detect_for_video() only
  serialization_ms   contract dict build + json.dumps
  publish_ms         paho publish() call for one landmarks message (enqueue to paho's network thread)
  queue_wait_ms      frame received → processing started (latest-wins slot)
  processing_ms      decode + inference + serialization for one frame

input_fps counts frames received; processing_fps counts frames that finished inference.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from typing import Any

TIMING_NAMES = (
    "decode_ms",
    "inference_ms",
    "serialization_ms",
    "publish_ms",
    "queue_wait_ms",
    "processing_ms",
)

COUNTER_NAMES = (
    "input_frame_count",
    "invalid_frame_count",
    "processed_frame_count",
    "dropped_frame_count",
    "failed_frame_count",
    "detected_frame_count",
    "no_hand_frame_count",
    "publish_count",
    "publish_dropped_count",
    "publish_error_count",
    "mqtt_message_count",
    "mqtt_connect_count",
    "mqtt_disconnect_count",
)


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_values:
        return math.nan
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


class RollingStat:
    def __init__(self, maxlen: int) -> None:
        self._values: deque[float] = deque(maxlen=maxlen)
        self._total_count = 0

    def add(self, value: float) -> None:
        self._values.append(value)
        self._total_count += 1

    def summary(self) -> dict[str, Any]:
        values = sorted(self._values)
        if not values:
            return {"count": self._total_count, "window": 0, "avg": None, "p50": None, "p95": None, "max": None}
        return {
            "count": self._total_count,
            "window": len(values),
            "avg": round(sum(values) / len(values), 3),
            "p50": round(percentile(values, 50), 3),
            "p95": round(percentile(values, 95), 3),
            "max": round(values[-1], 3),
        }


class RateMeter:
    """Events per second over a sliding time window."""

    def __init__(self, window_s: float = 5.0) -> None:
        self._window_s = window_s
        self._stamps: deque[float] = deque()

    def mark(self, now: float | None = None) -> None:
        now = time.perf_counter() if now is None else now
        self._stamps.append(now)
        self._trim(now)

    def _trim(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] > self._window_s:
            self._stamps.popleft()

    def rate(self, now: float | None = None) -> float:
        now = time.perf_counter() if now is None else now
        self._trim(now)
        if len(self._stamps) < 2:
            return 0.0
        span = self._stamps[-1] - self._stamps[0]
        return (len(self._stamps) - 1) / span if span > 0 else 0.0


class VisionMetrics:
    def __init__(self, window: int = 1000, frame_log_size: int = 300) -> None:
        self._lock = threading.Lock()
        self._window = window
        self._frame_log_size = frame_log_size
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._counters = {name: 0 for name in COUNTER_NAMES}
            self._timings = {name: RollingStat(self._window) for name in TIMING_NAMES}
            self._input_rate = RateMeter()
            self._processing_rate = RateMeter()
            self._frames: OrderedDict[tuple[str, int], dict[str, Any]] = OrderedDict()
            self._started_at = time.time()

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] += n

    def observe(self, name: str, value_ms: float) -> None:
        with self._lock:
            self._timings[name].add(value_ms)

    def mark_input(self) -> None:
        with self._lock:
            self._counters["input_frame_count"] += 1
            self._input_rate.mark()

    def mark_processed(self) -> None:
        with self._lock:
            self._counters["processed_frame_count"] += 1
            self._processing_rate.mark()

    def record_frame(self, session_id: str, frame_id: int, **fields: Any) -> None:
        """Per-frame record keyed by (session_id, frame_id); later calls merge fields."""
        key = (session_id, frame_id)
        with self._lock:
            record = self._frames.get(key)
            if record is None:
                record = {"session_id": session_id, "frame_id": frame_id}
                self._frames[key] = record
                while len(self._frames) > self._frame_log_size:
                    self._frames.popitem(last=False)
            record.update(fields)

    def recent_frames(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in list(self._frames.values())[-limit:]]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "since_epoch_s": round(self._started_at, 3),
                "uptime_s": round(time.time() - self._started_at, 3),
                **self._counters,
                "input_fps": round(self._input_rate.rate(), 2),
                "processing_fps": round(self._processing_rate.rate(), 2),
                "timings_ms": {name: stat.summary() for name, stat in self._timings.items()},
            }
