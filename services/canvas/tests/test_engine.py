import unittest

from src.engine import CanvasEngine


def gesture(command, frame_id, pointer=None, **params):
    return {"schema_version": "1.0.0", "message_type": "gesture", "trace_id": "trace0001",
            "session_id": "session", "frame_id": frame_id, "timestamp_ms": frame_id,
            "gesture": command, "pointer": pointer, "confidence": 1.0, "params": params, "gesture_ms": 0}


class CanvasEngineTests(unittest.TestCase):
    def test_draw_interpolates_move_points(self):
        engine = CanvasEngine(width=100, height=100, smoothing=1, segment_px=10)
        begin = engine.apply(gesture("DRAW_START", 0, {"x": .1, "y": .1}, stroke_id="line"))
        move = engine.apply(gesture("DRAW_MOVE", 1, {"x": .9, "y": .1}, stroke_id="line"))
        self.assertEqual(begin["operations"][0]["type"], "stroke_begin")
        self.assertGreaterEqual(len(move["operations"][0]["points"]), 8)
        self.assertEqual(move["sequence"], 1)

    def test_undo_and_redo_emit_visibility_operations(self):
        engine = CanvasEngine()
        engine.apply(gesture("DRAW_START", 0, {"x": .5, "y": .5}, stroke_id="line"))
        undo = engine.apply(gesture("UNDO", 1))
        redo = engine.apply(gesture("REDO", 2))
        self.assertEqual(undo["operations"][0]["type"], "remove_stroke")
        self.assertEqual(redo["operations"][0]["type"], "restore_stroke")
