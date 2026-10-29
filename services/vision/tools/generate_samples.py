"""Generate services/vision/samples/*.json from REAL HandLandmarker inference.

    python -m tools.generate_samples [--count 30]

Input frames are crops of the official MediaPipe test image (see tools/frames.py).
Files contain contract messages only (no extra keys); provenance is in samples/README.md.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from src.config import Settings
from src.contract import FrameMeta, validate_landmarks_message
from src.detector import HandDetector
from src.metrics import VisionMetrics
from src.pipeline import FramePipeline
from tools import assets, frames

SAMPLES_DIR = assets.VISION_DIR / "samples"
SESSION_ID = "sample-session-001"


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    args = parser.parse_args()

    model = assets.ensure_model()
    image = assets.ensure_test_image()
    detector = HandDetector(Settings(model_path=str(model)))
    pipeline = FramePipeline(detector, VisionMetrics(), publisher=None)
    SAMPLES_DIR.mkdir(exist_ok=True)

    def run(session_id: str, frame_id: int, ts: int, jpeg: bytes) -> dict:
        result = pipeline.process(FrameMeta(session_id, frame_id, ts, frames.FRAME_W, frames.FRAME_H), jpeg)
        validate_landmarks_message(result.message)
        return result.message

    base_ts = int(time.time() * 1000)

    detected = run(SESSION_ID, 1, base_ts, frames.hand_frame(image))
    if not detected["hands"]:
        raise SystemExit("expected a hand in the hand frame but none was detected; not writing samples")
    _write(SAMPLES_DIR / "detected_hand.json", detected)

    no_hand = run(SESSION_ID + "-nohand", 2, base_ts + 33, frames.no_hand_frame(image))
    if no_hand["hands"]:
        raise SystemExit("expected no hand in the wall crop but one was detected; not writing samples")
    _write(SAMPLES_DIR / "no_hand.json", no_hand)

    sequence = []
    for i, jpeg in enumerate(frames.sliding_sequence(image, args.count)):
        # ~30 FPS spacing of the original frame timestamps
        sequence.append(run(SESSION_ID + "-seq", 100 + i, base_ts + 1000 + i * 33, jpeg))
    _write(SAMPLES_DIR / "continuous_frames.json", sequence)
    detected_count = sum(1 for m in sequence if m["hands"])
    print(f"continuous: {len(sequence)} frames, {detected_count} with a hand")

    detector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
