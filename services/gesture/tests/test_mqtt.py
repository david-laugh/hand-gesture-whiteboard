"""실제 브로커 없이 MQTT 입출력 테스트. 발행은 가짜 client가 기록만 한다."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import config
from src.main import app
from src.mqtt_io import MqttBridge
from src.processor import GestureProcessor
from tests.conftest import enter_frames_at, exit_frames_at, load_fixture, to_vision_format

PREFIX = "hgw/v1"


class FakeClient:
    def __init__(self):
        self.published = []  # (topic, dict, qos, retain)
        self.subscribed = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload), qos, retain))
        return SimpleNamespace(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def bridge(clock):
    b = MqttBridge(GestureProcessor(clock=clock), prefix=PREFIX, client=FakeClient())
    b.connected = True
    return b


SERVER_NOW_MS = 1_793_260_800_000


def frame(pose: str, i: int, fmt: str = "vision", session_id: str = "sess-01", ts_offset_ms: int = 0) -> bytes:
    repo = load_fixture(pose)
    repo["session_id"] = session_id
    repo["frame_id"] = i
    repo["timestamp_ms"] = SERVER_NOW_MS + ts_offset_ms + i * 66
    data = to_vision_format(repo) if fmt == "vision" else repo
    return json.dumps(data).encode()


def deliver(bridge, payload: bytes, session_id: str = "sess-01"):
    """paho가 부르는 수신 콜백에 직접 넣는다."""
    bridge._on_message(None, None, SimpleNamespace(topic=f"{PREFIX}/landmarks/{session_id}", payload=payload))


def published(bridge):
    return bridge.client.published


def gestures(bridge):
    return [m["gesture"] for _, m, _, _ in published(bridge)]


N, M = enter_frames_at(66), exit_frames_at(66)  # 66ms 간격에서 진입/이탈에 필요한 프레임 수
DRAW_SEQUENCE = ["no_hand"] * 2 + ["index_pose"] * 6 + ["fist"] * M + ["no_hand"] * 2


@pytest.mark.parametrize("fmt", ["vision", "repo"])
def test_sequence_publishes_draw_start_move_end(bridge, fmt):
    for i, pose in enumerate(DRAW_SEQUENCE):
        deliver(bridge, frame(pose, i, fmt))
    assert gestures(bridge) == (
        ["IDLE"] * (2 + N - 1) + ["DRAW_START"] + ["DRAW_MOVE"] * (6 - N) + ["DRAW_MOVE"] * (M - 1) + ["DRAW_END"] + ["IDLE"] * 2
    )
    stroke_ids = {m["params"].get("stroke_id") for _, m, _, _ in published(bridge)[2 + N - 1:2 + 6 + M]}
    assert len(stroke_ids) == 1 and None not in stroke_ids


def test_publish_topic_qos_and_retain(bridge):
    deliver(bridge, frame("index_pose", 0, session_id="cam-7"), session_id="cam-7")
    topic, msg, qos, retain = published(bridge)[0]
    assert topic == f"{PREFIX}/gestures/cam-7"
    assert msg["session_id"] == "cam-7"
    assert (qos, retain) == (0, False)


def test_ids_are_copied_from_payload(bridge):
    deliver(bridge, frame("index_pose", 0, fmt="repo"))
    _, msg, _, _ = published(bridge)[0]
    repo = load_fixture("index_pose")
    assert msg["trace_id"] == repo["trace_id"]
    assert (msg["session_id"], msg["frame_id"]) == ("sess-01", 0)


def test_invalid_messages_are_skipped(bridge, caplog):
    deliver(bridge, b"not json")
    deliver(bridge, json.dumps({"type": "landmarks"}).encode())
    bad = json.loads(frame("index_pose", 1))
    bad["hands"][0]["landmarks"] = bad["hands"][0]["landmarks"][:5]
    deliver(bridge, json.dumps(bad).encode())
    assert published(bridge) == []
    assert caplog.text.count("invalid_message") == 3

    for i in range(3):
        deliver(bridge, frame("index_pose", 10 + i))
    assert gestures(bridge) == ["IDLE"] * (N - 1) + ["DRAW_START"] + ["DRAW_MOVE"] * (3 - N)


def test_gap_while_drawing_publishes_draw_end(bridge, clock):
    for i in range(5):
        deliver(bridge, frame("index_pose", i))
    last = published(bridge)[-1][1]
    assert last["gesture"] == "DRAW_MOVE"

    clock.t += 0.3  # 아직 SESSION_GAP_MS(500ms) 전
    bridge.check_gaps()
    assert len(published(bridge)) == 5

    clock.t += 0.3  # 총 600ms 동안 메시지 없음
    bridge.check_gaps()
    topic, end, _, _ = published(bridge)[-1]
    assert end["gesture"] == "DRAW_END"
    assert topic == f"{PREFIX}/gestures/sess-01"
    assert end["params"]["stroke_id"] == last["params"]["stroke_id"]
    assert end["pointer"] == last["pointer"]  # 마지막 그리기 좌표
    assert (end["trace_id"], end["session_id"], end["frame_id"]) == (
        last["trace_id"], last["session_id"], last["frame_id"])

    bridge.check_gaps()  # 한 번만 발행
    assert gestures(bridge).count("DRAW_END") == 1


@pytest.mark.parametrize("ts_offset_ms", [10_000, -10_000], ids=["phone_ahead_10s", "phone_behind_10s"])
def test_gap_watch_uses_server_clock_not_ts(clock, ts_offset_ms):
    """ts(휴대폰 시계)가 서버보다 10초 앞서거나 뒤처져도, 끊김 감시는 서버 수신 시각으로 판단한다."""
    server_ms = lambda: SERVER_NOW_MS + int((clock.t - 100.0) * 1000)  # noqa: E731
    b = MqttBridge(GestureProcessor(clock=clock, wall_ms=server_ms), prefix=PREFIX, client=FakeClient())
    b.connected = True

    # 66ms 간격으로 계속 들어오는 동안에는 감시가 DRAW_END를 만들면 안 된다
    for i in range(20):
        deliver(b, frame("index_pose", i, ts_offset_ms=ts_offset_ms))
        b.check_gaps()
        clock.t += 0.066
    assert gestures(b) == ["IDLE"] * (N - 1) + ["DRAW_START"] + ["DRAW_MOVE"] * (20 - N)

    clock.t += 0.4  # 마지막 수신 후 약 470ms: 아직 아님
    b.check_gaps()
    assert "DRAW_END" not in gestures(b)

    clock.t += 0.1  # 약 570ms > 500ms
    b.check_gaps()
    assert gestures(b)[-1] == "DRAW_END"


def test_gap_while_idle_publishes_nothing(bridge, clock):
    deliver(bridge, frame("no_hand", 0))
    clock.t += 1.0
    bridge.check_gaps()
    assert gestures(bridge) == ["IDLE"]


def test_not_connected_logs_instead_of_publishing(bridge, caplog):
    bridge.connected = False
    deliver(bridge, frame("index_pose", 0))
    assert published(bridge) == []
    assert "publish_skipped_not_connected" in caplog.text


def test_subscribes_on_connect(bridge):
    bridge._on_connect(bridge.client, None, None, SimpleNamespace(is_failure=False), None)
    assert bridge.client.subscribed == [(f"{PREFIX}/landmarks/+", 0)]


def test_all_published_messages_pass_schema(bridge, clock, gesture_validator):
    for i, pose in enumerate(DRAW_SEQUENCE[:8]):
        deliver(bridge, frame(pose, i))
    clock.t += 1.0
    bridge.check_gaps()  # gap으로 만든 DRAW_END 포함
    msgs = [m for _, m, _, _ in published(bridge)]
    assert "DRAW_END" in [m["gesture"] for m in msgs]
    for m in msgs:
        gesture_validator.validate(m)


def test_lifespan_without_mqtt_starts_no_bridge():
    with TestClient(app) as c:
        assert app.state.mqtt is None
        assert c.get("/healthz").status_code == 200
