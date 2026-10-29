"""vision 팀이 올린 실제 MediaPipe 추론 샘플 (services/vision/samples/ 복사본)로 확인.

샘플은 정지 사진(손바닥 쪽, 네 손가락 모두 펴짐)의 crop이라 그리기 자세가 아니다.
그래서 여기서는 "오류 없이 읽고, 출력이 schema를 통과하는지"만 본다.
"""
import json
from pathlib import Path

import pytest

from src.adapter import to_landmark_message
from src.processor import GestureProcessor

SAMPLES = Path(__file__).parent / "fixtures" / "vision_samples"


def load(name: str):
    data = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def run(name: str) -> list[dict]:
    proc = GestureProcessor()
    return [proc.process(to_landmark_message(m)) for m in load(name)]


def test_detected_hand_passes_adapter_and_schema(gesture_validator):
    (raw,) = load("detected_hand.json")
    msg = to_landmark_message(raw)
    assert len(msg.hands) == 1 and len(msg.hands[0].landmarks) == 21
    assert msg.trace_id == f"{raw['session_id']}-{raw['frame_id']}"
    (out,) = run("detected_hand.json")
    gesture_validator.validate(out)
    assert out["pointer"] is not None


def test_no_hand_is_idle_with_null_pointer(gesture_validator):
    (out,) = run("no_hand.json")
    gesture_validator.validate(out)
    assert out["gesture"] == "IDLE"
    assert out["pointer"] is None


def test_continuous_frames_process_without_error(gesture_validator):
    raws = load("continuous_frames.json")
    outs = run("continuous_frames.json")
    assert len(outs) == len(raws) == 30
    for raw, out in zip(raws, outs):
        gesture_validator.validate(out)
        assert out["frame_id"] == raw["frame_id"]
        assert out["session_id"] == raw["session_id"]


@pytest.mark.parametrize("name", ["detected_hand.json", "no_hand.json"])
def test_samples_over_http(client, gesture_validator, name):
    (raw,) = load(name)
    res = client.post("/v1/classify", json=raw)
    assert res.status_code == 200
    gesture_validator.validate(res.json())
