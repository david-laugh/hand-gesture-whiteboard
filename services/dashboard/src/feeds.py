"""Data sources: a passive MQTT subscriber on all pipeline topics, and vision's /metrics."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
import urllib.request
import uuid

import paho.mqtt.client as mqtt

from .config import Settings
from .tracker import KINDS, LatencyTracker

log = logging.getLogger("dashboard")


def parse_payload(kind: str, payload: bytes) -> dict | None:
    """frames: [uint32 BE meta_len][FrameMessage JSON][JPEG] → only the JSON; others: JSON."""
    try:
        if kind == "frames":
            (size,) = struct.unpack_from(">I", payload, 0)
            if not 0 < size <= 16384 or 4 + size > len(payload):
                return None
            return json.loads(payload[4:4 + size])
        data = json.loads(payload)
        return data if isinstance(data, dict) else None
    except (struct.error, ValueError, UnicodeDecodeError):
        return None


class MqttFeed:
    """Subscribes to {prefix}/{frames,landmarks,gestures,canvas}/+ and timestamps arrivals."""

    def __init__(self, settings: Settings, tracker: LatencyTracker) -> None:
        self._settings = settings
        self._tracker = tracker
        self.connected = False
        self.invalid = 0
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="dashboard-" + uuid.uuid4().hex[:8])
        self._client.reconnect_delay_set(1, 10)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @property
    def broker(self) -> str:
        return f"{self._settings.mqtt_host}:{self._settings.mqtt_port}"

    def start(self) -> None:
        self._client.connect_async(self._settings.mqtt_host, self._settings.mqtt_port, 30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason, properties) -> None:
        if reason.is_failure:
            log.warning("MQTT connect to %s refused: %s", self.broker, reason)
            return
        self.connected = True
        for kind in KINDS:
            client.subscribe(f"{self._settings.mqtt_topic_prefix}/{kind}/+", qos=0)
        log.info("MQTT connected to %s", self.broker)

    def _on_disconnect(self, client, userdata, flags, reason, properties) -> None:
        if self.connected:
            log.warning("MQTT disconnected from %s: %s", self.broker, reason)
        self.connected = False

    def _on_message(self, client, userdata, message: mqtt.MQTTMessage) -> None:
        at = time.time() * 1000.0  # first thing: the arrival time is the measurement
        parts = message.topic.split("/")
        kind = parts[-2] if len(parts) >= 2 else ""
        if kind not in KINDS:
            return
        data = parse_payload(kind, message.payload)
        if data is None:
            self.invalid += 1
            return
        self._tracker.ingest(kind, data, at)


class VisionMetricsPoller:
    """Polls vision's GET /metrics for its internal stage timings (decode/inference/publish)."""

    def __init__(self, url: str, interval_s: float) -> None:
        self.url = url
        self.interval_s = interval_s
        self.latest: dict | None = None
        self.error: str | None = None
        self.updated_ms: float | None = None

    def _fetch(self) -> dict:
        with urllib.request.urlopen(self.url, timeout=2) as resp:
            return json.loads(resp.read())

    async def run(self) -> None:
        while True:
            try:
                self.latest = await asyncio.to_thread(self._fetch)
                self.error = None
                self.updated_ms = time.time() * 1000.0
            except Exception as exc:  # unreachable vision is shown in the UI, not fatal
                self.error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(self.interval_s)

    def view(self) -> dict:
        m = self.latest or {}
        timings = m.get("timings_ms", {})
        keep = ("decode_ms", "inference_ms", "serialization_ms", "publish_ms", "queue_wait_ms", "processing_ms")
        return {
            "url": self.url,
            "ok": self.error is None and self.latest is not None,
            "error": self.error,
            "updated_ms": self.updated_ms,
            "timings_ms": {k: timings.get(k) for k in keep if k in timings},
            "counters": {k: m.get(k) for k in ("input_frame_count", "processed_frame_count", "dropped_frame_count",
                                               "detected_frame_count", "no_hand_frame_count", "publish_dropped_count")
                         if k in m},
            "input_fps": m.get("input_fps"),
            "processing_fps": m.get("processing_fps"),
        }
