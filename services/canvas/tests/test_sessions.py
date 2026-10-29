import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.engine import CanvasEngine
from src.sessions import SessionTracker, drawing_filename, normalise_hands, save_drawing


def gesture(command, frame_id, pointer=None, session="cam-a", **params):
    return {"schema_version": "1.0.0", "message_type": "gesture", "trace_id": "trace0001",
            "session_id": session, "frame_id": frame_id, "timestamp_ms": frame_id,
            "gesture": command, "pointer": pointer, "confidence": 1.0, "params": params, "gesture_ms": 0}


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class SessionTrackerTests(unittest.TestCase):
    def test_new_session_becomes_live_and_reports_previous(self):
        clock = FakeClock()
        tracker = SessionTracker(idle_s=5, clock=clock, wall_clock=lambda: 1000.0)
        self.assertEqual(tracker.touch("a"), (None, "a"))
        self.assertIsNone(tracker.touch("a"))
        self.assertEqual(tracker.touch("b"), ("a", "b"))
        self.assertEqual(tracker.live, "b")

    def test_silent_session_expires_once(self):
        clock = FakeClock()
        tracker = SessionTracker(idle_s=5, clock=clock)
        tracker.touch("a")
        clock.now += 4
        self.assertEqual(tracker.expire(), [])
        clock.now += 2
        self.assertEqual(tracker.expire(), ["a"])
        self.assertEqual(tracker.expire(), [])
        self.assertFalse(tracker.active("a"))

    def test_resumed_session_keeps_its_start_time(self):
        clock = FakeClock()
        starts = iter([1000.0, 2000.0])
        tracker = SessionTracker(idle_s=5, clock=clock, wall_clock=lambda: next(starts))
        tracker.touch("a")
        clock.now += 10
        tracker.expire()
        self.assertEqual(tracker.touch("a"), ("a", "a"))
        self.assertEqual(tracker.started_at["a"], 1000.0)


class LandmarkTests(unittest.TestCase):
    def test_vision_and_repo_formats_become_xy_lists(self):
        vision = {"type": "landmarks", "hands": [{"handedness": "Left", "landmarks": [[i / 40, .5, 0] for i in range(21)]}]}
        repo = {"message_type": "landmarks", "hands": [{"landmarks": [{"id": i, "x": i / 40, "y": .5, "z": 0} for i in range(21)]}]}
        self.assertEqual(normalise_hands(vision), normalise_hands(repo))
        self.assertEqual(normalise_hands(vision)[0][2], [0.05, 0.5])

    def test_no_hand_and_broken_hands_are_dropped(self):
        self.assertEqual(normalise_hands({"hands": []}), [])
        self.assertEqual(normalise_hands({"hands": [{"landmarks": [[0.1, 0.2]] * 5}]}), [])
        self.assertEqual(normalise_hands({"hands": [{"landmarks": [["x", 0.2]] * 21}]}), [])


class SaveDrawingTests(unittest.TestCase):
    def test_empty_session_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(save_drawing(CanvasEngine(), "cam-a", 0, directory, 1280, 720))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_drawing_is_saved_and_resaving_overwrites_same_file(self):
        engine = CanvasEngine()
        engine.apply(gesture("DRAW_START", 0, {"x": .1, "y": .5}, stroke_id="line", color="#DC2626", brush_size=16))
        engine.apply(gesture("DRAW_MOVE", 1, {"x": .9, "y": .5}, stroke_id="line"))
        with tempfile.TemporaryDirectory() as directory:
            first = save_drawing(engine, "cam-a", 0, directory, 1280, 720)
            second = save_drawing(engine, "cam-a", 0, directory, 1280, 720)
            self.assertEqual(first, second)
            self.assertEqual([path.name for path in Path(directory).iterdir()], [first])
            image = Image.open(Path(directory) / first)
            self.assertEqual(image.size, (1280, 720))
            self.assertEqual(image.getpixel((640, 360)), (220, 38, 38))

    def test_undone_stroke_is_not_saved(self):
        engine = CanvasEngine()
        engine.apply(gesture("DRAW_START", 0, {"x": .5, "y": .5}, stroke_id="line"))
        engine.apply(gesture("UNDO", 1))
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(save_drawing(engine, "cam-a", 0, directory, 1280, 720))

    def test_filename_strips_unsafe_characters(self):
        self.assertTrue(drawing_filename("../../etc", 0).endswith("_etc.png"))
        self.assertTrue(drawing_filename("0123456789abcdef", 0).endswith("_01234567.png"))


if __name__ == "__main__":
    unittest.main()
