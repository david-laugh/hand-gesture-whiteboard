"""MQTT frame input from the gateway.

- Parsing tests use the gateway's own packet code (services/gateway/src/protocol.py,
  loaded read-only) so a change in the gateway wire format breaks these tests.
- Broker tests use the `mqtt_broker` fixture (VISION_MQTT_BROKER=host:port, or a throwaway
  docker mosquitto started by conftest).
"""

from __future__ import annotations

import importlib.util
import struct
import time
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.frame_input import FrameInputError, parse_mqtt_frame
from tools import frames

GATEWAY_PROTOCOL = Path(__file__).resolve().parents[2] / "gateway" / "src" / "protocol.py"
PREFIX = "hgw/v1"


def _load_gateway_protocol():
    if not GATEWAY_PROTOCOL.is_file():
        pytest.skip(f"gateway source not available at {GATEWAY_PROTOCOL} (e.g. inside the vision container)")
    spec = importlib.util.spec_from_file_location("gateway_protocol", GATEWAY_PROTOCOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gateway():
    return _load_gateway_protocol()


@pytest.fixture(scope="module")
def jpeg():
    import cv2
    import numpy as np

    ok, buf = cv2.imencode(".jpg", np.zeros((480, 640, 3), np.uint8))
    return buf.tobytes()


def _gateway_packet(gateway, jpeg, session_id="a" * 32, captured_at_ms=1793235000123):
    contracts = gateway.Contracts(SimpleNamespace(schema_version="1.0.0"))
    # Camera page wire format: uint64 captured_at_ms + JPEG
    envelope, body = contracts.browser_frame(struct.pack(">Q", captured_at_ms) + jpeg, session_id, -1, 2 * 1024 * 1024)
    return envelope, gateway.encode_frame(envelope, body)


def test_parses_real_gateway_packet(gateway, jpeg):
    envelope, packet = _gateway_packet(gateway, jpeg)
    meta, body = parse_mqtt_frame(f"{PREFIX}/frames/{envelope['session_id']}", packet,
                                  topic_prefix=PREFIX, schema_version="1.0.0")
    assert body == jpeg
    assert meta.session_id == envelope["session_id"]
    assert meta.frame_id == envelope["frame_id"] == 0
    assert meta.ts == envelope["captured_at_ms"] == 1793235000123
    assert meta.trace_id == envelope["trace_id"]
    assert (meta.width, meta.height) == (640, 480)


def test_tools_packet_matches_gateway_format(gateway, jpeg):
    # tools.frames.gateway_packet (used by bench/manual tests) must stay compatible with the gateway
    meta, body = gateway.decode_frame(frames.gateway_packet("s1", 3, jpeg))
    gateway.Contracts(SimpleNamespace(schema_version="1.0.0")).validate("frame", meta)
    assert body == jpeg


@pytest.mark.parametrize(
    "mutate,topic_session",
    [
        (lambda m: m.update(message_type="landmarks"), None),
        (lambda m: m.update(content_type="image/png"), None),
        (lambda m: m.update(schema_version="2.0.0"), None),
        (lambda m: m.update(byte_length=m["byte_length"] + 1), None),
        (lambda m: m.pop("trace_id"), None),
        (lambda m: m.pop("captured_at_ms"), None),
        (lambda m: m.update(frame_id=-1), None),
        (lambda m: None, "other-session"),
    ],
)
def test_rejects_invalid_packets(gateway, jpeg, mutate, topic_session):
    envelope, _ = _gateway_packet(gateway, jpeg)
    envelope = dict(envelope)
    mutate(envelope)
    packet = gateway.encode_frame(envelope, jpeg)
    topic = f"{PREFIX}/frames/{topic_session or 'a' * 32}"
    with pytest.raises(FrameInputError):
        parse_mqtt_frame(topic, packet, topic_prefix=PREFIX, schema_version="1.0.0")


@pytest.mark.parametrize("topic", ["hgw/v1/frames", "hgw/v2/frames/abc", "hgw/v1/frames/abc/extra", "other"])
def test_rejects_unexpected_topics(gateway, jpeg, topic):
    _, packet = _gateway_packet(gateway, jpeg, session_id="abc")
    with pytest.raises(FrameInputError):
        parse_mqtt_frame(topic, packet, topic_prefix=PREFIX, schema_version="1.0.0")


def test_rejects_oversized_payload(gateway, jpeg):
    _, packet = _gateway_packet(gateway, jpeg)
    with pytest.raises(FrameInputError):
        parse_mqtt_frame(f"{PREFIX}/frames/{'a' * 32}", packet, topic_prefix=PREFIX,
                         schema_version="1.0.0", max_bytes=len(packet) - 1)


# -- broker integration: gateway packets in → inference → landmarks out ------------------

@pytest.fixture
def app_client(settings, mqtt_broker, mqtt_prefix):
    from fastapi.testclient import TestClient

    from src.main import create_app

    app = create_app(replace(settings, mqtt_enabled=True, mqtt_host=mqtt_broker.host,
                             mqtt_port=mqtt_broker.port, mqtt_topic_prefix=mqtt_prefix))
    with TestClient(app) as client:
        _wait(lambda: client.get("/healthz").json()["mqtt_connected"])
        time.sleep(0.3)  # let the SUBSCRIBE settle before publishing (QoS 0, no retain)
        yield client


@pytest.fixture
def gateway_publisher(mqtt_broker):
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(mqtt_broker.host, mqtt_broker.port)
    client.loop_start()
    yield client
    client.loop_stop()
    client.disconnect()


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


@pytest.mark.model
def test_gateway_frames_to_landmarks_topic(app_client, gateway_publisher, recorder, mqtt_prefix, hand_jpeg, no_hand_jpeg):
    session = uuid.uuid4().hex
    frames_topic = f"{mqtt_prefix}/frames/{session}"
    gateway_publisher.publish(frames_topic, frames.gateway_packet(session, 0, hand_jpeg, 111)).wait_for_publish()
    assert recorder.wait_for(1, timeout=10)
    gateway_publisher.publish(frames_topic, frames.gateway_packet(session, 1, no_hand_jpeg, 222)).wait_for_publish()
    assert recorder.wait_for(2, timeout=10)
    # malformed packet: counted as invalid, never forwarded
    gateway_publisher.publish(frames_topic, bytes([0, 0, 0, 2]) + b"{}garbage").wait_for_publish()
    time.sleep(0.5)

    snap = app_client.get("/metrics").json()
    records = app_client.get("/metrics/frames").json()
    first, second = recorder.messages[:2]
    assert (first["session_id"], first["frame_id"], first["ts"], len(first["hands"])) == (session, 0, 111, 1)
    assert (second["frame_id"], second["ts"], second["hands"]) == (1, 222, [])
    assert recorder.errors == []
    assert snap["mqtt_message_count"] == 3 and snap["invalid_frame_count"] == 1
    assert snap["processed_frame_count"] == 2 and snap["publish_count"] == 2
    assert all(r.get("trace_id") for r in records if r["session_id"] == session)


@pytest.mark.model
def test_reconnects_after_broker_restart(app_client, gateway_publisher, recorder, mqtt_broker, mqtt_prefix, hand_jpeg):
    mqtt_broker.restart()  # skips unless the broker is the docker-managed test broker
    _wait(lambda: not app_client.get("/healthz").json()["mqtt_connected"] or
          app_client.get("/metrics").json()["mqtt_disconnect_count"] >= 1, timeout=15)
    _wait(lambda: app_client.get("/healthz").json()["mqtt_connected"], timeout=20)
    _wait(lambda: recorder._client.is_connected() and gateway_publisher.is_connected(), timeout=20)
    time.sleep(0.5)  # resubscribe after reconnect
    session = uuid.uuid4().hex
    gateway_publisher.publish(f"{mqtt_prefix}/frames/{session}", frames.gateway_packet(session, 0, hand_jpeg, 5)).wait_for_publish()
    assert recorder.wait_for(1, timeout=10)
    assert recorder.messages[-1]["session_id"] == session
    snap = app_client.get("/metrics").json()
    assert snap["mqtt_disconnect_count"] >= 1 and snap["mqtt_connect_count"] >= 2
