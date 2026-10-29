"""제스처: 지우개(다섯 손끝 모으기), 줌(집게/L자), UNDO(주먹), 펜 색(브이), 굵기(손바닥)."""
import math
from types import SimpleNamespace

import pytest

from src.hand_pose import (
    DRAW, ERASE, FIST, NONE, PALM, V, ZOOM, Thresholds, chirality, detect_pose, mirrored, tip_wrist, thumb_spread,
)
from src.processor import GestureProcessor, _Session, build_gesture_message
from src.state_machine import GestureStateMachine
from tests.conftest import load_landmarks

FRAME_MS = 33
COLORS = ["#172554", "#dc2626", "#ea580c"]


def P(x, y):
    return SimpleNamespace(x=x, y=y)


def pts(lms):
    return [P(p.x, p.y) for p in lms]


DRAW_LMS = pts(load_landmarks("index_pose"))
FIST_LMS = pts(load_landmarks("fist"))
PALM_LMS = pts(load_landmarks("open_palm"))
HAND = math.dist((DRAW_LMS[0].x, DRAW_LMS[0].y), (DRAW_LMS[9].x, DRAW_LMS[9].y))


def v_sign():
    lms = pts(DRAW_LMS)
    for i in (10, 11, 12):  # 중지를 손바닥 fixture처럼 편다
        lms[i] = P(PALM_LMS[i].x, PALM_LMS[i].y)
    return lms


def bunch(tw: float):
    """네 손가락이 "접힘"으로 보이면서 손끝이 한 점에 모인 손. tw = tip_wrist 목표값.
    손목 (0.5, 0.9), 손 크기 0.2. 둘째 마디(PIP)를 손끝보다 멀리 두어 접힘으로 판별되게 한다."""
    hs, wx, wy = 0.2, 0.5, 0.9
    lms = [P(0.5, 0.5) for _ in range(21)]
    lms[0] = P(wx, wy)
    for mcp, x in ((5, 0.44), (9, 0.50), (13, 0.55), (17, 0.59)):
        lms[mcp] = P(x, wy - hs if mcp == 9 else wy - hs + 0.01)
        lms[mcp + 1] = P(x, wy - hs * (tw + 0.15))  # PIP: 손끝보다 손목에서 멀다 → 접힘
        lms[mcp + 2] = P(x, wy - hs * (tw + 0.10))
    tip = P(wx, wy - hs * tw)
    for t in (8, 12, 16, 20, 4):
        lms[t] = P(tip.x, tip.y)
    lms[1], lms[2], lms[3] = P(0.42, 0.85), P(0.40, 0.80), P(0.45, 0.75)
    return lms


def thumb_at(x, y):
    lms = pts(DRAW_LMS)
    lms[4] = P(x, y)
    return lms


def pinch_zoom(ratio: float):
    """검지만 편 손에서 엄지 끝(4)을 검지 끝(8)에서 손 크기 × ratio 만큼 떨어뜨림."""
    return thumb_at(DRAW_LMS[8].x + ratio * HAND, DRAW_LMS[8].y)


def l_shape(spread: float):
    """엄지 끝을 검지 시작 마디(5)에서 손 크기 × spread 만큼 바깥(왼쪽)으로 벌림."""
    return thumb_at(DRAW_LMS[5].x - spread * HAND, DRAW_LMS[5].y)


V_LMS = v_sign()
BUNCH_LMS = bunch(1.30)
PINCH_LMS = pinch_zoom(0.10)
L_LMS = l_shape(1.60)


class Runner:
    def __init__(self, **kw):
        defaults = dict(enter_frames=3, exit_frames=5, hold_ms=500, undo_hold_ms=700, cooldown_ms=1000, colors=COLORS,
                        brush_sizes=[4, 8, 16], brush_default=8, eraser_sizes=[20, 30, 50], eraser_default=30,
                        canvas_min_side=720, normal_sign=1, thresholds=Thresholds(
                            ratio=0.15, erase_enter=1.20, erase_exit=1.15, fist_max=1.18, zoom_pinch=0.20,
                            zoom_l_enter=1.45, zoom_l_exit=1.30, zoom_index_min=0.0),
                        zoom_min=0.5, zoom_max=3.0, zoom_dist_floor=0.25)
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

@pytest.mark.parametrize("lms, pose", [(DRAW_LMS, DRAW), (FIST_LMS, FIST), (PALM_LMS, PALM), (V_LMS, V),
                                       (BUNCH_LMS, ERASE), (PINCH_LMS, ZOOM), (L_LMS, ZOOM)])
def test_detect_pose(lms, pose):
    assert detect_pose(lms).pose == pose


@pytest.mark.parametrize("tw, pose", [(1.10, FIST), (1.17, FIST), (1.19, NONE), (1.21, ERASE), (1.35, ERASE)])
def test_fist_and_bunch_boundary(tw, pose):
    lms = bunch(tw)
    assert tip_wrist(lms) == pytest.approx(tw, abs=1e-6)
    assert detect_pose(lms).pose == pose


def test_bunch_hysteresis_while_erasing():
    # 지우는 중에는 1.15까지 지우개 유지, 그 아래는 아님
    assert detect_pose(bunch(1.16), erasing=True).pose == ERASE
    assert detect_pose(bunch(1.16), erasing=False).pose == FIST
    assert detect_pose(bunch(1.14), erasing=True).pose == FIST


def test_erase_point_is_mean_of_five_tips():
    det = detect_pose(BUNCH_LMS)
    ids = (4, 8, 12, 16, 20)
    assert det.point.x == pytest.approx(sum(BUNCH_LMS[i].x for i in ids) / 5)
    assert det.point.y == pytest.approx(sum(BUNCH_LMS[i].y for i in ids) / 5)


def test_draw_vs_l_shape_by_thumb_spread():
    assert thumb_spread(DRAW_LMS) < 1.0
    assert detect_pose(l_shape(1.40)).pose == DRAW  # 진입값(1.45) 아래
    assert detect_pose(l_shape(1.50)).pose == ZOOM
    # L자가 이어지는 중에는 이탈값(1.30)까지 L자
    assert detect_pose(l_shape(1.35), zoom_cand=True).pose == ZOOM


def test_flip_fix_generalized_only_when_no_pose():
    sign = chirality(V_LMS)
    det = detect_pose(mirrored(V_LMS), normal_sign=sign)  # 원래 번호로는 "--RP" → 아무 포즈 아님
    assert (det.pose, det.flip_fixed) == (V, True)
    det = detect_pose(mirrored(FIST_LMS), normal_sign=chirality(FIST_LMS))  # 주먹은 뒤집어도 주먹
    assert (det.pose, det.flip_fixed) == (FIST, False)
    assert detect_pose(mirrored(V_LMS), normal_sign=sign, flip_fix=False).pose == NONE


# --- 지우개 ---------------------------------------------------------------------

def test_bunch_erases_start_move_end():
    r = Runner()
    res = r.feed(BUNCH_LMS, 4) + r.feed(None, 5)
    assert gestures(res) == ["IDLE", "IDLE", "ERASE_START", "ERASE_MOVE"] + ["ERASE_MOVE"] * 4 + ["ERASE_END"]
    assert res[2].params == {"eraser_radius": round(30 / 1440, 5)}
    assert len({x.stroke_id for x in res[2:]}) == 1


def test_ambiguous_band_does_nothing():
    """주먹(1.18 미만)과 모으기(1.20 초과) 사이에서는 아무 동작도 하지 않는다 (UNDO도 없음)."""
    r = Runner()
    res = r.feed(bunch(1.19), 60)
    assert set(gestures(res)) == {"IDLE"}
    assert not any(x.params for x in res)


def test_erase_does_not_flicker_near_exit():
    r = Runner()
    r.feed(BUNCH_LMS, 3)
    res = []
    for tw in [1.18, 1.16, 1.19, 1.17, 1.16, 1.21, 1.18]:
        res += r.feed(bunch(tw))
    assert set(gestures(res)) == {"ERASE_MOVE"}


def test_switch_draw_to_erase_ends_draw_first():
    r = Runner()
    g = gestures(r.feed(DRAW_LMS, 4) + r.feed(BUNCH_LMS, 7))
    end, start = g.index("DRAW_END"), g.index("ERASE_START")
    assert end < start
    assert "DRAW_MOVE" not in g[start:] and "ERASE_MOVE" not in g[:end]


# --- 줌 -------------------------------------------------------------------------

def zoom_params(results, kind):
    return [x.params for x in results if x.gesture == kind]


def test_zoom_in_from_pinch():
    r = Runner()
    res = r.feed(PINCH_LMS, 4)
    start = [x for x in res if x.gesture == "ZOOM_START"][0]
    mid = ((PINCH_LMS[4].x + PINCH_LMS[8].x) / 2, (PINCH_LMS[4].y + PINCH_LMS[8].y) / 2)
    assert start.params["zoom_scale"] == 1.0
    assert (start.params["zoom_center"]["x"], start.params["zoom_center"]["y"]) == pytest.approx(mid)
    assert start.stroke_id is None
    # 엄지를 점점 벌린다 → 확대, 최대 3배
    for ratio in [0.3, 0.5, 0.8, 1.2]:
        res += r.feed(pinch_zoom(ratio), 6)
    scales = [p["zoom_scale"] for p in zoom_params(res, "ZOOM_UPDATE")]
    assert scales[-1] > 2.5 and max(scales) <= 3.0
    assert all(p["zoom_center"] == start.params["zoom_center"] for p in zoom_params(res, "ZOOM_UPDATE"))


def test_zoom_out_from_l_and_scale_is_kept_per_session():
    r = Runner()
    res = r.feed(L_LMS, 4)
    assert "ZOOM_START" in gestures(res)
    for ratio in [1.2, 0.9, 0.6, 0.5]:  # 엄지를 검지 끝 쪽으로 모은다 → 축소
        res += r.feed(pinch_zoom(ratio), 8)
    end = r.feed(None, 5)[-1]
    assert end.gesture == "ZOOM_END"
    assert 0.5 <= end.params["zoom_scale"] < 0.8
    # 다음 줌은 저장된 배율에서 시작
    start = [x for x in r.feed(L_LMS, 4) if x.gesture == "ZOOM_START"][0]
    assert start.params["zoom_scale"] == pytest.approx(end.params["zoom_scale"])


def test_other_pose_ends_zoom():
    r = Runner()
    g = gestures(r.feed(PINCH_LMS, 6) + r.feed(PALM_LMS, 5))
    assert g[-1] == "ZOOM_END"


def test_drawing_with_thumb_moving_does_not_zoom():
    """그리는 중 엄지가 진입값 아래에서 움직이거나, 잠깐(M프레임 미만) 넘어도 줌이 되지 않는다."""
    r = Runner()
    res = r.feed(DRAW_LMS, 4)
    for spread in [0.8, 1.1, 1.3, 1.40, 1.2, 1.38, 0.9]:
        res += r.feed(l_shape(spread), 3)
    res += r.feed(l_shape(1.55), 2) + r.feed(DRAW_LMS, 5)  # 2프레임만 L자
    g = gestures(res)
    assert not any(x.startswith("ZOOM") for x in g)
    assert g.count("DRAW_START") == 1 and "DRAW_END" not in g


# --- 일회성 명령 ----------------------------------------------------------------

def test_fist_hold_undo_once_until_pose_changes():
    r = Runner()
    res = r.feed(FIST_LMS, 60)
    assert gestures(res).count("UNDO") == 1
    assert gestures(res).index("UNDO") * FRAME_MS >= 700 - FRAME_MS
    r.feed(None, 3)
    assert gestures(r.feed(FIST_LMS, 25)).count("UNDO") == 1


def test_cooldown_blocks_quick_second_command():
    r = Runner()
    assert gestures(r.feed(FIST_LMS, 24)).count("UNDO") == 1
    assert not any(x.params.get("color") for x in r.feed(V_LMS, 17))
    later = r.feed(V_LMS, 15)
    assert [x.params["color"] for x in later if "color" in x.params] == [COLORS[1]]


def test_v_cycles_pen_color_and_next_stroke_uses_it():
    r = Runner()
    res = r.feed(V_LMS, 20)
    assert [x.params["color"] for x in res if "color" in x.params] == [COLORS[1]]
    start = [x for x in r.feed(DRAW_LMS, 4) if x.gesture == "DRAW_START"][0]
    assert start.params == {"color": COLORS[1], "brush_size": 8}


def test_palm_cycles_size_of_last_used_tool():
    r = Runner()
    assert [x.params for x in r.feed(PALM_LMS, 20) if x.params] == [{"color": COLORS[0], "brush_size": 16}]
    r.feed(None, 6)
    r.feed(BUNCH_LMS, 4)
    r.feed(None, 6)
    assert [x.params for x in r.feed(PALM_LMS, 40) if x.params] == [{"eraser_radius": round(50 / 1440, 5)}]


def test_brief_fist_while_drawing_does_not_undo():
    r = Runner()
    g = gestures(r.feed(DRAW_LMS, 10) + r.feed(FIST_LMS, 5 + 18) + r.feed(None, 10))
    assert "DRAW_END" in g and "UNDO" not in g


def test_bunch_then_fist_undoes_once():
    """지우개(손끝 모으기)에서 바로 주먹으로 바꿔 700ms 이상 유지하면 UNDO 1번."""
    r = Runner()
    g = gestures(r.feed(BUNCH_LMS, 10) + r.feed(FIST_LMS, 5 + 30))
    assert g.index("ERASE_END") < g.index("UNDO")
    assert g.count("UNDO") == 1


# --- 출력 형식 ------------------------------------------------------------------

def test_new_messages_pass_schema(gesture_validator):
    r = Runner()
    results = (r.feed(V_LMS, 20) + r.feed(None, 3) + r.feed(PALM_LMS, 40) + r.feed(None, 3)
               + r.feed(DRAW_LMS, 4) + r.feed(BUNCH_LMS, 10) + r.feed(None, 6) + r.feed(PINCH_LMS, 4)
               + r.feed(pinch_zoom(0.8), 6) + r.feed(None, 6) + r.feed(FIST_LMS, 40))
    kinds = {x.gesture for x in results}
    assert {"ERASE_START", "ERASE_END", "DRAW_START", "ZOOM_START", "ZOOM_UPDATE", "ZOOM_END", "UNDO"} <= kinds
    ids = {"trace_id": "trace-0001", "session_id": "s-1", "frame_id": 1}
    for x in results:
        gesture_validator.validate(build_gesture_message(ids, x, 0.9, 0, 0.1))


def test_processor_keeps_tool_settings_after_gap():
    t = [100.0]
    proc = GestureProcessor(clock=lambda: t[0])
    s = proc._sessions.setdefault("s", _Session(GestureStateMachine(colors=COLORS)))
    s.machine._color_i = 2
    s.machine.zoom_scale = 2.0
    s.ids = {"trace_id": "trace-0001", "session_id": "s", "frame_id": 1}
    t[0] += 1.0
    proc.expire()
    assert proc._sessions["s"].machine.color == COLORS[2]
    assert proc._sessions["s"].machine.zoom_scale == 2.0
