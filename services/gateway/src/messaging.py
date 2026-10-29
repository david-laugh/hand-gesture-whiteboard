"""MQTT publisher for one-way camera frame delivery."""
import asyncio
import uuid

import paho.mqtt.client as mqtt


class MqttTransport:
    def __init__(self, settings):
        self.settings = settings
        self.client_id = "gateway-" + uuid.uuid4().hex
        self.connected = False
        self.loop = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        self.client.reconnect_delay_set(1, 10)
        self.client.max_queued_messages_set(settings.max_sessions * 3)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, 30)
        self.client.loop_start()

    async def stop(self):
        self.connected = False
        self.client.disconnect()
        await asyncio.to_thread(self.client.loop_stop)

    def _on_connect(self, client, userdata, flags, reason, properties):
        if not reason.is_failure:
            self.loop.call_soon_threadsafe(setattr, self, "connected", True)

    def _on_disconnect(self, client, userdata, flags, reason, properties):
        self.loop.call_soon_threadsafe(setattr, self, "connected", False)

    async def publish_frame(self, envelope, jpeg):
        if not self.connected:
            raise ConnectionError("MQTT is not ready")
        # The packet keeps JPEG binary separate from JSON metadata, matching frame.schema.json.
        from .protocol import encode_frame
        topic = f"{self.settings.mqtt_prefix}/frames/{envelope['session_id']}"
        info = self.client.publish(topic, encode_frame(envelope, jpeg), qos=0, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT publish failed: {info.rc}")
