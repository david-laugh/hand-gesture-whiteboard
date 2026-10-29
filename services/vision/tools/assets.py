"""Official MediaPipe assets used for local development and tests.

    python -m tools.assets          # download model + test image (verifies model sha256)

Download errors are raised, never swallowed.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parents[1]

# Pinned model version 1 (float16). Same bytes as .../float16/latest/ as of 2026-10-29.
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
MODEL_PATH = VISION_DIR / "models" / "hand_landmarker.task"

# Test image from the official MediaPipe Hand Landmarker Python example.
TEST_IMAGE_URL = "https://storage.googleapis.com/mediapipe-tasks/hand_landmarker/woman_hands.jpg"
TEST_IMAGE_PATH = VISION_DIR / "tests" / "data" / "woman_hands.jpg"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url: str, dest: Path, expected_sha256: str | None = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp:  # raises on HTTP/network errors
        tmp.write_bytes(resp.read())
    if expected_sha256 is not None and sha256(tmp) != expected_sha256:
        tmp.unlink()
        raise RuntimeError(f"checksum mismatch for {url}")
    tmp.replace(dest)
    return dest


def ensure_model() -> Path:
    if MODEL_PATH.is_file() and sha256(MODEL_PATH) == MODEL_SHA256:
        return MODEL_PATH
    return download(MODEL_URL, MODEL_PATH, MODEL_SHA256)


def ensure_test_image() -> Path:
    if TEST_IMAGE_PATH.is_file():
        return TEST_IMAGE_PATH
    return download(TEST_IMAGE_URL, TEST_IMAGE_PATH)


if __name__ == "__main__":
    print("model:", ensure_model())
    print("test image:", ensure_test_image())
    sys.exit(0)
