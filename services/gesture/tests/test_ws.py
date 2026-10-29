"""WebSocket 입력(/ws/landmarks) → MQTT 발행. 실제 브로커 없이 가짜 MQTT client로 확인."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from src import config
from src.main import app, processor
from src.mqtt_io import MqttBridge
from tests.conftest import enter_frames_at, load_fixture, to_vision_format
from tests.test_mqtt import FakeClient

WS_PATH = "/ws/landmarks"


@pytest.fixture
def fake_bridge():
    """lifespan이 끝난 뒤 app.state.mqtt를 가짜 bridge로 바꾼다 (테스트에서는 MQTT_ENABLED=false)."""
    with TestClient(app) as client:
        bridge = MqttBridge(processor, prefix="hgw/v1", client=FakeClient())
        bridge.connected = True
        app.state.mqtt = bridge
        yield client, bridge


def text(pose: str, i: int, session_id: str = "ws-01") -> str:
    repo = load_fixture(pose)
    repo["session_id"] = session_id
    repo["frame_id"] = i
    repo["timestamp_ms"] = int(time.time() * 1000) + i * 66
    return json.dumps(to_vision_format(repo))


def gestures(bridge):
    return [m["gesture"] for _, m, _, _ in bridge.client.published]


def test_ws_sequence_publishes_start_move_end(fake_bridge, gesture_validator):
    client, bridge = fake_bridge
    from tests.conftest import enter_frames_at, exit_frames_at

    n, m = enter_frames_at(66), exit_frames_at(66)
    seq = ["no_hand"] * 2 + ["index_pose"] * 6 + ["fist"] * m + ["no_hand"] * 2
    with client.websocket_connect(WS_PATH) as ws:
        for i, pose in enumerate(seq):
            ws.send_text(text(pose, i))
    # 연결을 닫으면 서버 쪽 처리가 끝날 때까지 기다린다
    assert gestures(bridge) == (
        ["IDLE"] * (2 + n - 1) + ["DRAW_START"] + ["DRAW_MOVE"] * (6 - n + m - 1) + ["DRAW_END"] + ["IDLE"] * 2
    )
    topics = {t for t, _, _, _ in bridge.client.published}
    assert topics == {"hgw/v1/gestures/ws-01"}
    for _, m, _, _ in bridge.client.published:
        gesture_validator.validate(m)


def test_ws_keeps_connection_after_invalid_messages(fake_bridge, caplog):
    client, bridge = fake_bridge
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_text("not json")
        ws.send_text(json.dumps({"type": "landmarks"}))
        ws.send_bytes(b"\xff\xfe")
        for i in range(3):
            ws.send_text(text("index_pose", i))
    assert caplog.text.count("invalid_message") == 3
    # 잘못된 메시지 뒤에도 같은 연결로 처리됨. 마지막 END는 그리는 중에 연결을 닫아서 나온 것.
    n = enter_frames_at(66)
    assert gestures(bridge) == ["IDLE"] * (n - 1) + ["DRAW_START"] + ["DRAW_MOVE"] * (3 - n) + ["DRAW_END"]


def test_ws_disconnect_while_drawing_publishes_draw_end_once(fake_bridge, monkeypatch, gesture_validator):
    client, bridge = fake_bridge
    with client.websocket_connect(WS_PATH) as ws:
        for i in range(5):
            ws.send_text(text("index_pose", i))
    n = enter_frames_at(66)
    assert gestures(bridge) == ["IDLE"] * (n - 1) + ["DRAW_START"] + ["DRAW_MOVE"] * (5 - n) + ["DRAW_END"]
    last_move, end = bridge.client.published[-2][1], bridge.client.published[-1][1]
    gesture_validator.validate(end)
    assert end["pointer"] == last_move["pointer"]
    assert end["params"]["stroke_id"] == last_move["params"]["stroke_id"]
    assert (end["trace_id"], end["frame_id"]) == (last_move["trace_id"], last_move["frame_id"])

    # 나중에 gap_watch가 돌아도 END가 한 번 더 나가면 안 된다
    monkeypatch.setattr(processor, "_clock", lambda: time.monotonic() + 10)
    bridge.check_gaps()
    assert gestures(bridge).count("DRAW_END") == 1


def test_gap_watch_then_disconnect_publishes_draw_end_once(fake_bridge, monkeypatch):
    """반대 순서: 연결이 살아 있는 동안 gap_watch가 먼저 END를 냈으면, 끊길 때는 다시 내지 않는다."""
    client, bridge = fake_bridge
    with client.websocket_connect(WS_PATH) as ws:
        for i in range(5):
            ws.send_text(text("index_pose", i))
        # 서버가 5개를 다 처리할 때까지 기다린 뒤 시간을 건너뛴다
        deadline = time.monotonic() + 2
        while len(bridge.client.published) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        real = time.monotonic
        monkeypatch.setattr(processor, "_clock", lambda: real() + 10)
        bridge.check_gaps()
        assert gestures(bridge).count("DRAW_END") == 1
    assert gestures(bridge).count("DRAW_END") == 1


def test_ws_disconnect_while_idle_publishes_nothing(fake_bridge):
    client, bridge = fake_bridge
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_text(text("no_hand", 0))
    assert gestures(bridge) == ["IDLE"]


def test_ws_two_sessions_on_one_connection(fake_bridge):
    client, bridge = fake_bridge
    with client.websocket_connect(WS_PATH) as ws:
        for i in range(3):
            ws.send_text(text("index_pose", i, session_id="a"))
            ws.send_text(text("no_hand", i, session_id="b"))
    ends = [(t, m["session_id"]) for t, m, _, _ in bridge.client.published if m["gesture"] == "DRAW_END"]
    assert ends == [("hgw/v1/gestures/a", "a")]


def test_ws_input_is_disabled_by_default():
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if k != "WS_INPUT_ENABLED"}
    out = subprocess.run(
        [sys.executable, "-c", "from src import config; print(config.WS_INPUT_ENABLED)"],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "False"


def test_ws_without_mqtt_only_logs(caplog):
    """MQTT가 꺼져 있으면(테스트 기본) 발행 대신 로그만 남긴다."""
    with TestClient(app) as client:
        assert app.state.mqtt is None
        with client.websocket_connect(WS_PATH) as ws:
            ws.send_text(text("index_pose", 0))
    assert "publish_skipped_mqtt_disabled" in caplog.text
