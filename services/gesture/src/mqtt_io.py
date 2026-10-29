"""MQTT 입출력.

구독: {prefix}/landmarks/+   (vision → gesture, MQTT_INPUT_ENABLED=true일 때)
발행: {prefix}/gestures/{session_id}   (gesture → canvas)
QoS 0, retain 없음. paho가 자기 스레드에서 수신 콜백을 차례로 부르므로 같은 연결의 메시지 순서가 지켜진다.
"""
import json
import logging
import threading
import uuid

import paho.mqtt.client as mqtt

from . import config
from .logs import log_json
from .processor import GestureProcessor

GAP_CHECK_INTERVAL_S = 0.1


class MqttBridge:
    def __init__(
        self,
        processor: GestureProcessor,
        host: str = config.MQTT_HOST,
        port: int = config.MQTT_PORT,
        prefix: str = config.MQTT_TOPIC_PREFIX,
        subscribe: bool = config.MQTT_INPUT_ENABLED,
        client=None,
    ):
        self.processor = processor
        self.host = host
        self.port = port
        self.subscribe = subscribe
        self.in_topic = f"{prefix}/landmarks/+"
        self.out_prefix = f"{prefix}/gestures"
        self.connected = False
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self.client = client or mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="gesture-" + uuid.uuid4().hex
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # --- 시작 / 종료 ---------------------------------------------------------

    def start(self) -> None:
        # connect_async + loop_start: 브로커가 없어도 바로 반환하고, 백그라운드에서 계속 재연결한다.
        self.client.reconnect_delay_set(1, 10)
        self.client.connect_async(self.host, self.port, 30)
        self.client.loop_start()
        self._stop.clear()
        self._watcher = threading.Thread(target=self._watch_gaps, name="gesture-gap-watch", daemon=True)
        self._watcher.start()
        log_json(event="mqtt_start", host=self.host, port=self.port,
                 subscribe=self.in_topic if self.subscribe else None)

    def stop(self) -> None:
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=1)
        self.client.disconnect()
        self.client.loop_stop()
        self.connected = False

    # --- paho 콜백 (paho 스레드) ----------------------------------------------

    def _on_connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            log_json(logging.WARNING, event="mqtt_connect_failed", reason=str(reason))
            return
        self.connected = True
        if self.subscribe:
            client.subscribe(self.in_topic, qos=0)  # 재연결할 때마다 다시 구독
        log_json(event="mqtt_connected", host=self.host, port=self.port,
                 subscribe=self.in_topic if self.subscribe else None)

    def _on_disconnect(self, client, userdata, flags, reason, properties):
        self.connected = False
        log_json(logging.WARNING, event="mqtt_disconnected", reason=str(reason))

    def _on_message(self, client, userdata, message):
        self.handle_payload(message.topic, message.payload)

    # --- 처리 -----------------------------------------------------------------

    def handle_payload(self, topic: str, payload: bytes) -> None:
        """잘못된 메시지는 로그만 남기고 건너뛴다."""
        try:
            out = self.processor.process_payload(payload, source="mqtt", origin=topic)
        except Exception:
            logging.getLogger(config.SERVICE_NAME).exception("process failed (topic=%s)", topic)
            return
        if out is not None:
            self.publish(out)

    def publish(self, gesture_message: dict) -> None:
        topic = f"{self.out_prefix}/{gesture_message['session_id']}"
        if not self.connected:
            log_json(logging.WARNING, event="publish_skipped_not_connected", topic=topic,
                     frame_id=gesture_message["frame_id"], gesture=gesture_message["gesture"])
            return
        info = self.client.publish(topic, json.dumps(gesture_message, separators=(",", ":")), qos=0, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log_json(logging.WARNING, event="publish_failed", topic=topic, rc=info.rc)

    def check_gaps(self) -> None:
        for out in self.processor.expire():
            self.publish(out)

    def _watch_gaps(self) -> None:
        while not self._stop.wait(GAP_CHECK_INTERVAL_S):
            try:
                self.check_gaps()
            except Exception:
                logging.getLogger(config.SERVICE_NAME).exception("gap watch failed")
