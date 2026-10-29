import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mqtt_host: str = "localhost"
    mqtt_port: int = 11883
    mqtt_prefix: str = "hgw/v1"
    timeout: float = 3.0
    max_frame_bytes: int = 2 * 1024 * 1024
    max_sessions: int = 16
    schema_version: str = "1.0.0"
    public_url: str = ""

    @classmethod
    def from_env(cls):
        defaults = cls()
        settings = cls(
            mqtt_host=os.getenv("MQTT_HOST", defaults.mqtt_host),
            mqtt_port=int(os.getenv("MQTT_PORT", defaults.mqtt_port)),
            mqtt_prefix=os.getenv("MQTT_TOPIC_PREFIX", defaults.mqtt_prefix).strip("/"),
            timeout=float(os.getenv("GATEWAY_TIMEOUT", defaults.timeout)),
            max_frame_bytes=int(os.getenv("GATEWAY_MAX_FRAME_BYTES", defaults.max_frame_bytes)),
            max_sessions=int(os.getenv("GATEWAY_MAX_SESSIONS", defaults.max_sessions)),
            schema_version=os.getenv("SCHEMA_VERSION", defaults.schema_version),
            public_url=os.getenv("GATEWAY_PUBLIC_URL", defaults.public_url).rstrip("/"),
        )
        if settings.timeout <= 0 or settings.max_sessions <= 0 or settings.max_frame_bytes <= 0:
            raise ValueError("Gateway limits must be positive")
        if not settings.mqtt_prefix or any(c in settings.mqtt_prefix for c in "+#\0"):
            raise ValueError("Invalid MQTT_TOPIC_PREFIX")
        return settings
