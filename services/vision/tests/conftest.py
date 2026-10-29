from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
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


class Broker:
    def __init__(self, host: str, port: int, container: str | None) -> None:
        self.host, self.port, self.container = host, port, container

    def restart(self) -> None:
        """Stop and start the broker (only for the docker-managed one) to test reconnects."""
        if self.container is None:
            pytest.skip("broker restart needs the docker-managed test broker")
        subprocess.run(["docker", "restart", "-t", "0", self.container], check=True, capture_output=True)
        _wait_port(self.host, self.port)


def _wait_port(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"MQTT broker {host}:{port} did not come up")


@pytest.fixture(scope="session")
def mqtt_broker():
    """VISION_MQTT_BROKER=host:port, else a throwaway eclipse-mosquitto:2 container, else skip."""
    if os.getenv("VISION_MQTT_BROKER"):
        host, port = os.environ["VISION_MQTT_BROKER"].rsplit(":", 1)
        yield Broker(host, int(port), None)
        return
    if shutil.which("docker") is None:
        pytest.skip("no MQTT broker: set VISION_MQTT_BROKER=host:port or install Docker")
    name = f"vision-pytest-mqtt-{uuid.uuid4().hex[:6]}"
    # Fixed host port and no --rm, so `docker restart` (reconnect test) keeps the same address.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    run = subprocess.run(
        ["docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:1883",
         "eclipse-mosquitto:2", "mosquitto", "-c", "/mosquitto-no-auth.conf"],
        capture_output=True, text=True,
    )
    if run.returncode != 0:
        pytest.skip(f"could not start a test MQTT broker with docker: {run.stderr.strip()[:200]}")
    try:
        _wait_port("127.0.0.1", port)
        yield Broker("127.0.0.1", port, name)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def mqtt_prefix() -> str:
    # Unique prefix per test so tests never see each other's (or a real gateway's) messages.
    return f"test-{uuid.uuid4().hex[:8]}/v1"


@pytest.fixture
def recorder(mqtt_broker, mqtt_prefix):
    from tests.landmark_recorder import LandmarkRecorder

    rec = LandmarkRecorder(mqtt_broker.host, mqtt_broker.port, prefix=mqtt_prefix).start()
    yield rec
    rec.stop()


@pytest.fixture
def settings(model_path: Path) -> Settings:
    # MQTT input is off unless a test enables it against an explicit broker.
    return Settings(model_path=str(model_path), metrics_log_interval_s=60, mqtt_enabled=False)
