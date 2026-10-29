import copy
import json
import math
import os
from pathlib import Path

# 테스트는 실제 브로커 없이 돈다. src를 import하기 전에 설정해야 한다.
os.environ["MQTT_ENABLED"] = "false"
os.environ["WS_INPUT_ENABLED"] = "true"  # 기본은 꺼짐. WS 입력 테스트를 위해 켠다.

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402

from src.main import app, processor  # noqa: E402
from src.models import LandmarkMessage  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _shared_dir() -> Path:
    """컨테이너에서는 /shared, 로컬에서는 레포의 shared/ 를 쓴다."""
    for candidate in (Path("/shared"), Path(__file__).resolve().parents[3] / "shared"):
        if (candidate / "contracts").is_dir():
            return candidate
    raise FileNotFoundError("shared/contracts 폴더를 찾을 수 없습니다")


SHARED = _shared_dir()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def load_landmarks(name: str):
    """fixture 파일의 첫 번째 손 21개 점."""
    return LandmarkMessage.model_validate(load_fixture(name)).hands[0].landmarks


def enter_frames_at(interval_ms: float) -> int:
    """프레임 간격이 interval_ms일 때 *_START까지 필요한 프레임 수 (state_machine과 같은 규칙)."""
    from src import config
    return max(config.DRAW_ENTER_FRAMES, math.ceil((config.STATE_ENTER_MS - 5) / interval_ms))


def exit_frames_at(interval_ms: float) -> int:
    """프레임 간격이 interval_ms일 때 *_END까지 필요한 프레임 수."""
    from src import config
    return max(config.DRAW_EXIT_FRAMES, math.ceil((config.STATE_EXIT_MS - 5) / interval_ms))


def to_vision_format(repo_msg: dict) -> dict:
    """레포 LandmarkMessage → vision 자체 형식 (services/vision/src/contract.py)."""
    return {
        "type": "landmarks",
        "session_id": repo_msg["session_id"],
        "frame_id": repo_msg["frame_id"],
        "ts": repo_msg["timestamp_ms"],
        "hands": [
            {
                "handedness": h["handedness"],
                "score": h["confidence"],
                "landmarks": [[p["x"], p["y"], p["z"]] for p in h["landmarks"]],
            }
            for h in repo_msg["hands"]
        ],
        "gesture": None,
    }


@pytest.fixture(autouse=True)
def clear_sessions():
    """테스트끼리 세션 상태가 섞이지 않게."""
    processor.clear()
    yield
    processor.clear()


@pytest.fixture
def fixture():
    """fixture("index_pose") 처럼 호출하면 매번 새 복사본을 돌려준다."""
    return lambda name: copy.deepcopy(load_fixture(name))


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((SHARED / "contracts" / name).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.fixture(scope="session")
def gesture_validator():
    return _validator("gesture.schema.json")


@pytest.fixture(scope="session")
def landmark_validator():
    return _validator("landmark.schema.json")


@pytest.fixture
def client():
    return TestClient(app)
