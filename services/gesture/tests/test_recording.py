"""실제 손등 촬영 녹화(rec01, 30fps)의 핵심 구간으로 하는 회귀 테스트.

원본: dev/recordings/rec01_hand_back.jsonl (커밋하지 않음). 여기 fixture는 그중 세 구간만 잘라낸 것.
dump의 ts는 처리 시각이라, 실시간 30fps 입력처럼 frame_id로 ts를 다시 붙인다.
기준은 일부러 느슨하게 잡았다 (MediaPipe나 필터 값이 조금 바뀌어도 깨지지 않게).
"""
import json
from pathlib import Path

import pytest

from src.adapter import to_landmark_message
from src.hand_pose import INDEX_TIP, classify_draw, is_draw_pose
from src.processor import GestureProcessor
from src.state_machine import GestureStateMachine

REC = Path(__file__).parent / "fixtures" / "rec01"
OLD = dict(enter_frames=3, exit_frames=3, flip_fix=False)  # 녹화 분석 전 설정
NEW = dict(enter_frames=3, exit_frames=5, flip_fix=True, normal_sign=1)  # 현재 기본값


def load(name: str) -> list[dict]:
    msgs = []
    for line in (REC / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        raw["ts"] = 1_800_000_000_000 + int(raw["frame_id"] * 1000 / 30)
        msgs.append(raw)
    return msgs


def run(name: str, **kw):
    m = GestureStateMachine(finger_ratio=0.15, **kw)
    out = []
    for raw in load(name):
        msg = to_landmark_message(raw)
        lms = msg.hands[0].landmarks if msg.hands else None
        out.append((msg.frame_id, lms, m.step(lms, msg.timestamp_ms, msg.timestamp_ms)))
    return out


def pieces(results) -> list[tuple[int, int]]:
    """획 조각 (첫 프레임, 마지막 프레임)."""
    spans: dict[str, list[int]] = {}
    for fid, _, r in results:
        if r.stroke_id:
            spans.setdefault(r.stroke_id, []).append(fid)
    return [(v[0], v[-1]) for v in spans.values()]


def drawn(results, lo=0, hi=10**9) -> int:
    return sum(1 for fid, _, r in results if lo <= fid <= hi and r.gesture != "IDLE")


def test_short_line_is_mostly_drawn():
    """짧은 선(381-415)은 대부분 손가락 번호가 뒤집혀 있었다. 보정 후 80% 이상 그려져야 한다."""
    res = run("short_line_379_419", **NEW)
    assert drawn(res, 381, 415) >= 0.8 * 35
    assert any(r.flip_fixed for _, _, r in res)
    assert drawn(run("short_line_379_419", **OLD), 381, 415) < 0.8 * 35  # 기존 설정에서는 못 그림


def test_circle_has_no_more_pieces_and_joins_first_break():
    """원 2바퀴: 손이 화면 밖으로 나가서 생긴 끊김은 남지만, 조각이 늘지 않고 더 많이 그려진다."""
    old, new = run("circle_143_323", **OLD), run("circle_143_323", **NEW)
    assert len(pieces(new)) <= len(pieces(old))
    assert drawn(new) > drawn(old)
    # 179~182의 4프레임 튐(기존에는 여기서 획이 끊김)을 M=5가 이어 준다
    assert any(a <= 150 and b >= 200 for a, b in pieces(new))
    assert not any(a <= 150 and b >= 200 for a, b in pieces(old))


def test_reenter_is_one_stroke():
    res = run("reenter_586_639", **NEW)
    assert len(pieces(res)) == 1
    assert drawn(res) >= 0.8 * len(res)


def test_flip_fixed_pointer_uses_real_index_tip():
    """보정된 프레임의 pointer는 원래 번호 20번(실제 검지 끝) 쪽이어야 한다. 8번(실제 새끼 끝)이 아니라."""
    checked = 0
    for fid, lms, r in run("short_line_379_419", **NEW):
        if not r.flip_fixed:
            continue
        ok, tip, fixed = classify_draw(lms, 0.15, True, 1)
        assert ok and fixed
        assert (tip.x, tip.y) == (lms[20].x, lms[20].y)
        # 원본 좌표(필터 전)가 8번보다 20번에 훨씬 가깝다
        d20 = abs(tip.x - lms[20].x) + abs(tip.y - lms[20].y)
        d8 = abs(tip.x - lms[INDEX_TIP].x) + abs(tip.y - lms[INDEX_TIP].y)
        assert d20 < d8
        checked += 1
    assert checked >= 10


@pytest.mark.parametrize("name", ["circle_143_323", "short_line_379_419", "reenter_586_639"])
def test_flip_fix_disabled_matches_plain_pose(name):
    """FLIP_FIX_ENABLED=false면 기존 판별(is_draw_pose + 8번 끝)과 프레임마다 같다."""
    for raw in load(name):
        msg = to_landmark_message(raw)
        if not msg.hands:
            continue
        lms = msg.hands[0].landmarks
        ok, tip, fixed = classify_draw(lms, 0.15, False, 1)
        assert (ok, fixed) == (is_draw_pose(lms, 0.15), False)
        assert tip is lms[INDEX_TIP]
    res = run(name, enter_frames=3, exit_frames=5, flip_fix=False)
    assert not any(r.flip_fixed for _, _, r in res)


@pytest.mark.parametrize("name", ["circle_143_323", "short_line_379_419", "reenter_586_639"])
def test_recording_outputs_pass_schema(name, gesture_validator):
    proc = GestureProcessor()
    for raw in load(name):
        gesture_validator.validate(proc.process(to_landmark_message(raw)))
