"""LandmarkMessage → GestureMessage 처리. HTTP, MQTT, WebSocket이 같은 객체를 함께 쓴다.

HTTP 요청(스레드풀), MQTT 수신(paho 스레드), WebSocket(이벤트 루프), 끊김 감시(별도 스레드)가
동시에 들어오므로 세션 상태는 lock 안에서만 바꾼다.
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config
from .adapter import MessageError, decode, to_landmark_message
from .logs import log_json
from .models import Hand, LandmarkMessage
from .state_machine import GestureStateMachine, StepResult


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _Session:
    machine: GestureStateMachine
    last_seen: float = 0.0  # 마지막 수신 시각 (time.monotonic, 초)
    ids: dict = field(default_factory=dict)  # 마지막 메시지의 trace_id/session_id/frame_id
    confidence: float = 1.0
    hand_index: int | None = None
    last_lag_warn: float = float("-inf")


def _pick_hand(msg: LandmarkMessage) -> tuple[Hand | None, int | None]:
    """손은 1개만 쓴다. 여러 개면 confidence가 가장 높은 손."""
    if not msg.hands:
        return None, None
    i, hand = max(enumerate(msg.hands), key=lambda pair: pair[1].confidence)
    return hand, hand.hand_index if hand.hand_index is not None else i


def build_gesture_message(ids: dict, result: StepResult, confidence: float,
                          hand_index: int | None, gesture_ms: float) -> dict:
    params = {"state_frames": result.state_frames}
    if result.stroke_id is not None:
        params["stroke_id"] = result.stroke_id
    pointer = None if result.pointer is None else {"x": result.pointer[0], "y": result.pointer[1]}
    return {
        "schema_version": config.SCHEMA_VERSION,
        "message_type": "gesture",
        "trace_id": ids["trace_id"],
        "session_id": ids["session_id"],
        "frame_id": ids["frame_id"],
        "timestamp_ms": now_ms(),
        "gesture": result.gesture,
        "pointer": pointer,
        "confidence": confidence,
        "hand_index": hand_index,
        "params": params,
        "gesture_ms": gesture_ms,
    }


class GestureProcessor:
    def __init__(
        self,
        session_gap_ms: int = config.SESSION_GAP_MS,
        lag_warn_ms: int = config.LAG_WARN_MS,
        machine_factory: Callable[[], GestureStateMachine] = GestureStateMachine,
        clock: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = now_ms,
    ):
        self.session_gap_ms = session_gap_ms
        self.lag_warn_ms = lag_warn_ms
        self._machine_factory = machine_factory
        self._clock = clock
        self._wall_ms = wall_ms
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def process(self, msg: LandmarkMessage, source: str = "http") -> dict:
        """프레임 하나를 처리해 GestureMessage(dict)를 돌려준다."""
        started = time.perf_counter()
        recv_ms = self._wall_ms()
        lag_ms = recv_ms - msg.timestamp_ms
        hand, hand_index = _pick_hand(msg)
        confidence = hand.confidence if hand else 1.0
        ids = {"trace_id": msg.trace_id, "session_id": msg.session_id, "frame_id": msg.frame_id}

        with self._lock:
            s = self._sessions.get(msg.session_id)
            if s is None:
                s = self._sessions[msg.session_id] = _Session(self._machine_factory())
            result = s.machine.step(hand.landmarks if hand else None, msg.timestamp_ms, recv_ms)
            s.last_seen = self._clock()
            s.ids = ids
            s.confidence = confidence
            s.hand_index = hand_index
            warn_lag = lag_ms > self.lag_warn_ms and s.last_seen - s.last_lag_warn >= 1.0
            if warn_lag:
                s.last_lag_warn = s.last_seen

        gesture_ms = round((time.perf_counter() - started) * 1000, 3)
        out = build_gesture_message(ids, result, confidence, hand_index, gesture_ms)
        if warn_lag:  # 같은 세션에서 1초에 한 번만
            log_json(logging.WARNING, event="lag", trace_id=msg.trace_id, session_id=msg.session_id,
                     frame_id=msg.frame_id, lag_ms=lag_ms, threshold_ms=self.lag_warn_ms)
        log_json(trace_id=msg.trace_id, frame_id=msg.frame_id, session_id=msg.session_id, source=source,
                 gesture=result.gesture, state_frames=result.state_frames, gesture_ms=gesture_ms, lag_ms=lag_ms)
        return out

    def process_payload(self, payload: bytes | str | dict[str, Any], source: str, origin: str = "") -> dict | None:
        """MQTT/WebSocket으로 받은 원본 메시지 처리. 잘못된 메시지는 에러 로그만 남기고 None."""
        try:
            data = payload if isinstance(payload, dict) else decode(payload)
            msg = to_landmark_message(data)
        except MessageError as exc:
            log_json(logging.ERROR, event="invalid_message", source=source, origin=origin,
                     code=exc.code, error=str(exc))
            return None
        return self.process(msg, source=source)

    def _end_message(self, s: _Session, result: StepResult, source: str) -> dict:
        """강제로 만든 DRAW_END. 마지막으로 받은 메시지의 id와 마지막 그리기 좌표를 쓴다."""
        log_json(trace_id=s.ids["trace_id"], frame_id=s.ids["frame_id"], session_id=s.ids["session_id"],
                 source=source, gesture=result.gesture, state_frames=result.state_frames, gesture_ms=0.0)
        return build_gesture_message(s.ids, result, s.confidence, s.hand_index, 0.0)

    def end_session(self, session_id: str, source: str) -> dict | None:
        """그 세션이 그리는 중이면 DRAW_END를 만든다 (WebSocket 연결이 끊겼을 때).
        force_end()가 lock 안에서 상태를 IDLE로 바꾸므로 gap_watch와 겹쳐도 END는 한 번만 나온다."""
        with self._lock:
            s = self._sessions.get(session_id)
            result = s.machine.force_end() if s else None
        return None if result is None else self._end_message(s, result, source)

    def expire(self) -> list[dict]:
        """SESSION_GAP_MS 동안 메시지가 없던 세션을 정리한다.
        그리는 중이었다면 DRAW_END를 만들어 돌려준다 (발행은 호출한 쪽이 한다)."""
        now = self._clock()
        ended: list[tuple[_Session, StepResult]] = []
        with self._lock:
            for sid, s in list(self._sessions.items()):
                if (now - s.last_seen) * 1000 <= self.session_gap_ms:
                    continue
                result = s.machine.force_end()
                if result is not None:
                    ended.append((s, result))
                # 세션을 지워도 된다: 다음 메시지가 오면 새로 만들고, stroke_id는 프로세스 전체에서 겹치지 않는다.
                del self._sessions[sid]
        return [self._end_message(s, result, "gap_watch") for s, result in ended]

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
