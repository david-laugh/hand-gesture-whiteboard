"""세션 하나의 제스처 상태 머신 (순수 Python, FastAPI와 무관).

상태: IDLE, DRAWING, ERASING, ZOOMING (동시에 하나만)
- 검지만 N프레임         → DRAW_START  … 아닌 프레임이 M프레임 → DRAW_END
- 다섯 손끝 모으기 N프레임 → ERASE_START … 아닌 프레임이 M프레임 → ERASE_END
- 엄지·검지(집게/L자) N프레임 → ZOOM_START … ZOOM_UPDATE … 아닌 프레임이 M프레임 → ZOOM_END
- 다른 도구로 바꾸면 이전 도구의 *_END가 먼저 나가고, 그다음 새 도구의 *_START가 나간다.

줌: zoom_scale = 시작 배율 × (현재 dist(4,8) ÷ 시작 dist(4,8)), ZOOM_SCALE_MIN~MAX.
    zoom_center = 시작할 때 엄지·검지 중간점. 끝나면 그 배율이 세션의 현재 배율이 된다.

일회성 명령 (IDLE에서만): 자세를 COMMAND_HOLD_MS(주먹은 UNDO_HOLD_MS) 유지하면 1번 발행
- 주먹 → UNDO
- 브이 → 다음 펜 색 (이후 DRAW_START의 params.color)
- 손바닥 → 마지막으로 쓴 도구의 굵기 순환 (펜 brush_size / 지우개 eraser_radius)
같은 자세를 계속 유지하면 다시 나가지 않는다 (자세를 바꿔야 재발행). 명령 사이에는 쿨다운이 있다.
획이 끝나면 유지 시간을 그때부터 새로 잰다 → 그리다가 주먹으로 잠깐 접으면 UNDO가 나가지 않고,
획이 끝난 뒤 주먹을 UNDO_HOLD_MS 이상 유지하면 UNDO가 나간다.
"""
import itertools
import secrets
import string
import threading
from dataclasses import dataclass, field
from typing import Sequence

from . import config
from .hand_pose import DRAW, ERASE, FIST, NONE, PALM, V, ZOOM, Point, Thresholds, detect_pose
from .one_euro import OneEuroFilter, PointFilter

IDLE = "IDLE"
DRAWING = "DRAWING"
ERASING = "ERASING"
ZOOMING = "ZOOMING"
_TOOL_STATE = {DRAW: DRAWING, ERASE: ERASING, ZOOM: ZOOMING}
_STATE_TOOL = {v: k for k, v in _TOOL_STATE.items()}
_COMMANDS = {FIST: "UNDO", V: "COLOR", PALM: "SIZE"}

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
    gesture: str  # IDLE / DRAW_* / ERASE_* / ZOOM_* / UNDO
    pointer: tuple[float, float] | None  # 0~1 로 자른 좌표
    stroke_id: str | None
    state_frames: int  # 현재 상태를 몇 프레임째 유지 중인지
    flip_fixed: bool = False  # 손가락 번호 뒤집힘 보정으로 나온 포즈
    params: dict = field(default_factory=dict)  # color / brush_size / eraser_radius / zoom_scale / zoom_center


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


def _start_index(sizes: list[float], default: float) -> int:
    return min(range(len(sizes)), key=lambda i: abs(sizes[i] - default))


class GestureStateMachine:
    def __init__(
        self,
        enter_frames: int = config.DRAW_ENTER_FRAMES,
        exit_frames: int = config.DRAW_EXIT_FRAMES,
        enter_ms: int = config.STATE_ENTER_MS,
        exit_ms: int = config.STATE_EXIT_MS,
        session_gap_ms: int = config.SESSION_GAP_MS,
        finger_ratio: float = config.FINGER_EXTENDED_RATIO,
        min_cutoff: float = config.ONE_EURO_MIN_CUTOFF,
        beta: float = config.ONE_EURO_BETA,
        d_cutoff: float = config.ONE_EURO_D_CUTOFF,
        flip_fix: bool = config.FLIP_FIX_ENABLED,
        normal_sign: int = config.FLIP_NORMAL_SIGN,
        thresholds: Thresholds | None = None,
        hold_ms: int = config.COMMAND_HOLD_MS,
        undo_hold_ms: int = config.UNDO_HOLD_MS,
        cooldown_ms: int = config.COMMAND_COOLDOWN_MS,
        colors: list[str] = config.PEN_COLORS,
        brush_sizes: list[float] = config.BRUSH_SIZES_PX,
        brush_default: float = config.BRUSH_DEFAULT_PX,
        eraser_sizes: list[float] = config.ERASER_SIZES_PX,
        eraser_default: float = config.ERASER_DEFAULT_PX,
        canvas_min_side: float = config.CANVAS_MIN_SIDE_PX,
        zoom_min: float = config.ZOOM_SCALE_MIN,
        zoom_max: float = config.ZOOM_SCALE_MAX,
        zoom_dist_floor: float = config.ZOOM_DIST_FLOOR,
        zoom_reset_hold_ms: int = config.ZOOM_RESET_HOLD_MS,
        zoom_reset_still: float = config.ZOOM_RESET_STILL,
    ):
        # 진입/이탈은 "최소 프레임 수"와 "시간(ms)"을 둘 다 채워야 한다. ms를 0으로 주면 프레임 수만 본다.
        self.enter_frames, self.exit_frames = enter_frames, exit_frames
        self.enter_ms, self.exit_ms = enter_ms, exit_ms
        self.zoom_reset_hold_ms, self.zoom_reset_still = zoom_reset_hold_ms, zoom_reset_still
        self.session_gap_ms = session_gap_ms
        self.th = thresholds or Thresholds(ratio=finger_ratio)
        self.flip_fix = flip_fix
        self.normal_sign = normal_sign
        self.hold_ms, self.undo_hold_ms, self.cooldown_ms = hold_ms, undo_hold_ms, cooldown_ms
        self.colors = colors
        self.brush_sizes, self.eraser_sizes = brush_sizes, eraser_sizes
        self.canvas_min_side = canvas_min_side
        self.zoom_min, self.zoom_max, self.zoom_dist_floor = zoom_min, zoom_max, zoom_dist_floor
        self._filter = PointFilter(min_cutoff, beta, d_cutoff)
        self._dist_filter = OneEuroFilter(min_cutoff, beta, d_cutoff)  # 줌 거리 떨림 줄이기
        self._last_ts: int | None = None
        self._last_recv_ms: int | None = None
        self._clock_ms = 0  # 프레임 간격을 더해 만든 세션 내부 시각 (유지 시간·쿨다운용)
        self._dt_now = 0  # 지금 프레임의 간격 (ms). 한 프레임이 차지하는 시간으로 본다
        # 도구 설정과 화면 배율: 세션이 살아 있는 동안 유지 (gap으로 초기화해도 그대로)
        self._color_i = 0
        self._brush_i = _start_index(brush_sizes, brush_default)
        self._eraser_i = _start_index(eraser_sizes, eraser_default)
        self.last_tool = DRAW
        self.zoom_scale = 1.0
        self._last_command_ms: float = float("-inf")
        self._reset_state()

    # --- 도구 설정 ---------------------------------------------------------------

    @property
    def color(self) -> str:
        return self.colors[self._color_i]

    @property
    def brush_size(self) -> float:
        return self.brush_sizes[self._brush_i]

    @property
    def eraser_radius(self) -> float:
        """canvas 계약: 정규화 반지름. canvas는 이 값 × min(가로, 세로) × 2 를 지우개 지름(px)으로 쓴다."""
        return round(self.eraser_sizes[self._eraser_i] / (2 * self.canvas_min_side), 5)

    def _tool_params(self, tool: str) -> dict:
        if tool == DRAW:
            return {"color": self.color, "brush_size": self.brush_size}
        return {"eraser_radius": self.eraser_radius}

    def _zoom_params(self) -> dict:
        return {"zoom_scale": round(self._zoom_now, 4), "zoom_center": dict(self._zoom_center)}

    # --- 상태 ---------------------------------------------------------------------

    def _reset_state(self) -> None:
        self.state = IDLE
        self.state_frames = 0
        self._exit_count = 0  # 도구 사용 중에 그 도구 자세가 아닌 연속 프레임 수와 시작 시각
        self._exit_start_ms = self._clock_ms
        self._cand = NONE  # 지금 연속으로 보이는 자세와 그 프레임 수·시작 시각
        self._cand_frames = 0
        self._cand_start_ms = self._clock_ms
        self._cmd_start_ms = self._clock_ms  # 일회성 명령 유지 시간의 시작 (획이 끝나면 새로 잼)
        self._reset_anchor: float | None = None  # 줌 1배 복귀: 집게 거리 기준값과 유지 시작 시각
        self._reset_start_ms = self._clock_ms
        self._reset_pending = False  # 1배로 돌린 ZOOM_UPDATE를 보냈고, 다음 프레임에 ZOOM_END
        self._latched: str | None = None  # 이 자세로는 명령을 다시 내지 않음 (자세가 바뀌면 풀림)
        self.stroke_id: str | None = None
        self.last_draw_pointer: tuple[float, float] | None = None
        self._zoom_start_dist = 1.0
        self._zoom_base = self.zoom_scale
        self._zoom_now = self.zoom_scale
        self._zoom_center = {"x": 0.5, "y": 0.5}
        self._filter.reset()
        self._dist_filter.reset()

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
        """도구 사용 중이면 끝낸다 (*_END). 아니면 None."""
        if self.state == IDLE:
            return None
        tool = _STATE_TOOL[self.state]
        params = {}
        if tool == ZOOM:
            params = self._zoom_params()
            self.zoom_scale = self._zoom_now  # 세션의 현재 배율로 저장
        result = StepResult(f"{tool}_END", self.last_draw_pointer, self.stroke_id, self.state_frames, params=params)
        self._reset_state()
        return result

    def step(self, landmarks: Sequence[Point] | None, ts_ms: int, recv_ms: int) -> StepResult:
        """프레임 하나 처리. landmarks: 손 하나의 21개 점, 손이 없으면 None."""
        dt_ms = self._dt_ms(ts_ms, recv_ms)
        self._clock_ms += dt_ms or 0
        self._dt_now = dt_ms or 0

        if dt_ms is not None and dt_ms > self.session_gap_ms:
            # 오래 끊겼다가 다시 왔다: 상태 초기화. 진행 중인 도구가 있으면 먼저 끝낸다.
            ended = self.force_end()
            self._reset_state()
            if ended is not None:
                return ended

        dt_s = (dt_ms or 0) / 1000.0
        pose, raw, flip_fixed, zoom_dist = NONE, None, False, 0.0
        if landmarks is not None:
            det = detect_pose(landmarks, self.th, erasing=self.state == ERASING, zooming=self.state == ZOOMING,
                              zoom_cand=self._cand == ZOOM, flip_fix=self.flip_fix, normal_sign=self.normal_sign)
            pose, raw, flip_fixed, zoom_dist = det.pose, (det.point.x, det.point.y), det.flip_fixed, det.zoom_dist

        if pose == self._cand:
            self._cand_frames += 1
        else:
            self._cand, self._cand_frames = pose, 1
            self._cand_start_ms = self._cmd_start_ms = self._clock_ms
        if self._latched is not None and pose != self._latched:
            self._latched = None

        if self.state == IDLE:
            result = self._step_idle(pose, raw, dt_s, zoom_dist)
        else:
            result = self._step_active(pose, raw, dt_s, zoom_dist)
        result.flip_fixed = flip_fixed
        return result

    def _covered_ms(self, start_ms: float) -> float:
        """start_ms 프레임부터 지금 프레임까지 차지한 시간 (지금 프레임의 간격 포함)."""
        return self._clock_ms - start_ms + self._dt_now

    def _held(self, frames: int, start_ms: float, need_frames: int, need_ms: int) -> bool:
        # 5ms는 ts 반올림 오차 허용 (30fps 3프레임 = 99~100ms)
        return frames >= need_frames and self._covered_ms(start_ms) >= need_ms - 5

    def _filtered(self, raw: tuple[float, float], dt_s: float) -> tuple[float, float]:
        x, y = self._filter(raw[0], raw[1], dt_s)
        return _clamp01(x), _clamp01(y)

    def _update_zoom(self, zoom_dist: float, dt_s: float) -> None:
        d = max(self._dist_filter(zoom_dist, dt_s), self.zoom_dist_floor)
        scale = self._zoom_base * d / self._zoom_start_dist
        self._zoom_now = min(self.zoom_max, max(self.zoom_min, scale))

    def _zoom_reset_due(self, zoom_dist: float) -> bool:
        """집게(거리 < ZOOM_PINCH_RATIO)를 벌리지 않고(거리 변화 ≤ ZOOM_RESET_STILL) ZOOM_RESET_HOLD_MS 유지했는지."""
        if zoom_dist >= self.th.zoom_pinch:
            self._reset_anchor = None
            return False
        if self._reset_anchor is None or abs(zoom_dist - self._reset_anchor) > self.zoom_reset_still:
            self._reset_anchor, self._reset_start_ms = zoom_dist, self._clock_ms  # 움직였다 → 다시 잼
            return False
        return self._covered_ms(self._reset_start_ms) >= self.zoom_reset_hold_ms

    def _step_idle(self, pose: str, raw, dt_s: float, zoom_dist: float) -> StepResult:
        if (pose in _TOOL_STATE and self._latched != pose
                and self._held(self._cand_frames, self._cand_start_ms, self.enter_frames, self.enter_ms)):
            self.state = _TOOL_STATE[pose]
            self.state_frames = 1
            self._exit_count = 0
            self._filter.reset()  # 새 획·줌은 필터를 새로 시작
            self._dist_filter.reset()
            self.last_draw_pointer = self._filtered(raw, dt_s)
            if pose == ZOOM:
                self._reset_anchor, self._reset_start_ms = None, self._clock_ms
                self._zoom_reset_due(zoom_dist)  # 시작 프레임부터 1배 복귀 유지 시간을 잰다
                self._zoom_start_dist = max(self._dist_filter(zoom_dist, dt_s), self.zoom_dist_floor)
                self._zoom_base = self._zoom_now = self.zoom_scale
                self._zoom_center = {"x": _clamp01(raw[0]), "y": _clamp01(raw[1])}
                return StepResult("ZOOM_START", self.last_draw_pointer, None, self.state_frames,
                                  params=self._zoom_params())
            self.last_tool = pose
            self.stroke_id = new_stroke_id()
            return StepResult(f"{pose}_START", self.last_draw_pointer, self.stroke_id, self.state_frames,
                              params=self._tool_params(pose))

        self.state_frames += 1
        command = self._maybe_command(pose)
        if command is not None:
            return command
        if raw is None:
            self._filter.reset()
            return StepResult(IDLE, None, None, self.state_frames)
        return StepResult(IDLE, self._filtered(raw, dt_s), None, self.state_frames)

    def _maybe_command(self, pose: str) -> StepResult | None:
        """일회성 명령: 유지 시간을 채웠고, 잠겨 있지 않고, 쿨다운이 지났으면 1번 발행."""
        kind = _COMMANDS.get(pose)
        hold = self.undo_hold_ms if kind == "UNDO" else self.hold_ms
        if (kind is None or self._latched == pose
                or self._clock_ms - self._cmd_start_ms < hold
                or self._clock_ms - self._last_command_ms < self.cooldown_ms):
            return None
        self._latched = pose
        self._last_command_ms = self._clock_ms
        if kind == "UNDO":
            return StepResult("UNDO", None, None, self.state_frames)
        if kind == "COLOR":
            self._color_i = (self._color_i + 1) % len(self.colors)
            return StepResult(IDLE, None, None, self.state_frames, params={"color": self.color})
        # SIZE: 마지막으로 쓴 도구(펜/지우개)의 굵기
        if self.last_tool == ERASE:
            self._eraser_i = (self._eraser_i + 1) % len(self.eraser_sizes)
        else:
            self._brush_i = (self._brush_i + 1) % len(self.brush_sizes)
        return StepResult(IDLE, None, None, self.state_frames, params=self._tool_params(self.last_tool))

    def _step_active(self, pose: str, raw, dt_s: float, zoom_dist: float) -> StepResult:
        self.state_frames += 1
        tool = _STATE_TOOL[self.state]
        move = "ZOOM_UPDATE" if tool == ZOOM else f"{tool}_MOVE"

        if self._reset_pending:
            # 직전 프레임에 1배로 돌렸다 → 줌을 끝내고, 집게를 풀 때까지 다시 시작하지 않는다
            ended = self.force_end()
            self._latched = ZOOM
            return ended

        if pose == tool:
            self._exit_count = 0
            self.last_draw_pointer = self._filtered(raw, dt_s)
            if tool == ZOOM:
                self._update_zoom(zoom_dist, dt_s)
                if self._zoom_reset_due(zoom_dist):
                    self._zoom_now = 1.0
                    self._reset_pending = True
                return StepResult(move, self.last_draw_pointer, None, self.state_frames, params=self._zoom_params())
            return StepResult(move, self.last_draw_pointer, self.stroke_id, self.state_frames)

        if self._exit_count == 0:
            self._exit_start_ms = self._clock_ms
        self._exit_count += 1
        if self._held(self._exit_count, self._exit_start_ms, self.exit_frames, self.exit_ms):
            cand = (self._cand, self._cand_frames, self._cand_start_ms)
            ended = self.force_end()
            # 다음 도구로 바로 넘어갈 수 있게 연속 자세 정보는 유지하고,
            # 명령 유지 시간은 끝난 지금부터 새로 잰다 → 그리다 주먹으로 잠깐 접어도 UNDO가 나가지 않는다
            self._cand, self._cand_frames, self._cand_start_ms = cand
            self._cmd_start_ms = self._clock_ms
            return ended
        # 이탈 대기 중: 손 모양이 바뀌는 중이라 끝점이 휘어 있다. 꼬리가 생기지 않게 마지막 좌표로 고정.
        params = self._zoom_params() if tool == ZOOM else {}
        return StepResult(move, self.last_draw_pointer, self.stroke_id, self.state_frames, params=params)
