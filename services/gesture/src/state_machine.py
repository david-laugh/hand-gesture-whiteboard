"""세션 하나의 제스처 상태 머신 (순수 Python, FastAPI와 무관).

상태는 IDLE ↔ DRAWING 두 개뿐이다.
나중에 ERASING / ZOOMING을 추가할 때는 상태와 포즈 판별만 늘리면 된다.
"""
import itertools
import secrets
import string
import threading
from dataclasses import dataclass
from typing import Sequence

from . import config
from .hand_pose import INDEX_TIP, Point, is_draw_pose
from .one_euro import PointFilter

IDLE = "IDLE"
DRAWING = "DRAWING"

# 프로세스가 시작될 때 한 번 만드는 짧은 고유값. 재시작(--reload) 후에도 stroke_id가 겹치지 않게 한다.
RUN_ID = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(5))

# stroke 번호는 프로세스 전체에서 하나씩 증가한다. /reset 후에도 겹치지 않게.
_stroke_counter = itertools.count(1)
_stroke_lock = threading.Lock()


def new_stroke_id() -> str:
    with _stroke_lock:
        n = next(_stroke_counter)
    return f"stk-{RUN_ID}-{n:04d}"


@dataclass
class StepResult:
    gesture: str  # IDLE / DRAW_START / DRAW_MOVE / DRAW_END
    pointer: tuple[float, float] | None  # 0~1 로 자른 좌표
    stroke_id: str | None
    state_frames: int  # 현재 상태를 몇 프레임째 유지 중인지


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


class GestureStateMachine:
    def __init__(
        self,
        enter_frames: int = config.DRAW_ENTER_FRAMES,
        exit_frames: int = config.DRAW_EXIT_FRAMES,
        session_gap_ms: int = config.SESSION_GAP_MS,
        finger_ratio: float = config.FINGER_EXTENDED_RATIO,
        min_cutoff: float = config.ONE_EURO_MIN_CUTOFF,
        beta: float = config.ONE_EURO_BETA,
        d_cutoff: float = config.ONE_EURO_D_CUTOFF,
    ):
        self.enter_frames = enter_frames
        self.exit_frames = exit_frames
        self.session_gap_ms = session_gap_ms
        self.finger_ratio = finger_ratio
        self._filter = PointFilter(min_cutoff, beta, d_cutoff)
        self._last_ts: int | None = None
        self._last_recv_ms: int | None = None
        self._reset_state()

    def _reset_state(self) -> None:
        self.state = IDLE
        self.state_frames = 0
        self._enter_count = 0  # IDLE에서 그리기 자세 연속 프레임 수
        self._exit_count = 0  # DRAWING에서 그리기 자세 아닌 연속 프레임 수
        self.stroke_id: str | None = None
        self.last_draw_pointer: tuple[float, float] | None = None
        self._filter.reset()

    def _dt_ms(self, ts_ms: int, recv_ms: int) -> int | None:
        """이전 프레임과의 간격(ms). 메시지 시각이 같거나 거꾸로 가면 서버 수신 시각으로 대신 잰다."""
        dt = None
        if self._last_ts is not None:
            dt = ts_ms - self._last_ts
            if dt <= 0:
                dt = max(1, recv_ms - self._last_recv_ms)
        self._last_ts = ts_ms
        self._last_recv_ms = recv_ms
        return dt

    def force_end(self) -> StepResult | None:
        """그리는 중이면 지금 획을 닫는다 (DRAW_END). 그리는 중이 아니면 None."""
        if self.state != DRAWING:
            return None
        result = StepResult("DRAW_END", self.last_draw_pointer, self.stroke_id, self.state_frames)
        self._reset_state()
        return result

    def step(
        self,
        landmarks: Sequence[Point] | None,
        ts_ms: int,
        recv_ms: int,
    ) -> StepResult:
        """프레임 하나 처리. landmarks: 손 하나의 21개 점, 손이 없으면 None."""
        dt_ms = self._dt_ms(ts_ms, recv_ms)

        if dt_ms is not None and dt_ms > self.session_gap_ms:
            # 오래 끊겼다가 다시 왔다: 상태 초기화. 그리던 획이 있으면 먼저 닫는다.
            ended = self.force_end()
            self._reset_state()
            if ended is not None:
                return ended

        dt_s = (dt_ms or 0) / 1000.0
        pose = landmarks is not None and is_draw_pose(landmarks, self.finger_ratio)
        raw = None
        if landmarks is not None:
            tip = landmarks[INDEX_TIP]
            raw = (tip.x, tip.y)

        if self.state == IDLE:
            return self._step_idle(pose, raw, dt_s)
        return self._step_drawing(pose, raw, dt_s)

    def _filtered(self, raw: tuple[float, float], dt_s: float) -> tuple[float, float]:
        x, y = self._filter(raw[0], raw[1], dt_s)
        return _clamp01(x), _clamp01(y)

    def _step_idle(self, pose: bool, raw, dt_s: float) -> StepResult:
        self._enter_count = self._enter_count + 1 if pose else 0

        if self._enter_count >= self.enter_frames:
            self.state = DRAWING
            self.state_frames = 1
            self._exit_count = 0
            self.stroke_id = new_stroke_id()
            self._filter.reset()  # 새 획은 필터를 새로 시작
            self.last_draw_pointer = self._filtered(raw, dt_s)
            return StepResult("DRAW_START", self.last_draw_pointer, self.stroke_id, self.state_frames)

        self.state_frames += 1
        if raw is None:
            self._filter.reset()
            return StepResult(IDLE, None, None, self.state_frames)
        return StepResult(IDLE, self._filtered(raw, dt_s), None, self.state_frames)

    def _step_drawing(self, pose: bool, raw, dt_s: float) -> StepResult:
        self.state_frames += 1

        if pose:
            self._exit_count = 0
            self.last_draw_pointer = self._filtered(raw, dt_s)
            return StepResult("DRAW_MOVE", self.last_draw_pointer, self.stroke_id, self.state_frames)

        self._exit_count += 1
        if self._exit_count >= self.exit_frames:
            return self.force_end()
        # 이탈 대기 중: 손가락을 접는 중이라 검지 끝이 휘어 있다. 꼬리가 생기지 않게 마지막 그리기 좌표로 고정.
        return StepResult("DRAW_MOVE", self.last_draw_pointer, self.stroke_id, self.state_frames)
