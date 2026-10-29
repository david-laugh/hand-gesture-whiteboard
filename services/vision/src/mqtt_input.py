"""MQTT subscriber that receives camera frames published by the gateway (Container A).

The gateway publishes every frame to `{prefix}/frames/{session_id}` (QoS 0, no retain)
as [uint32 BE meta_len][FrameMessage JSON][JPEG]. paho runs its network loop in its
own thread; parsed frames are handed to the asyncio loop, where the pipeline's
latest-wins slot decides what gets processed.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable

import paho.mqtt.client as mqtt

from .config import Settings
from .contract import FrameMeta
from .frame_input import FrameInputError, parse_mqtt_frame
from .metrics import VisionMetrics

log = logging.getLogger("vision.mqtt")

FrameHandler = Callable[[FrameMeta, bytes], object]


class MqttFrameSubscriber:
    def __init__(self, settings: Settings, metrics: VisionMetrics, on_frame: FrameHandler) -> None:
        self._settings = settings
        self._metrics = metrics
        self._on_frame = on_frame
        self.topic = f"{settings.mqtt_topic_prefix}/frames/+"
        self.client_id = "vision-" + uuid.uuid4().hex
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_invalid_log = 0.0
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        self._client.reconnect_delay_set(1, 10)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_subscribe = self._on_subscribe
        self._client.on_connect_fail = self._on_connect_fail
        self._last_connect_fail_log = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def broker(self) -> str:
        return f"{self._settings.mqtt_host}:{self._settings.mqtt_port}"

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        # connect_async + loop_start: paho keeps retrying in its thread if the broker is not up yet.
        self._client.connect_async(self._settings.mqtt_host, self._settings.mqtt_port, self._settings.mqtt_keepalive_s)
        self._client.loop_start()
        log.info("MQTT subscriber starting: broker=%s topic=%s", self.broker, self.topic)

    async def stop(self) -> None:
        self._client.disconnect()
        await asyncio.to_thread(self._client.loop_stop)
        self._connected = False

    # -- paho callbacks (paho network thread) ------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason, properties) -> None:
        if reason.is_failure:
            log.warning("MQTT connect to %s refused: %s", self.broker, reason)
            return
        self._connected = True
        self._metrics.incr("mqtt_connect_count")
        # Subscribe on every (re)connect: a clean session forgets subscriptions.
        client.subscribe(self.topic, qos=0)
        log.info("MQTT connected to %s, subscribing %s", self.broker, self.topic)

    def _on_connect_fail(self, client, userdata) -> None:
        # Without this, paho retries silently when the broker is unreachable (DNS/refused).
        now = time.monotonic()
        if now - self._last_connect_fail_log > 30.0:
            log.warning("MQTT broker %s unreachable; paho keeps retrying (1-10 s backoff)", self.broker)
            self._last_connect_fail_log = now

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        for code in reason_codes:
            if code.is_failure:
                log.error("MQTT subscribe to %s rejected: %s", self.topic, code)

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
                topic_prefix=self._settings.mqtt_topic_prefix,
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
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._on_frame, meta, jpeg)
