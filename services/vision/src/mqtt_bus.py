"""Vision's single MQTT connection: frames in from the gateway, landmarks out to gesture.

    in   {prefix}/frames/+                gateway → vision   [uint32 BE meta_len][FrameMessage JSON][JPEG]
    out  {prefix}/landmarks/{session_id}  vision → gesture   landmarks JSON (src/contract.py)

Both directions use QoS 0, retain false, like the rest of the team's topics. paho runs its
network loop in its own thread; parsed frames are handed to the asyncio loop, where the
pipeline's latest-wins slot decides what gets processed.

Landmarks are real-time data: while the broker is unreachable nothing is buffered, and
each unsent result is counted as `publish_dropped_count`. This keeps "broker down"
distinguishable from "no hand detected".
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Literal

import paho.mqtt.client as mqtt

from .config import Settings
from .contract import FrameMeta
from .frame_input import FrameInputError, parse_mqtt_frame
from .metrics import VisionMetrics

log = logging.getLogger("vision.mqtt")

FrameHandler = Callable[[FrameMeta, bytes], object]
PublishStatus = Literal["published", "not_connected", "error"]


def landmarks_topic(prefix: str, session_id: str) -> str:
    return f"{prefix}/landmarks/{session_id}"


class MqttBus:
    def __init__(
        self,
        settings: Settings,
        metrics: VisionMetrics,
        on_frame: FrameHandler | None,
        *,
        subscribe_frames: bool = True,
    ) -> None:
        self._settings = settings
        self._metrics = metrics
        self._on_frame = on_frame
        self._subscribe_frames = subscribe_frames
        self.prefix = settings.mqtt_topic_prefix
        self.frames_topic = f"{self.prefix}/frames/+"
        self.client_id = "vision-" + uuid.uuid4().hex
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_invalid_log = 0.0
        self._last_connect_fail_log = 0.0
        self._last_drop_log = 0.0
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        self._client.reconnect_delay_set(1, 10)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_subscribe = self._on_subscribe

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def broker(self) -> str:
        return f"{self._settings.mqtt_host}:{self._settings.mqtt_port}"

    def landmarks_topic(self, session_id: str) -> str:
        return landmarks_topic(self.prefix, session_id)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        # connect_async + loop_start: paho keeps retrying in its thread if the broker is not up yet.
        self._client.connect_async(self._settings.mqtt_host, self._settings.mqtt_port, self._settings.mqtt_keepalive_s)
        self._client.loop_start()
        log.info("MQTT starting: broker=%s subscribe=%s publish=%s",
                 self.broker, self.frames_topic if self._subscribe_frames else "-", self.landmarks_topic("{session_id}"))

    async def stop(self) -> None:
        self._client.disconnect()
        await asyncio.to_thread(self._client.loop_stop)
        self._connected = False

    # -- output: landmarks → gesture ---------------------------------------------------------
    def publish(self, session_id: str, frame_id: int, payload: str) -> PublishStatus:
        """Publish one landmarks message (non-blocking: paho queues it for its network thread)."""
        if not self._connected:
            self._metrics.incr("publish_dropped_count")
            self._metrics.record_frame(session_id, frame_id, publish_status="not_connected")
            now = time.monotonic()
            if now - self._last_drop_log > 5.0:
                log.warning("MQTT not connected (%s); dropping landmark messages", self.broker)
                self._last_drop_log = now
            return "not_connected"
        t0 = time.perf_counter()
        info = self._client.publish(self.landmarks_topic(session_id), payload, qos=0, retain=False)
        publish_ms = (time.perf_counter() - t0) * 1000.0
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self._metrics.incr("publish_error_count")
            self._metrics.record_frame(session_id, frame_id, publish_status="error", publish_rc=info.rc)
            log.warning("MQTT publish failed for frame %s/%s: rc=%s", session_id, frame_id, info.rc)
            return "error"
        self._metrics.incr("publish_count")
        self._metrics.observe("publish_ms", publish_ms)
        self._metrics.record_frame(session_id, frame_id, publish_status="published", publish_ms=round(publish_ms, 3))
        return "published"

    # -- paho callbacks (paho network thread) ------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason, properties) -> None:
        if reason.is_failure:
            log.warning("MQTT connect to %s refused: %s", self.broker, reason)
            return
        self._connected = True
        self._metrics.incr("mqtt_connect_count")
        if self._subscribe_frames:
            # Subscribe on every (re)connect: a clean session forgets subscriptions.
            client.subscribe(self.frames_topic, qos=0)
        log.info("MQTT connected to %s", self.broker)

    def _on_connect_fail(self, client, userdata) -> None:
        # Without this, paho retries silently when the broker is unreachable (DNS/refused).
        now = time.monotonic()
        if now - self._last_connect_fail_log > 30.0:
            log.warning("MQTT broker %s unreachable; paho keeps retrying (1-10 s backoff)", self.broker)
            self._last_connect_fail_log = now

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        for code in reason_codes:
            if code.is_failure:
                log.error("MQTT subscribe to %s rejected: %s", self.frames_topic, code)

    def _on_disconnect(self, client, userdata, flags, reason, properties) -> None:
        if self._connected:
            log.warning("MQTT disconnected from %s: %s (paho will reconnect)", self.broker, reason)
            self._metrics.incr("mqtt_disconnect_count")
        self._connected = False

    def _on_message(self, client, userdata, message: mqtt.MQTTMessage) -> None:
        self._metrics.incr("mqtt_message_count")
        try:
            meta, jpeg = parse_mqtt_frame(
                message.topic,
                message.payload,
                topic_prefix=self.prefix,
                schema_version=self._settings.schema_version,
                max_bytes=self._settings.max_frame_bytes,
            )
        except FrameInputError as exc:
            self._metrics.incr("invalid_frame_count")
            now = time.monotonic()
            if now - self._last_invalid_log > 5.0:
                log.warning("invalid MQTT frame on %s: %s", message.topic, exc)
                self._last_invalid_log = now
            return
        if self._loop is not None and self._on_frame is not None:
            self._loop.call_soon_threadsafe(self._on_frame, meta, jpeg)
