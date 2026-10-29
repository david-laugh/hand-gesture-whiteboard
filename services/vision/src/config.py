"""Runtime settings for the vision service, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model_path: str = "/opt/models/hand_landmarker.task"
    num_hands: int = 1
    min_hand_detection_confidence: float = 0.5
    min_hand_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    # MediaPipe assumes a mirrored (selfie) image when labelling handedness.
    # Set to true only if field tests show the labels are reversed for our camera setup.
    swap_handedness: bool = False
    # One HandLandmarker (VIDEO mode, keeps tracking state) per session; LRU-evicted beyond this.
    max_sessions: int = 4

    # MQTT: frames in on {prefix}/frames/+ (gateway), landmarks out on {prefix}/landmarks/{session_id}
    # (gesture). Defaults match services/gateway for a host run; compose overrides host/port.
    mqtt_enabled: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = 11883
    mqtt_topic_prefix: str = "hgw/v1"
    mqtt_keepalive_s: int = 30
    schema_version: str = "1.0.0"
    max_frame_bytes: int = 2 * 1024 * 1024

    # Publish landmarks to gesture. Off → results are only available via POST /v1/detect.
    publish_enabled: bool = True

    metrics_window: int = 1000
    metrics_log_interval_s: float = 10.0
    log_level: str = "info"

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            model_path=os.getenv("MODEL_PATH", d.model_path),
            num_hands=int(os.getenv("MAX_NUM_HANDS", d.num_hands)),
            min_hand_detection_confidence=float(os.getenv("MIN_HAND_DETECTION_CONFIDENCE", d.min_hand_detection_confidence)),
            min_hand_presence_confidence=float(os.getenv("MIN_HAND_PRESENCE_CONFIDENCE", d.min_hand_presence_confidence)),
            min_tracking_confidence=float(os.getenv("MIN_TRACKING_CONFIDENCE", d.min_tracking_confidence)),
            swap_handedness=_env_bool("SWAP_HANDEDNESS", d.swap_handedness),
            max_sessions=int(os.getenv("MAX_SESSIONS", d.max_sessions)),
            mqtt_enabled=_env_bool("MQTT_ENABLED", d.mqtt_enabled),
            mqtt_host=os.getenv("MQTT_HOST", d.mqtt_host),
            mqtt_port=int(os.getenv("MQTT_PORT", d.mqtt_port)),
            mqtt_topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", d.mqtt_topic_prefix).strip("/"),
            mqtt_keepalive_s=int(os.getenv("MQTT_KEEPALIVE_S", d.mqtt_keepalive_s)),
            schema_version=os.getenv("SCHEMA_VERSION", d.schema_version),
            max_frame_bytes=int(os.getenv("MAX_FRAME_BYTES", d.max_frame_bytes)),
            publish_enabled=_env_bool("PUBLISH_ENABLED", d.publish_enabled),
            metrics_window=int(os.getenv("METRICS_WINDOW", d.metrics_window)),
            metrics_log_interval_s=float(os.getenv("METRICS_LOG_INTERVAL_S", d.metrics_log_interval_s)),
            log_level=os.getenv("LOG_LEVEL", d.log_level),
        )
