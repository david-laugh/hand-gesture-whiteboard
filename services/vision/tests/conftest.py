from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config import Settings
from tools import assets, frames

# Set VISION_REQUIRE_MODEL=1 to turn "model/test image missing" into a failure instead of a skip.
REQUIRE_MODEL = os.getenv("VISION_REQUIRE_MODEL") == "1"


def _require(path: Path, what: str) -> Path:
    if path.is_file():
        return path
    message = f"{what} not found at {path}; run `python -m tools.assets`"
    if REQUIRE_MODEL:
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def model_path() -> Path:
    return _require(Path(os.getenv("MODEL_PATH", assets.MODEL_PATH)), "hand_landmarker.task")


@pytest.fixture(scope="session")
def test_image() -> Path:
    return _require(assets.TEST_IMAGE_PATH, "test image")


@pytest.fixture(scope="session")
def hand_jpeg(test_image: Path) -> bytes:
    return frames.hand_frame(test_image)


@pytest.fixture(scope="session")
def no_hand_jpeg(test_image: Path) -> bytes:
    return frames.no_hand_frame(test_image)


@pytest.fixture
def settings(model_path: Path) -> Settings:
    # MQTT input is off unless a test enables it against an explicit broker.
    return Settings(model_path=str(model_path), metrics_log_interval_s=60, mqtt_enabled=False)
