"""REAL MediaPipe inference on the official test image (skipped if model/image are missing)."""

from __future__ import annotations

import json

import pytest

from src.contract import FrameMeta, validate_landmarks_message
from src.detector import HandDetector, JpegDecodeError, ModelLoadError
from src.metrics import VisionMetrics
from src.pipeline import FramePipeline
from tools import frames

pytestmark = pytest.mark.model


@pytest.fixture
def pipeline(settings):
    detector = HandDetector(settings)
    yield FramePipeline(detector, VisionMetrics(), publisher=None)
    detector.close()


def test_hand_detected_with_21_landmarks(pipeline, hand_jpeg):
    result = pipeline.process(FrameMeta("s-hand", 1, 1793235000123, 640, 480), hand_jpeg)
    msg = result.message
    validate_landmarks_message(msg)
    assert len(msg["hands"]) == 1
    hand = msg["hands"][0]
    assert hand["handedness"] in ("Left", "Right")
    assert 0.0 < hand["score"] <= 1.0
    assert len(hand["landmarks"]) == 21
    for x, y, _z in hand["landmarks"]:
        # this test frame contains the whole hand, so coordinates are inside the image
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
    assert result.inference_ms > 0 and result.decode_ms > 0
    assert (result.image_width, result.image_height) == (640, 480)


def test_no_hand_returns_empty_hands(pipeline, no_hand_jpeg):
    msg = pipeline.process(FrameMeta("s-empty", 2, 1793235000156), no_hand_jpeg).message
    validate_landmarks_message(msg)
    assert msg["hands"] == []
    assert msg["gesture"] is None


def test_ids_preserved_and_json_serializable(pipeline, hand_jpeg):
    result = pipeline.process(FrameMeta("session-xyz", 4242, 1793235999999), hand_jpeg)
    decoded = json.loads(result.payload)
    assert decoded == result.message
    assert (decoded["session_id"], decoded["frame_id"], decoded["ts"]) == ("session-xyz", 4242, 1793235999999)
    assert decoded["gesture"] is None


def test_metrics_separate_detected_and_no_hand(pipeline, hand_jpeg, no_hand_jpeg):
    pipeline.process_and_record(FrameMeta("s-m", 1, 1), hand_jpeg)
    pipeline.process_and_record(FrameMeta("s-m2", 1, 1), no_hand_jpeg)
    snap = pipeline._metrics.snapshot()
    assert snap["processed_frame_count"] == 2
    assert snap["detected_frame_count"] == 1
    assert snap["no_hand_frame_count"] == 1
    assert snap["timings_ms"]["inference_ms"]["count"] == 2


def test_continuous_frames_video_mode(pipeline, test_image):
    seq = frames.sliding_sequence(test_image, count=10)
    results = [pipeline.process(FrameMeta("s-seq", i, 1000 + i * 33), jpeg).message for i, jpeg in enumerate(seq)]
    assert [m["frame_id"] for m in results] == list(range(10))
    assert all(len(m["hands"]) == 1 for m in results)


def test_non_monotonic_ts_is_accepted(pipeline, hand_jpeg):
    # Out-of-order source timestamps must not crash VIDEO mode; ts is still echoed unchanged.
    a = pipeline.process(FrameMeta("s-ts", 1, 5000), hand_jpeg).message
    b = pipeline.process(FrameMeta("s-ts", 2, 4000), hand_jpeg).message
    assert (a["ts"], b["ts"]) == (5000, 4000)


def test_corrupt_jpeg_is_not_reported_as_no_hand(pipeline):
    with pytest.raises(JpegDecodeError):
        pipeline.process(FrameMeta("s-bad", 1, 1), b"\xff\xd8\xff" + b"\x00" * 100)
    assert pipeline.process_and_record(FrameMeta("s-bad", 2, 1), b"\xff\xd8\xff" + b"\x00" * 100) is None
    snap = pipeline._metrics.snapshot()
    assert snap["invalid_frame_count"] == 1
    assert snap["no_hand_frame_count"] == 0 and snap["processed_frame_count"] == 0


def test_missing_model_fails_loudly(settings, tmp_path):
    from dataclasses import replace

    with pytest.raises(ModelLoadError):
        HandDetector(replace(settings, model_path=str(tmp_path / "missing.task")))
    broken = tmp_path / "broken.task"
    broken.write_bytes(b"not a model")
    with pytest.raises(ModelLoadError):
        HandDetector(replace(settings, model_path=str(broken)))
