import base64
import unittest

from src.config import Settings
from src.protocol import Contracts, decode_frame, encode_frame


def jpeg():
    # 4×3 white baseline JPEG fixture; no image decoder dependency is needed.
    return base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAADAAMEASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/Aaf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/Aaf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z")


class GatewayProtocolTests(unittest.TestCase):
    def test_packet_round_trip(self):
        metadata = {"frame_id": 2, "width": 4}
        self.assertEqual(decode_frame(encode_frame(metadata, b"jpeg")), (metadata, b"jpeg"))

    def test_browser_frame_is_validated_and_keeps_jpeg_binary(self):
        metadata = {"frame_id": 0, "captured_at_ms": 1, "width": 3, "height": 3,
                    "jpeg_quality": 70, "mirrored": True}
        contracts = Contracts(Settings())
        envelope, image = contracts.browser_frame(encode_frame(metadata, jpeg()), "session", -1, 2_000_000)
        self.assertTrue(image.startswith(b"\xff\xd8"))
        self.assertEqual(envelope["message_type"], "frame")
        self.assertEqual(envelope["session_id"], "session")
        self.assertEqual(envelope["frame_id"], 0)

    def test_frame_ids_must_increase(self):
        metadata = {"frame_id": 4, "captured_at_ms": 1, "width": 3, "height": 3}
        with self.assertRaises(ValueError):
            Contracts(Settings()).browser_frame(encode_frame(metadata, jpeg()), "session", 4, 2_000_000)
