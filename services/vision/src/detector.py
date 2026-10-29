"""JPEG decoding and MediaPipe Tasks HandLandmarker inference.

mediapipe 1.x no longer ships the legacy `mp.solutions.hands` API, so this uses the
official Tasks API (`mediapipe.tasks.python.vision.HandLandmarker`) in VIDEO mode:

- VIDEO mode is synchronous: each call returns the result for exactly the frame passed
  in, so frame_id ↔ result mapping is exact. LIVE_STREAM mode may silently drop frames
  inside MediaPipe and returns results through a callback; we do frame dropping
  ourselves (latest-wins, see pipeline.py) so that every drop is counted.
- VIDEO mode keeps hand tracking state between frames, so each session gets its own
  landmarker instance and strictly increasing timestamps.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision as mp_vision

from .config import Settings
from .contract import NUM_LANDMARKS, Hand

log = logging.getLogger("vision.detector")

MEDIAPIPE_API = "mediapipe.tasks.python.vision.HandLandmarker"
RUNNING_MODE = "VIDEO"


class ModelLoadError(RuntimeError):
    pass


class JpegDecodeError(ValueError):
    pass


def decode_jpeg(data: bytes) -> np.ndarray:
    """Decode JPEG bytes into a contiguous RGB uint8 array (MediaPipe expects SRGB)."""
    if not data:
        raise JpegDecodeError("empty image payload")
    bgr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise JpegDecodeError("cv2.imdecode failed (not a valid JPEG?)")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


@dataclass(frozen=True)
class Detection:
    hands: list[Hand]
    inference_ms: float


class _SessionTracker:
    def __init__(self, landmarker: mp_vision.HandLandmarker) -> None:
        self.landmarker = landmarker
        self.last_ts = -1


class HandDetector:
    """Thread-safe wrapper; calls are serialized because HandLandmarker is not re-entrant."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        path = Path(settings.model_path)
        if not path.is_file():
            raise ModelLoadError(
                f"model file not found: {path} (download the official hand_landmarker.task; see services/vision/README.md)"
            )
        self._model_bytes = path.read_bytes()
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, _SessionTracker] = OrderedDict()
        # Fail fast at startup if the model cannot be loaded.
        try:
            self._create_landmarker().close()
        except Exception as exc:
            raise ModelLoadError(f"failed to load HandLandmarker model {path}: {exc}") from exc
        log.info(
            "HandLandmarker ready: model=%s (%d bytes) mediapipe=%s mode=%s num_hands=%d det=%.2f presence=%.2f track=%.2f",
            path, len(self._model_bytes), mp.__version__, RUNNING_MODE, settings.num_hands,
            settings.min_hand_detection_confidence, settings.min_hand_presence_confidence,
            settings.min_tracking_confidence,
        )

    def _create_landmarker(self) -> mp_vision.HandLandmarker:
        s = self._settings
        options = mp_vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=self._model_bytes),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=s.num_hands,
            min_hand_detection_confidence=s.min_hand_detection_confidence,
            min_hand_presence_confidence=s.min_hand_presence_confidence,
            min_tracking_confidence=s.min_tracking_confidence,
        )
        return mp_vision.HandLandmarker.create_from_options(options)

    def _tracker(self, session_id: str) -> _SessionTracker:
        tracker = self._sessions.get(session_id)
        if tracker is None:
            tracker = _SessionTracker(self._create_landmarker())
            self._sessions[session_id] = tracker
            while len(self._sessions) > self._settings.max_sessions:
                evicted_id, evicted = self._sessions.popitem(last=False)
                evicted.landmarker.close()
                log.info("evicted landmarker for session %s", evicted_id)
        else:
            self._sessions.move_to_end(session_id)
        return tracker

    def detect(self, session_id: str, ts: int, rgb: np.ndarray) -> Detection:
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        with self._lock:
            tracker = self._tracker(session_id)
            # VIDEO mode requires strictly increasing timestamps per landmarker.
            mp_ts = max(int(ts), tracker.last_ts + 1)
            tracker.last_ts = mp_ts
            t0 = time.perf_counter()
            result = tracker.landmarker.detect_for_video(image, mp_ts)
            inference_ms = (time.perf_counter() - t0) * 1000.0
        return Detection(hands=self._to_hands(result), inference_ms=inference_ms)

    def _to_hands(self, result: mp_vision.HandLandmarkerResult) -> list[Hand]:
        hands: list[Hand] = []
        for landmarks, categories in zip(result.hand_landmarks, result.handedness):
            if len(landmarks) != NUM_LANDMARKS or not categories:
                log.warning("skipping malformed hand result (%d landmarks)", len(landmarks))
                continue
            top = categories[0]
            label = top.category_name
            if self._settings.swap_handedness:
                label = "Left" if label == "Right" else "Right"
            hands.append(
                Hand(
                    handedness=label,
                    score=float(top.score),
                    landmarks=[(lm.x, lm.y, lm.z) for lm in landmarks],
                )
            )
        return hands[: self._settings.num_hands]

    def reset_session(self, session_id: str) -> bool:
        with self._lock:
            tracker = self._sessions.pop(session_id, None)
        if tracker is None:
            return False
        tracker.landmarker.close()
        return True

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def close(self) -> None:
        with self._lock:
            for tracker in self._sessions.values():
                tracker.landmarker.close()
            self._sessions.clear()
