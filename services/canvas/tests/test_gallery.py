import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import Settings, create_app


def gesture(command, frame_id, pointer=None, **params):
    return {"schema_version": "1.0.0", "message_type": "gesture", "trace_id": "trace0001",
            "session_id": "cam-gallery", "frame_id": frame_id, "timestamp_ms": frame_id,
            "gesture": command, "pointer": pointer, "confidence": 1.0, "params": params, "gesture_ms": 0}


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        settings = Settings()
        settings.save_dir = Path(self.directory.name)
        # No context manager: the lifespan (MQTT connection) does not run in these tests.
        self.client = TestClient(create_app(settings))

    def tearDown(self):
        self.directory.cleanup()

    def test_gallery_page_is_served(self):
        response = self.client.get("/gallery")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="gallery"', response.text)
        self.assertEqual(self.client.get("/static/gallery.js").status_code, 200)

    def test_empty_folder_lists_nothing(self):
        data = self.client.get("/v1/drawings").json()
        self.assertEqual(data["files"], [])
        self.assertEqual(data["live"], {"session_id": None, "active": False, "has_drawing": False, "saved_file": None})

    def test_saved_drawing_is_listed_with_cache_busting_url(self):
        self.client.post("/v1/apply", json=gesture("DRAW_START", 0, {"x": .2, "y": .5}, stroke_id="line"))
        self.client.post("/v1/apply", json=gesture("DRAW_MOVE", 1, {"x": .8, "y": .5}, stroke_id="line"))
        saved = self.client.post("/v1/sessions/cam-gallery/save").json()["file"]
        files = self.client.get("/v1/drawings").json()["files"]
        self.assertEqual([item["file"] for item in files], [saved])
        self.assertEqual(files[0]["session"], "cam-gall")
        self.assertTrue(files[0]["url"].startswith(f"/drawings/{saved}?v="))
        self.assertGreater(files[0]["saved_ms"], 0)
        image = self.client.get(files[0]["url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
