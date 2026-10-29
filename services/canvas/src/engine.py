"""Canvas state and point smoothing, independent from transport and image export."""
from dataclasses import dataclass, field
import math
import time


def now_ms():
    return time.time_ns() // 1_000_000


@dataclass
class Stroke:
    stroke_id: str
    points: list = field(default_factory=list)
    style: dict = field(default_factory=dict)
    erase: bool = False
    visible: bool = True


@dataclass
class Session:
    sequence: int = -1
    strokes: dict = field(default_factory=dict)
    order: list = field(default_factory=list)
    redo: list = field(default_factory=list)
    active_id: str | None = None
    # Viewport: screen_px = canvas_px * scale + offset (identity = scale 1, offset 0).
    view: dict = field(default_factory=lambda: {"scale": 1.0, "offset": {"x": 0, "y": 0}})
    # During a zoom gesture: (screen anchor px, canvas point under it) kept fixed while scaling.
    zoom_anchor: tuple | None = None


ZOOM_MIN, ZOOM_MAX = 0.5, 3.0


class CanvasEngine:
    def __init__(self, width=1280, height=720, smoothing=1.0, segment_px=8):
        self.width, self.height = width, height
        self.smoothing = smoothing
        self.segment_px = segment_px
        self.sessions = {}

    def session(self, session_id):
        return self.sessions.setdefault(session_id, Session())

    def to_canvas(self, state, screen_x, screen_y):
        """Screen pixels of the current (zoomed) view -> fixed canvas pixels."""
        scale, offset = state.view["scale"], state.view["offset"]
        return (screen_x - offset["x"]) / scale, (screen_y - offset["y"]) / scale

    def clamp_offset(self, scale, offset_x, offset_y):
        """Zoomed in: never show empty space beside the page. Zoomed out: keep the page centered."""
        def axis(size, offset):
            span = size * scale
            return (size - span) / 2 if span <= size else min(0.0, max(size - span, offset))
        return axis(self.width, offset_x), axis(self.height, offset_y)

    def zoom(self, state, command, params):
        try:
            scale = float(params.get("zoom_scale", state.view["scale"]))
        except (TypeError, ValueError):
            scale = state.view["scale"]
        scale = max(ZOOM_MIN, min(ZOOM_MAX, scale))
        center = params.get("zoom_center")
        if not (isinstance(center, dict) and all(isinstance(center.get(a), (int, float)) for a in ("x", "y"))):
            center = {"x": .5, "y": .5}
        anchor_screen = (min(1, max(0, center["x"])) * self.width, min(1, max(0, center["y"])) * self.height)
        # Pin the canvas point that was under the fingers when the zoom started (pinch-zoom behaviour).
        if command == "ZOOM_START" or not state.zoom_anchor or state.zoom_anchor[0] != anchor_screen:
            state.zoom_anchor = (anchor_screen, self.to_canvas(state, *anchor_screen))
        (screen_x, screen_y), (canvas_x, canvas_y) = state.zoom_anchor
        offset_x, offset_y = self.clamp_offset(scale, screen_x - scale * canvas_x, screen_y - scale * canvas_y)
        state.view = {"scale": round(scale, 4), "offset": {"x": round(offset_x, 2), "y": round(offset_y, 2)}}
        if command == "ZOOM_END":
            state.zoom_anchor = None
        return {"type": "set_view", "view": state.view}

    def point(self, pointer, state, previous=None):
        if pointer is None:
            return None
        # Pointers are in the zoomed screen; strokes are stored in the fixed canvas coordinates.
        canvas_x, canvas_y = self.to_canvas(state, pointer["x"] * self.width, pointer["y"] * self.height)
        raw = {"x": max(0, min(self.width, canvas_x)), "y": max(0, min(self.height, canvas_y))}
        # Low-pass filtering settles small frame-to-frame landmark jitter.
        if previous:
            raw = {axis: previous[axis] + self.smoothing * (raw[axis] - previous[axis]) for axis in ("x", "y")}
        return raw

    def segment(self, previous, current):
        if previous is None:
            return [current]
        distance = math.dist((previous["x"], previous["y"]), (current["x"], current["y"]))
        steps = max(1, math.ceil(distance / self.segment_px))
        return [{"x": previous["x"] + (current["x"] - previous["x"]) * index / steps,
                 "y": previous["y"] + (current["y"] - previous["y"]) * index / steps}
                for index in range(1, steps + 1)]

    def apply(self, gesture):
        required = ("schema_version", "trace_id", "session_id", "frame_id", "gesture", "pointer", "params")
        if not isinstance(gesture, dict) or any(key not in gesture for key in required):
            raise ValueError("Invalid GestureMessage")
        started = time.perf_counter()
        state = self.session(gesture["session_id"])
        command, pointer, params = gesture["gesture"], gesture["pointer"], gesture["params"]
        operations = []
        if command in ("DRAW_START", "ERASE_START"):
            stroke_id = params.get("stroke_id") or f"{gesture['trace_id']}-{gesture['frame_id']}"
            style = {"color": params.get("color", "#172554"), "width": params.get("brush_size", 4)}
            if command.startswith("ERASE"):
                style = {"width": params.get("eraser_radius", 0.025) * min(self.width, self.height) * 2}
            point = self.point(pointer, state)
            if point is not None:
                stroke = Stroke(stroke_id, [point], style, erase=command.startswith("ERASE"))
                state.strokes[stroke_id] = stroke
                state.order.append(stroke_id)
                state.redo.clear()
                state.active_id = stroke_id
                operations.append({"type": "erase_begin" if stroke.erase else "stroke_begin", "stroke_id": stroke_id,
                                   "points": [point], "style": style})
        elif command in ("DRAW_MOVE", "ERASE_MOVE"):
            stroke = state.strokes.get(params.get("stroke_id") or state.active_id)
            if stroke and stroke.visible and pointer is not None:
                point = self.point(pointer, state, stroke.points[-1] if stroke.points else None)
                points = self.segment(stroke.points[-1] if stroke.points else None, point)
                stroke.points.extend(points)
                operations.append({"type": "erase_append" if stroke.erase else "stroke_append", "stroke_id": stroke.stroke_id,
                                   "points": points})
        elif command in ("DRAW_END", "ERASE_END"):
            stroke = state.strokes.get(params.get("stroke_id") or state.active_id)
            if stroke:
                if pointer is not None:
                    point = self.point(pointer, state, stroke.points[-1] if stroke.points else None)
                    points = self.segment(stroke.points[-1] if stroke.points else None, point)
                    if points:
                        stroke.points.extend(points)
                        operations.append({"type": "erase_append" if stroke.erase else "stroke_append", "stroke_id": stroke.stroke_id,
                                           "points": points})
                operations.append({"type": "erase_end" if stroke.erase else "stroke_end", "stroke_id": stroke.stroke_id})
            state.active_id = None
        elif command == "UNDO" and state.order:
            stroke_id = state.order.pop()
            state.strokes[stroke_id].visible = False
            state.redo.append(stroke_id)
            operations.append({"type": "remove_stroke", "stroke_id": stroke_id})
        elif command == "REDO" and state.redo:
            stroke_id = state.redo.pop()
            state.strokes[stroke_id].visible = True
            state.order.append(stroke_id)
            operations.append({"type": "restore_stroke", "stroke_id": stroke_id})
        elif command == "CLEAR":
            state.strokes.clear(); state.order.clear(); state.redo.clear(); state.active_id = None
            operations.append({"type": "clear"})
        elif command in ("ZOOM_START", "ZOOM_UPDATE", "ZOOM_END"):
            operations.append(self.zoom(state, command, params))
        else:
            operations.append({"type": "noop"})
        state.sequence += 1
        return {"schema_version": gesture["schema_version"], "message_type": "canvas",
                "trace_id": gesture["trace_id"], "session_id": gesture["session_id"], "frame_id": gesture["frame_id"],
                "timestamp_ms": now_ms(), "sequence": state.sequence, "operations": operations,
                "history": {"undo_depth": len(state.order), "redo_depth": len(state.redo)},
                "canvas_ms": (time.perf_counter() - started) * 1000}

    def snapshot(self, session_id):
        """Return replayable operations for a browser joining an existing session."""
        state = self.sessions.get(session_id)
        if not state:
            return {"sequence": -1, "operations": [], "view": {"scale": 1, "offset": {"x": 0, "y": 0}}}
        operations = []
        for stroke in state.strokes.values():
            if not stroke.visible or not stroke.points:
                continue
            prefix = "erase" if stroke.erase else "stroke"
            operations.append({"type": f"{prefix}_begin", "stroke_id": stroke.stroke_id,
                               "points": [stroke.points[0]], "style": stroke.style})
            if len(stroke.points) > 1:
                operations.append({"type": f"{prefix}_append", "stroke_id": stroke.stroke_id,
                                   "points": stroke.points[1:]})
            operations.append({"type": f"{prefix}_end", "stroke_id": stroke.stroke_id})
        return {"sequence": state.sequence, "operations": operations, "view": state.view}
