"""실제 폰 줌 시도 녹화(rec04, 약 15fps)에서 잘라낸 구간으로 하는 회귀 테스트.

이 녹화에서는 검지를 둥글게 말아 엄지에 꼭 붙인 집게가 "주먹"으로 판별되어 UNDO가 나가고,
벌리는 중간에 검지가 펴지면서 "그리기"로 바뀌어 선이 그려졌다.
원본: dev/recordings/rec04_zoom.jsonl (커밋하지 않음). ts는 녹화된 값(실제 폰 캡처 시각) 그대로 쓴다.
"""
import json
from pathlib import Path

import pytest

from src.adapter import to_landmark_message
from src.hand_pose import Thresholds
from src.processor import GestureProcessor
from src.state_machine import GestureStateMachine

REC = Path(__file__).parent / "fixtures" / "rec04"


def run(name: str, **kw):
    m = GestureStateMachine(**kw)
    out = []
    for line in (REC / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        msg = to_landmark_message(json.loads(line))
        lms = msg.hands[0].landmarks if msg.hands else None
        out.append((msg.frame_id, m.step(lms, msg.timestamp_ms, msg.timestamp_ms)))
    return out


def gestures(res):
    return [r.gesture for _, r in res]


def test_curled_pinch_starts_zoom_not_undo():
    """꼭 붙인 집게(검지가 말려 비율 < 0)가 주먹이 아니라 줌으로 시작된다. 그리기나 UNDO는 나오지 않는다.
    (이 구간은 끝에 손 전체를 펴서 손바닥이 되므로 확대 장면으로는 쓰지 않는다.)"""
    g = gestures(run("pinch_zoom_490_530"))
    assert g.count("ZOOM_START") == 1
    assert "DRAW_START" not in g and "UNDO" not in g


def test_pinch_held_still_resets_zoom_to_1x_once():
    """집게를 벌리지 않고 1초 유지하면 ZOOM_UPDATE(1.0) → ZOOM_END. 집게를 계속 쥐고 있어도 줌이 다시 시작되지 않고 UNDO도 없다."""
    res = run("pinch_zoom_1110_1235", zoom_reset_hold_ms=1000)
    g = gestures(res)
    assert g.count("ZOOM_START") == 1 and g.count("ZOOM_END") == 1
    i_end = g.index("ZOOM_END")
    assert res[i_end - 1][1].gesture == "ZOOM_UPDATE" and res[i_end - 1][1].params["zoom_scale"] == 1.0
    assert res[i_end][1].params["zoom_scale"] == 1.0
    start_f, end_f = res[g.index("ZOOM_START")][0], res[i_end][0]
    assert 12 <= end_f - start_f <= 25  # 약 15fps에서 1초 남짓
    assert "UNDO" not in g[g.index("ZOOM_START"):]


def test_zoom_kept_while_pinch_held_without_reset():
    """1배 복귀를 끄면, 집게를 유지하는 동안(검지 비율이 0 아래로 내려가도) 줌이 끊기지 않는다."""
    res = run("pinch_zoom_1110_1235", zoom_reset_hold_ms=10**9)
    zoom = [f for f, r in res if r.gesture.startswith("ZOOM")]
    assert gestures(res).count("ZOOM_START") == 1
    assert zoom[-1] - zoom[0] >= 80
    assert not any(r.gesture == "UNDO" for f, r in res if zoom[0] <= f <= zoom[-1])


def test_without_tight_pinch_rule_it_was_read_as_fist():
    """수정 전 동작 재현: 꼭 붙인 집게 규칙과 줌 유지 규칙을 끄면 줌이 안 되고 그리기가 나온다."""
    old = Thresholds(zoom_pinch_tight=0.0, zoom_keep_index_min=0.0)
    g = gestures(run("pinch_zoom_490_530", thresholds=old))
    assert "ZOOM_START" not in g


@pytest.mark.parametrize("name", ["pinch_zoom_490_530", "pinch_zoom_1110_1235"])
def test_outputs_pass_schema(name, gesture_validator):
    proc = GestureProcessor()
    for line in (REC / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        gesture_validator.validate(proc.process(to_landmark_message(json.loads(line))))
