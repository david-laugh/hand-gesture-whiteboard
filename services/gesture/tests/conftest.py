import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from src.main import app, sessions
from src.models import LandmarkMessage

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


@pytest.fixture(autouse=True)
def clear_sessions():
    """테스트끼리 세션 상태가 섞이지 않게."""
    sessions.clear()
    yield
    sessions.clear()


@pytest.fixture
def fixture():
    """fixture("index_pose") 처럼 호출하면 매번 새 복사본을 돌려준다."""
    return lambda name: copy.deepcopy(load_fixture(name))


@pytest.fixture(scope="session")
def gesture_validator():
    schema = json.loads((SHARED / "contracts" / "gesture.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.fixture(scope="session")
def landmark_validator():
    schema = json.loads((SHARED / "contracts" / "landmark.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.fixture
def client():
    return TestClient(app)
