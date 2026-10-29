"""Camera session tracking, live hand-skeleton payloads and automatic drawing saves.

Gateway creates a new session_id every time the phone starts recording, but it does not announce
start/stop. Vision publishes one landmarks message per processed frame (even with no hand), so the
first landmarks message of an unknown session_id marks its start, and silence for ``idle_s`` marks
its end. Both transitions are when the drawing of the previous session is saved as a PNG.
"""

import io
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw


class SessionTracker:
    """Pure state machine: callers pass activity in and get start/end events out."""

    def __init__(self, idle_s=5.0, clock=time.monotonic, wall_clock=time.time):
        self.idle_s = idle_s
        self.clock, self.wall_clock = clock, wall_clock
        self.last_seen = {}
        self.started_at = {}
        self.live = None

    def touch(self, session_id):
        """Record activity. Returns (previous_live, session_id) when a session (re)starts, else None."""
        is_new = session_id not in self.last_seen
        self.last_seen[session_id] = self.clock()
        if not is_new:
            return None
        self.started_at.setdefault(session_id, self.wall_clock())
        previous, self.live = self.live, session_id
        return previous, session_id

    def expire(self):
        """Return session ids that have been silent for longer than idle_s and forget their activity."""
        now = self.clock()
        ended = [sid for sid, seen in self.last_seen.items() if now - seen > self.idle_s]
        for sid in ended:
            del self.last_seen[sid]
        return ended

    def active(self, session_id):
        return session_id in self.last_seen


def normalise_hands(message):
    """Reduce a Vision or repo-contract landmarks message to [[ [x, y] * 21 ], ...] for the browser."""
    hands = []
    for hand in message.get("hands") or []:
        if not isinstance(hand, dict):
            continue
        points = []
        for point in hand.get("landmarks") or []:
            if isinstance(point, dict):
                x, y = point.get("x"), point.get("y")
            elif isinstance(point, (list, tuple)) and len(point) >= 2:
                x, y = point[0], point[1]
            else:
                x = y = None
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                points = []
                break
            points.append([round(float(x), 4), round(float(y), 4)])
        if len(points) == 21:
            hands.append(points)
    return hands


def render_png(engine, session_id, width, height):
    """Render the visible strokes of one session to PNG bytes (erasing paints the white background)."""
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    state = engine.sessions.get(session_id)
    if state:
        for stroke in state.strokes.values():
            if not stroke.visible or not stroke.points:
                continue
            points = [(point["x"], point["y"]) for point in stroke.points]
            color = "white" if stroke.erase else stroke.style.get("color", "#172554")
            size = max(1, round(stroke.style.get("width", 4)))
            for x, y in (points[0], points[-1]) if len(points) > 1 else points:
                draw.ellipse((x - size / 2, y - size / 2, x + size / 2, y + size / 2), fill=color)
            if len(points) > 1:
                draw.line(points, fill=color, width=size, joint="curve")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def has_visible_strokes(engine, session_id):
    state = engine.sessions.get(session_id)
    return bool(state) and any(stroke.visible and stroke.points for stroke in state.strokes.values())


def drawing_filename(session_id, started_at):
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id)[:8] or "session"
    return f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(started_at))}_{safe}.png"


def save_drawing(engine, session_id, started_at, save_dir, width, height):
    """Write the session drawing; returns the file name, or None when there is nothing to save.

    The name depends only on the session and its start time, so saving again overwrites the same file.
    """
    if not has_visible_strokes(engine, session_id):
        return None
    directory = Path(save_dir)
    directory.mkdir(parents=True, exist_ok=True)
    name = drawing_filename(session_id, started_at)
    temporary = directory / f".{name}.tmp"
    temporary.write_bytes(render_png(engine, session_id, width, height))
    temporary.replace(directory / name)
    return name
