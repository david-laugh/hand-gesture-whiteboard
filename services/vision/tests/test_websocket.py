"""GesturePublisher against the mock gesture server (no inference)."""

from __future__ import annotations

import asyncio
import socket

from src.contract import FrameMeta, Hand, build_landmarks_message, serialize
from src.metrics import VisionMetrics
from src.publisher import GesturePublisher
from tests.mock_gesture_server import MockGestureServer

LANDMARKS = [(0.5, 0.5, 0.0)] * 21


def _payload(frame_id: int, hands=True) -> str:
    hand_list = [Hand("Right", 0.9, LANDMARKS)] if hands else []
    return serialize(build_landmarks_message(FrameMeta("ws-test", frame_id, 1000 + frame_id), hand_list))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def test_publish_to_mock_server():
    server = MockGestureServer()
    port = server.start_in_thread()

    async def scenario():
        metrics = VisionMetrics()
        pub = GesturePublisher(f"ws://127.0.0.1:{port}/ws/landmarks", metrics, reconnect_interval_s=0.2)
        await pub.start()
        assert await _wait(lambda: pub.connected)
        for i in range(5):
            assert pub.publish("ws-test", i, _payload(i, hands=i % 2 == 0)) == "queued"
        assert await asyncio.to_thread(server.wait_for, 5)
        await pub.stop()
        return metrics.snapshot()

    snap = asyncio.run(scenario())
    server.stop()
    assert [m["frame_id"] for m in server.messages] == [0, 1, 2, 3, 4]
    assert server.messages[1]["hands"] == []
    assert server.errors == []
    assert snap["ws_sent_count"] == 5 and snap["ws_unsent_count"] == 0
    assert snap["timings_ms"]["websocket_send_ms"]["count"] == 5


def test_connection_failure_then_reconnect():
    port = _free_port()
    server = MockGestureServer(port=port)

    async def scenario():
        metrics = VisionMetrics()
        pub = GesturePublisher(f"ws://127.0.0.1:{port}/ws/landmarks", metrics,
                               reconnect_interval_s=0.2, open_timeout_s=0.5)
        await pub.start()
        # No server yet: attempts fail, messages are counted as unsent (not as "no hand").
        assert await _wait(lambda: metrics.snapshot()["ws_connect_attempt_count"] >= 2)
        assert not pub.connected
        assert pub.publish("ws-test", 1, _payload(1)) == "not_connected"

        await asyncio.to_thread(server.start_in_thread)
        assert await _wait(lambda: pub.connected, timeout=5)
        pub.publish("ws-test", 2, _payload(2))
        assert await asyncio.to_thread(server.wait_for, 1)

        # Server goes away: disconnect is detected, then it reconnects when the server returns.
        await asyncio.to_thread(server.stop)
        assert await _wait(lambda: not pub.connected, timeout=5)
        server2 = MockGestureServer(port=port)
        await asyncio.to_thread(server2.start_in_thread)
        assert await _wait(lambda: pub.connected, timeout=5)
        pub.publish("ws-test", 3, _payload(3))
        assert await asyncio.to_thread(server2.wait_for, 1)
        await pub.stop()
        await asyncio.to_thread(server2.stop)
        return metrics.snapshot(), server2.messages

    snap, second_messages = asyncio.run(scenario())
    assert [m["frame_id"] for m in server.messages] == [2]
    assert [m["frame_id"] for m in second_messages] == [3]
    assert snap["ws_unsent_count"] >= 1
    assert snap["ws_connect_count"] == 2
    assert snap["ws_connect_attempt_count"] > snap["ws_connect_count"]


def test_wrong_path_is_rejected_by_mock():
    server = MockGestureServer()
    port = server.start_in_thread()

    async def scenario():
        metrics = VisionMetrics()
        pub = GesturePublisher(f"ws://127.0.0.1:{port}/wrong", metrics, reconnect_interval_s=0.2)
        await pub.start()
        await asyncio.sleep(0.5)
        pub.publish("ws-test", 1, _payload(1))
        await asyncio.sleep(0.3)
        await pub.stop()

    asyncio.run(scenario())
    server.stop()
    assert server.messages == []
