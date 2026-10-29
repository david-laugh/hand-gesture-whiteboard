"""2차 제스처: 지우개(집게), UNDO(주먹), 펜 색(브이), 굵기(손바닥)."""
from types import SimpleNamespace

import pytest

from src.hand_pose import (
    DRAW, ERASE, FIST, NONE, PALM, V, chirality, detect_pose, finger_ratio, mirrored, pinch_ratio,
)
from src.processor import GestureProcessor, build_gesture_message
from src.state_machine import GestureStateMachine
from tests.conftest import load_landmarks

FRAME_MS = 33
COLORS = ["#172554", "#dc2626", "#ea580c"]


def pts(lms):
    return [SimpleNamespace(x=p.x, y=p.y) for p in lms]


DRAW_LMS = pts(load_landmarks("index_pose"))
FIST_LMS = pts(load_landmarks("fist"))
PALM_LMS = pts(load_landmarks("open_palm"))
HAND = ((DRAW_LMS[0].x - DRAW_LMS[9].x) ** 2 + (DRAW_LMS[0].y - DRAW_LMS[9].y) ** 2) ** 0.5


def v_sign():
    lms = pts(DRAW_LMS)
    for i in (10, 11, 12):  # 중지를 손바닥 fixture처럼 편다
        lms[i] = SimpleNamespace(x=PALM_LMS[i].x, y=PALM_LMS[i].y)
    return lms


def pinch(ratio: float):
    """엄지 끝(4)을 검지 끝(8)에서 손 크기 × ratio 만큼 떨어뜨린 손."""
    lms = pts(DRAW_LMS)
    lms[4] = SimpleNamespace(x=lms[8].x + ratio * HAND, y=lms[8].y)
    return lms


V_LMS = v_sign()
PINCH_LMS = pinch(0.10)


class Runner:
    def __init__(self, **kw):
        defaults = dict(enter_frames=3, exit_frames=5, hold_ms=500, cooldown_ms=1000, colors=COLORS,
                        brush_sizes=[4, 8, 16], brush_default=8, eraser_sizes=[20, 30, 50], eraser_default=30,
                        canvas_min_side=720, pinch_enter=0.30, pinch_exit=0.45, normal_sign=1)
        self.m = GestureStateMachine(**{**defaults, **kw})
        self.ts = 1_000_000

    def feed(self, lms, n=1):
        out = []
        for _ in range(n):
            self.ts += FRAME_MS
            out.append(self.m.step(lms, self.ts, self.ts))
        return out


def gestures(results):
    return [r.gesture for r in results]


# --- 포즈 판별 ------------------------------------------------------------------

@pytest.mark.parametrize("lms, pose", [(DRAW_LMS, DRAW), (FIST_LMS, FIST), (PALM_LMS, PALM),
                                       (V_LMS, V), (PINCH_LMS, ERASE)])
def test_detect_pose(lms, pose):
    assert detect_pose(lms).pose == pose


def test_fist_is_not_pinch_even_though_thumb_is_close():
    """주먹은 엄지가 검지 옆에 붙어 dist(4,8)가 작지만, 검지가 깊게 접혀 있어 집게가 아니다."""
    assert pinch_ratio(FIST_LMS) < 0.30
    assert finger_ratio(FIST_LMS, (8, 6)) < -0.15
    assert detect_pose(FIST_LMS).pose == FIST


def test_pinch_point_is_midpoint_of_thumb_and_index_tips():
    det = detect_pose(PINCH_LMS)
    assert det.point.x == pytest.approx((PINCH_LMS[4].x + PINCH_LMS[8].x) / 2)
    assert det.point.y == pytest.approx((PINCH_LMS[4].y + PINCH_LMS[8].y) / 2)


def test_flip_fix_generalized_only_when_no_pose():
    sign = chirality(V_LMS)
    det = detect_pose(mirrored(V_LMS), normal_sign=sign)  # 원래 번호로는 "--RP" → 아무 포즈 아님
    assert (det.pose, det.flip_fixed) == (V, True)
    # 원래 번호로 이미 포즈가 나오면 (주먹) 뒤집지 않는다
    det = detect_pose(mirrored(FIST_LMS), normal_sign=chirality(FIST_LMS))
    assert (det.pose, det.flip_fixed) == (FIST, False)
    # 보정을 끄면 NONE
    assert detect_pose(mirrored(V_LMS), normal_sign=sign, flip_fix=False).pose == NONE


# --- 지우개 ---------------------------------------------------------------------

def test_pinch_erases_start_move_end():
    r = Runner()
    res = r.feed(PINCH_LMS, 4) + r.feed(None, 5)
    assert gestures(res) == ["IDLE", "IDLE", "ERASE_START", "ERASE_MOVE"] + ["ERASE_MOVE"] * 4 + ["ERASE_END"]
    start = res[2]
    assert start.params == {"eraser_radius": round(30 / 1440, 5)}
    mid = detect_pose(PINCH_LMS).point
    assert start.pointer == pytest.approx((mid.x, mid.y))
    assert len({x.stroke_id for x in res[2:]}) == 1


def test_pinch_boundary_does_not_flicker():
    r = Runner()
    # 진입값(0.30)과 이탈값(0.45) 사이에서는 집게로 들어가지 않는다 (검지만 편 손이므로 그리기가 됨)
    g = gestures(r.feed(pinch(0.38), 10))
    assert "ERASE_START" not in g and "DRAW_START" in g
    r.feed(None, 6)
    # 지우는 중에는 그 사이 값이 오가도 끝나지 않는다
    r.feed(PINCH_LMS, 3)
    res = []
    for ratio in [0.35, 0.42, 0.33, 0.44, 0.40, 0.36, 0.43, 0.41]:
        res += r.feed(pinch(ratio))
    assert set(gestures(res)) == {"ERASE_MOVE"}
    # 이탈값을 넘으면 M프레임 뒤에 끝난다
    assert gestures(r.feed(pinch(0.60), 5))[-1] == "ERASE_END"


def test_switch_draw_to_erase_ends_draw_first():
    r = Runner()
    res = r.feed(DRAW_LMS, 4) + r.feed(PINCH_LMS, 7)
    g = gestures(res)
    end, start = g.index("DRAW_END"), g.index("ERASE_START")
    assert end < start
    assert "DRAW_MOVE" not in g[start:] and "ERASE_MOVE" not in g[:end]


# --- 일회성 명령 ----------------------------------------------------------------

def test_fist_hold_undo_once_until_pose_changes():
    r = Runner()
    res = r.feed(FIST_LMS, 60)  # 약 2초
    assert gestures(res).count("UNDO") == 1
    undo_at = gestures(res).index("UNDO")
    assert undo_at * FRAME_MS >= 500 - FRAME_MS
    undo = res[undo_at]
    assert undo.pointer is None and undo.stroke_id is None
    # 자세를 바꿨다가 다시 주먹 → 쿨다운(1초)이 지났으므로 다시 UNDO
    r.feed(None, 3)
    assert gestures(r.feed(FIST_LMS, 20)).count("UNDO") == 1


def test_cooldown_blocks_quick_second_command():
    r = Runner()
    first = r.feed(FIST_LMS, 18)
    assert gestures(first).count("UNDO") == 1
    quick = r.feed(V_LMS, 17)  # 0.5초 유지했지만 직전 명령 후 1초가 안 됨
    assert not any(x.params.get("color") for x in quick)
    later = r.feed(V_LMS, 15)  # 쿨다운이 지나면 나간다
    assert [x.params["color"] for x in later if "color" in x.params] == [COLORS[1]]


def test_v_cycles_pen_color_and_next_stroke_uses_it():
    r = Runner()
    res = r.feed(V_LMS, 20)
    colors = [x.params["color"] for x in res if "color" in x.params]
    assert colors == [COLORS[1]]
    assert all(x.gesture == "IDLE" for x in res)
    start = [x for x in r.feed(DRAW_LMS, 4) if x.gesture == "DRAW_START"][0]
    assert start.params == {"color": COLORS[1], "brush_size": 8}


def test_palm_cycles_size_of_last_used_tool():
    r = Runner()
    res = r.feed(PALM_LMS, 20)
    assert [x.params for x in res if x.params] == [{"color": COLORS[0], "brush_size": 16}]
    start = [x for x in r.feed(DRAW_LMS, 4) if x.gesture == "DRAW_START"][0]
    assert start.params["brush_size"] == 16
    # 지우개를 쓴 뒤에는 손바닥이 지우개 크기를 바꾼다: 30 → 50px
    r.feed(None, 6)
    r.feed(PINCH_LMS, 4)
    r.feed(None, 6)
    res = r.feed(PALM_LMS, 40)
    assert [x.params for x in res if x.params] == [{"eraser_radius": round(50 / 1440, 5)}]


def test_folding_to_fist_while_drawing_does_not_undo():
    """그리다가 주먹을 쥐어도(오래 유지해도) UNDO는 나가지 않는다."""
    r = Runner()
    res = r.feed(DRAW_LMS, 10) + r.feed(FIST_LMS, 60)
    g = gestures(res)
    assert "DRAW_END" in g
    assert "UNDO" not in g
    # 손을 바꿨다가 다시 주먹을 쥐면 그때는 UNDO
    r.feed(None, 3)
    assert "UNDO" in gestures(r.feed(FIST_LMS, 20))


def test_no_command_while_drawing_or_erasing():
    r = Runner()
    res = r.feed(DRAW_LMS, 4)
    # 그리는 중 V가 잠깐 (M보다 짧게) 나와도 색이 바뀌지 않는다
    res += r.feed(V_LMS, 4) + r.feed(DRAW_LMS, 3)
    assert not any(x.params.get("color") for x in res[4:])


# --- 출력 형식 ------------------------------------------------------------------

def test_new_messages_pass_schema(gesture_validator):
    r = Runner()
    results = (r.feed(V_LMS, 20) + r.feed(None, 3) + r.feed(PALM_LMS, 40) + r.feed(None, 3)
               + r.feed(DRAW_LMS, 4) + r.feed(PINCH_LMS, 10) + r.feed(None, 6) + r.feed(FIST_LMS, 40))
    kinds = {x.gesture for x in results}
    assert {"ERASE_START", "ERASE_END", "DRAW_START", "UNDO"} <= kinds
    ids = {"trace_id": "trace-0001", "session_id": "s-1", "frame_id": 1}
    for x in results:
        gesture_validator.validate(build_gesture_message(ids, x, 0.9, 0, 0.1))


def test_processor_keeps_tool_settings_after_gap():
    """끊김 감시가 획을 닫아도 세션의 펜 색 설정은 남는다."""
    t = [100.0]
    proc = GestureProcessor(clock=lambda: t[0])
    m = proc._sessions.setdefault("s", __import__("src.processor", fromlist=["_Session"])._Session(
        GestureStateMachine(colors=COLORS)))
    m.machine._color_i = 2
    m.ids = {"trace_id": "trace-0001", "session_id": "s", "frame_id": 1}
    t[0] += 1.0
    proc.expire()
    assert proc._sessions["s"].machine.color == COLORS[2]
