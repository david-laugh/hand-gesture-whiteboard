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

    def test_snapshot_replays_existing_stroke(self):
        engine = CanvasEngine()
        engine.apply(gesture("DRAW_START", 0, {"x": .2, "y": .2}, stroke_id="line"))
        engine.apply(gesture("DRAW_MOVE", 1, {"x": .3, "y": .2}, stroke_id="line"))
        snapshot = engine.snapshot("session")
        self.assertEqual(snapshot["operations"][0]["type"], "stroke_begin")
        self.assertEqual(snapshot["operations"][1]["type"], "stroke_append")

    def test_eraser_start_creates_a_single_click_erase_area(self):
        engine = CanvasEngine(width=100, height=100)
        result = engine.apply(gesture("ERASE_START", 0, {"x": .5, "y": .5}, stroke_id="erase", eraser_radius=.2))
        operation = result["operations"][0]
        self.assertEqual(operation["type"], "erase_begin")
        self.assertEqual(operation["points"], [{"x": 50.0, "y": 50.0}])
        self.assertEqual(operation["style"]["width"], 40.0)


class ZoomTests(unittest.TestCase):
    def zoom(self, engine, scale, x, y, frame=0):
        for command in ("ZOOM_START", "ZOOM_UPDATE", "ZOOM_END"):
            result = engine.apply(gesture(command, frame, None, zoom_scale=scale, zoom_center={"x": x, "y": y}))
        return result["operations"][0]["view"]

    def test_default_view_is_identity(self):
        engine = CanvasEngine()
        start = engine.apply(gesture("DRAW_START", 0, {"x": .25, "y": .5}, stroke_id="a"))
        self.assertEqual(start["operations"][0]["points"], [{"x": 320.0, "y": 360.0}])

    def test_zoom_keeps_point_under_fingers_and_maps_drawing_back(self):
        engine = CanvasEngine()
        view = self.zoom(engine, 2.0, .25, .25)
        self.assertEqual(view, {"scale": 2.0, "offset": {"x": -320.0, "y": -180.0}})
        # The canvas point that was under the fingers (320, 180) is still drawn at the same screen spot.
        start = engine.apply(gesture("DRAW_START", 1, {"x": .25, "y": .25}, stroke_id="z"))
        self.assertEqual(start["operations"][0]["points"], [{"x": 320.0, "y": 180.0}])
        # Screen center while zoomed in x2 is canvas (480, 270).
        move = engine.apply(gesture("DRAW_MOVE", 2, {"x": .5, "y": .5}, stroke_id="z"))
        self.assertEqual(move["operations"][0]["points"][-1], {"x": 480.0, "y": 270.0})

    def test_zoom_back_to_one_restores_identity(self):
        engine = CanvasEngine()
        self.zoom(engine, 2.0, .25, .25)
        self.assertEqual(self.zoom(engine, 1.0, .8, .6), {"scale": 1.0, "offset": {"x": 0.0, "y": 0.0}})

    def test_zoom_never_shows_space_beside_the_page(self):
        engine = CanvasEngine()
        self.assertEqual(self.zoom(engine, 2.0, 1, 1)["offset"], {"x": -1280.0, "y": -720.0})
        engine = CanvasEngine()
        self.assertEqual(self.zoom(engine, 2.0, 0, 0)["offset"], {"x": 0.0, "y": 0.0})

    def test_zoom_out_centers_page_and_clamps_drawing_to_it(self):
        engine = CanvasEngine()
        self.assertEqual(self.zoom(engine, .5, .1, .9), {"scale": 0.5, "offset": {"x": 320.0, "y": 180.0}})
        start = engine.apply(gesture("DRAW_START", 1, {"x": 0, "y": 0}, stroke_id="o"))
        self.assertEqual(start["operations"][0]["points"], [{"x": 0, "y": 0}])

    def test_scale_is_limited_and_bad_params_are_tolerated(self):
        engine = CanvasEngine()
        self.assertEqual(self.zoom(engine, 9, .5, .5)["scale"], 3.0)
        result = engine.apply(gesture("ZOOM_UPDATE", 1, None, zoom_scale="x", zoom_center="bad"))
        self.assertEqual(result["operations"][0]["view"]["scale"], 3.0)

    def test_snapshot_carries_the_view(self):
        engine = CanvasEngine()
        self.zoom(engine, 1.5, .5, .5)
        self.assertEqual(engine.snapshot("session")["view"]["scale"], 1.5)
