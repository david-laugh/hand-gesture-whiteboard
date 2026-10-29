"""Per-frame latency tracking across gateway → vision → gesture → canvas.

Every service keeps `session_id` and `frame_id`, so messages on the four MQTT topics are
joined by that pair. The dashboard records the time each message *arrives here* with one
clock, so stage latencies never mix clocks:

    capture ─uplink─► gateway recv ─gateway─► frames ─vision─► landmarks ─gesture─► gestures ─canvas─► canvas
     (captured_at_ms)  (received_at_ms)        (arrivals at the dashboard ...........................)

- uplink  = gateway received_at_ms − captured_at_ms (phone clock aligned by the gateway ping;
            error ≈ half the ping round trip)
- gateway = frames arrival − received_at_ms  (gateway queue + MQTT publish + broker)
- vision  = landmarks arrival − frames arrival   (decode + inference + publish, incl. its queue)
- gesture = gestures arrival − landmarks arrival
- canvas  = canvas arrival − gestures arrival
- e2e     = canvas arrival − captured_at_ms

Only frames that start on the frames topic are traced; gestures without a camera frame
(e.g. the canvas mouse UI) are counted as `unmatched` and ignored.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

STAGES = ("uplink", "gateway", "vision", "gesture", "canvas")
KINDS = ("frames", "landmarks", "gestures", "canvas")
OUTCOMES = ("complete", "vision_dropped", "no_gesture", "no_canvas")


def percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize(values: list[float]) -> dict[str, Any]:
    s = sorted(values)
    if not s:
        return {"n": 0, "p50": None, "p95": None, "max": None, "avg": None}
    return {
        "n": len(s),
        "p50": round(percentile(s, 50), 1),
        "p95": round(percentile(s, 95), 1),
        "max": round(s[-1], 1),
        "avg": round(sum(s) / len(s), 1),
    }


@dataclass
class FrameTrace:
    session_id: str
    frame_id: int
    captured_ms: float | None = None
    gw_received_ms: float | None = None
    frames_at: float | None = None
    landmarks_at: float | None = None
    hand: bool | None = None
    gestures_at: float | None = None
    gesture: str | None = None
    gesture_ms: float | None = None
    canvas_at: float | None = None
    canvas_ms: float | None = None
    outcome: str | None = None
    finalized_at: float | None = None

    def stages(self) -> dict[str, float | None]:
        def diff(a, b):
            return None if a is None or b is None else a - b

        return {
            "uplink": diff(self.gw_received_ms, self.captured_ms),
            "gateway": diff(self.frames_at, self.gw_received_ms),
            "vision": diff(self.landmarks_at, self.frames_at),
            "gesture": diff(self.gestures_at, self.landmarks_at),
            "canvas": diff(self.canvas_at, self.gestures_at),
            "e2e": diff(self.canvas_at, self.captured_ms),
            "server": diff(self.canvas_at, self.gw_received_ms),
        }

    def classify(self) -> str:
        if self.canvas_at is not None:
            return "complete"
        if self.landmarks_at is None:
            return "vision_dropped"
        if self.gestures_at is None:
            return "no_gesture"
        return "no_canvas"

    def row(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "frame_id": self.frame_id,
            "outcome": self.outcome,
            "hand": self.hand,
            "gesture": self.gesture,
            "gesture_ms": self.gesture_ms,
            "canvas_ms": self.canvas_ms,
            "captured_ms": self.captured_ms,
            "stages": {k: (None if v is None else round(v, 1)) for k, v in self.stages().items()},
        }


@dataclass
class _Rate:
    window_ms: float = 5000.0
    stamps: deque = field(default_factory=deque)

    def mark(self, now: float) -> None:
        self.stamps.append(now)
        self.trim(now)

    def trim(self, now: float) -> None:
        while self.stamps and now - self.stamps[0] > self.window_ms:
            self.stamps.popleft()

    def rate(self, now: float) -> float:
        self.trim(now)
        if len(self.stamps) < 2:
            return 0.0
        span = self.stamps[-1] - self.stamps[0]
        return round((len(self.stamps) - 1) * 1000.0 / span, 1) if span > 0 else 0.0


class LatencyTracker:
    """Thread-safe: MQTT callbacks ingest, the web loop reads snapshots."""

    def __init__(self, settle_ms: float = 2000.0, keep_s: float = 300.0, max_open: int = 5000) -> None:
        self.settle_ms = settle_ms        # wait this long after the frame for the later stages
        self.keep_ms = keep_s * 1000.0    # finalized traces kept for stats/history
        self.max_open = max_open
        self._lock = threading.Lock()
        self._open: OrderedDict[tuple[str, int], FrameTrace] = OrderedDict()
        self._done: deque[FrameTrace] = deque()
        self._rates = {kind: _Rate() for kind in KINDS}
        self._unmatched = {kind: 0 for kind in KINDS}
        self._received = {kind: 0 for kind in KINDS}
        self._sessions: OrderedDict[str, float] = OrderedDict()  # session → last frames arrival

    # -- ingest (any thread) ------------------------------------------------------------------
    def ingest(self, kind: str, msg: dict, at: float) -> None:
        sid, fid = msg.get("session_id"), msg.get("frame_id")
        if not isinstance(sid, str) or not isinstance(fid, int):
            return
        with self._lock:
            self._received[kind] += 1
            self._rates[kind].mark(at)
            key = (sid, fid)
            if kind == "frames":
                trace = self._open.get(key) or FrameTrace(sid, fid)
                trace.captured_ms = _num(msg.get("captured_at_ms"))
                trace.gw_received_ms = _num(msg.get("received_at_ms"))
                trace.frames_at = at
                self._open[key] = trace
                self._sessions[sid] = at
                self._sessions.move_to_end(sid)
                while len(self._sessions) > 50:
                    self._sessions.popitem(last=False)
                while len(self._open) > self.max_open:
                    self._finalize(self._open.popitem(last=False)[1], at)
                return
            trace = self._open.get(key)
            if trace is None:
                self._unmatched[kind] += 1  # dashboard started mid-frame, or a non-camera gesture
                return
            if kind == "landmarks":
                trace.landmarks_at = at
                trace.hand = bool(msg.get("hands"))
            elif kind == "gestures":
                trace.gestures_at = at
                trace.gesture = msg.get("gesture")
                trace.gesture_ms = _num(msg.get("gesture_ms"))
            elif kind == "canvas":
                trace.canvas_at = at
                trace.canvas_ms = _num(msg.get("canvas_ms"))
                del self._open[key]
                self._finalize(trace, at)

    def _finalize(self, trace: FrameTrace, now: float) -> None:
        trace.outcome = trace.classify()
        trace.finalized_at = now
        self._done.append(trace)

    def _expire(self, now: float) -> None:
        for key in [k for k, t in self._open.items() if now - (t.frames_at or now) > self.settle_ms]:
            self._finalize(self._open.pop(key), now)
        while self._done and now - self._done[0].finalized_at > self.keep_ms:
            self._done.popleft()

    # -- read ---------------------------------------------------------------------------------
    def snapshot(self, now: float, window_s: float = 30.0, history_s: int = 120, recent: int = 20) -> dict[str, Any]:
        with self._lock:
            self._expire(now)
            window = [t for t in self._done if now - t.finalized_at <= window_s * 1000.0]
            hist_src = [t for t in self._done if now - (t.frames_at or 0) <= history_s * 1000.0]
            stage_values = {k: [] for k in (*STAGES, "e2e", "server")}
            outcomes = {o: 0 for o in OUTCOMES}
            for t in window:
                outcomes[t.outcome] += 1
                for k, v in t.stages().items():
                    if v is not None:
                        stage_values[k].append(v)
            total = len(window)
            current = next(reversed(self._sessions)) if self._sessions else None
            return {
                "now_ms": now,
                "window_s": window_s,
                "frames_in_window": total,
                "stages": {k: summarize(v) for k, v in stage_values.items()},
                "outcomes": outcomes,
                "rates": {
                    "vision_drop": _ratio(outcomes["vision_dropped"], total),
                    "gesture_missing": _ratio(outcomes["no_gesture"], total),
                    "canvas_missing": _ratio(outcomes["no_canvas"], total),
                    "complete": _ratio(outcomes["complete"], total),
                },
                "fps": {kind: self._rates[kind].rate(now) for kind in KINDS},
                "received": dict(self._received),
                "unmatched": dict(self._unmatched),
                "open_frames": len(self._open),
                "current_session": current,
                "current_session_idle_ms": round(now - self._sessions[current]) if current else None,
                "history": _history(hist_src, now, history_s),
                "recent": [t.row() for t in list(self._done)[-recent:]][::-1],
            }


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _ratio(part: int, total: int) -> float | None:
    return round(part / total, 4) if total else None


def _history(traces: list[FrameTrace], now: float, seconds: int) -> list[dict[str, Any]]:
    """Per-second buckets (by frame arrival) of e2e p50/p95, stage medians and frame counts."""
    buckets: dict[int, list[FrameTrace]] = {}
    end = int(now // 1000)
    for t in traces:
        if t.frames_at is not None:
            buckets.setdefault(int(t.frames_at // 1000), []).append(t)
    out = []
    for sec in range(end - seconds + 1, end + 1):
        ts = buckets.get(sec, [])
        e2e = sorted(v for t in ts if (v := t.stages()["e2e"]) is not None)
        stage_p50 = {}
        for stage in STAGES:
            vals = sorted(v for t in ts if (v := t.stages()[stage]) is not None)
            stage_p50[stage] = None if not vals else round(percentile(vals, 50), 1)
        out.append({
            "t": sec * 1000,
            "frames": len(ts),
            "complete": sum(1 for t in ts if t.outcome == "complete"),
            "e2e_p50": None if not e2e else round(percentile(e2e, 50), 1),
            "e2e_p95": None if not e2e else round(percentile(e2e, 95), 1),
            "stage_p50": stage_p50,
        })
    return out
