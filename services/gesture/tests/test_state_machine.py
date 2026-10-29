import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.state_machine import GestureStateMachine
from tests.conftest import load_landmarks

DRAW = load_landmarks("index_pose")
FIST = load_landmarks("fist")
NO_HAND = None
FRAME_MS = 33


def shifted(lms, dx=0.0, dy=0.0):
    """손 전체를 (dx, dy)만큼 옮긴 복사본."""
    return [SimpleNamespace(x=p.x + dx, y=p.y + dy) for p in lms]


@pytest.fixture
def machine():
    # 환경변수와 상관없이 기본값으로 고정
    return GestureStateMachine(enter_frames=3, exit_frames=3, enter_ms=0, exit_ms=0, session_gap_ms=500)


class Runner:
    """프레임을 차례로 넣는 도우미. 시간은 기본 33ms씩 증가."""

    def __init__(self, machine):
        self.m = machine
        self.ts = 1_000_000

    def feed(self, lms, gap_ms=FRAME_MS):
        self.ts += gap_ms
        return self.m.step(lms, self.ts, recv_ms=self.ts)

    def feed_many(self, frames):
        return [self.feed(f) for f in frames]


def gestures(results):
    return [r.gesture for r in results]


def test_draw_start_after_n_frames(machine):
    results = Runner(machine).feed_many([DRAW] * 3)
    assert gestures(results) == ["IDLE", "IDLE", "DRAW_START"]
    assert results[2].stroke_id is not None


def test_idle_pointer_follows_index_tip_and_is_null_without_hand(machine):
    r = Runner(machine)
    assert r.feed(DRAW).pointer == pytest.approx((DRAW[8].x, DRAW[8].y))
    assert r.feed(NO_HAND).pointer is None


def test_draw_move_keeps_same_stroke_id(machine):
    r = Runner(machine)
    start = r.feed_many([DRAW] * 3)[-1]
    moves = [r.feed(shifted(DRAW, dx=0.01 * i)) for i in range(1, 6)]
    assert gestures(moves) == ["DRAW_MOVE"] * 5
    assert {m.stroke_id for m in moves} == {start.stroke_id}


def test_single_glitch_frame_does_not_end_stroke(machine):
    r = Runner(machine)
    r.feed_many([DRAW] * 3)
    results = r.feed_many([DRAW, FIST, DRAW, NO_HAND, DRAW])
    assert "DRAW_END" not in gestures(results)
    assert gestures(results) == ["DRAW_MOVE"] * 5


@pytest.mark.parametrize("lost", [FIST, NO_HAND], ids=["fist", "no_hand"])
def test_pose_lost_m_frames_ends_stroke_then_idle(machine, lost):
    r = Runner(machine)
    start = r.feed_many([DRAW] * 3)[-1]
    results = r.feed_many([lost] * 3)
    assert gestures(results) == ["DRAW_MOVE", "DRAW_MOVE", "DRAW_END"]
    assert results[-1].stroke_id == start.stroke_id
    after = r.feed(lost)
    assert after.gesture == "IDLE"
    assert after.stroke_id is None
    if lost is NO_HAND:
        assert after.pointer is None


@pytest.mark.parametrize("lost", [FIST, NO_HAND], ids=["fist", "no_hand"])
def test_pointer_is_frozen_while_leaving(machine, lost):
    """이탈 대기 중 DRAW_MOVE와 DRAW_END의 pointer는 마지막 그리기 좌표 (획 끝 꼬리 방지)."""
    r = Runner(machine)
    r.feed_many([DRAW] * 3)
    last_draw = r.feed(shifted(DRAW, dx=0.05)).pointer
    results = r.feed_many([lost] * 3)
    assert [x.pointer for x in results] == [last_draw] * 3
    if lost is FIST:
        # 주먹의 검지 끝 좌표는 실제로 다르다 → 고정이 아니었다면 값이 달랐을 것
        assert (FIST[8].x, FIST[8].y) != pytest.approx(last_draw)


def test_each_new_stroke_gets_new_stroke_id(machine):
    r = Runner(machine)
    first = r.feed_many([DRAW] * 3)[-1].stroke_id
    r.feed_many([FIST] * 3)
    second = r.feed_many([DRAW] * 3)[-1]
    assert second.gesture == "DRAW_START"
    assert second.stroke_id != first


def test_stroke_id_contains_process_run_id(machine):
    from src.state_machine import RUN_ID

    stroke_id = Runner(machine).feed_many([DRAW] * 3)[-1].stroke_id
    assert re.fullmatch(rf"stk-{RUN_ID}-\d{{4,}}", stroke_id)


def test_stroke_id_differs_after_session_recreated():
    """/reset은 세션 상태 머신을 지우고 새로 만든다. 새 머신에서도 id가 겹치면 안 된다."""
    first = Runner(GestureStateMachine()).feed_many([DRAW] * 3)[-1].stroke_id
    second = Runner(GestureStateMachine()).feed_many([DRAW] * 3)[-1].stroke_id
    assert first != second


def _stroke_id_from_new_process() -> str:
    code = (
        "from src.state_machine import GestureStateMachine;"
        "from tests.conftest import load_landmarks;"
        "m = GestureStateMachine(enter_frames=1, enter_ms=0);"
        "print(m.step(load_landmarks('index_pose'), 1, 1).stroke_id)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_stroke_id_differs_across_processes():
    """C가 재시작돼도(새 프로세스) 첫 획의 id가 이전 프로세스와 겹치지 않는다."""
    a, b = _stroke_id_from_new_process(), _stroke_id_from_new_process()
    assert a.endswith("-0001") and b.endswith("-0001")
    assert a != b


def test_same_or_reversed_timestamps_do_not_fail(machine):
    ts = 2_000_000
    stamps = [ts, ts, ts - 50, ts - 100, ts - 100, ts + 10]
    results = []
    for i, stamp in enumerate(stamps):
        lms = shifted(DRAW, dx=0.01 * i)
        results.append(machine.step(lms, stamp, recv_ms=ts + i * FRAME_MS))
    assert gestures(results) == ["IDLE", "IDLE", "DRAW_START", "DRAW_MOVE", "DRAW_MOVE", "DRAW_MOVE"]
    for res in results:
        assert all(0.0 <= v <= 1.0 for v in res.pointer)


def test_gap_while_drawing_ends_stroke_and_resets(machine):
    r = Runner(machine)
    start = r.feed_many([DRAW] * 3)[-1]
    last_draw = r.feed(DRAW).pointer
    ended = r.feed(DRAW, gap_ms=600)
    assert ended.gesture == "DRAW_END"
    assert ended.stroke_id == start.stroke_id
    assert ended.pointer == last_draw
    # 초기화됐으므로 다시 N프레임을 채워야 DRAW_START
    assert gestures(r.feed_many([DRAW] * 3)) == ["IDLE", "IDLE", "DRAW_START"]


def test_gap_while_idle_resets_enter_count(machine):
    r = Runner(machine)
    r.feed_many([DRAW] * 2)
    assert r.feed(DRAW, gap_ms=600).gesture == "IDLE"  # 카운트가 1부터 다시 시작
    assert gestures(r.feed_many([DRAW] * 2)) == ["IDLE", "DRAW_START"]


def test_pointer_is_clamped_to_0_1(machine):
    r = Runner(machine)
    results = r.feed_many([shifted(DRAW, dx=0.7, dy=-0.5)] * 4)
    for res in results:
        assert all(0.0 <= v <= 1.0 for v in res.pointer)


def test_force_end_only_when_drawing(machine):
    r = Runner(machine)
    assert machine.force_end() is None
    r.feed_many([DRAW] * 3)
    ended = machine.force_end()
    assert ended.gesture == "DRAW_END"
    assert r.feed(DRAW).gesture == "IDLE"
