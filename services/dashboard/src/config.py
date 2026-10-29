"""Dashboard settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mqtt_host: str = "localhost"
    mqtt_port: int = 11883
    mqtt_topic_prefix: str = "hgw/v1"
    # Vision's own per-stage timings (decode / inference / publish). Empty = don't poll.
    vision_metrics_url: str = "http://vision:8001/metrics"
    vision_poll_s: float = 2.0
    settle_ms: float = 2000.0       # how long a frame may wait for its later stages
    push_interval_s: float = 1.0    # live update period for browsers
    log_level: str = "info"

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            mqtt_host=os.getenv("MQTT_HOST", d.mqtt_host),
            mqtt_port=int(os.getenv("MQTT_PORT", d.mqtt_port)),
            mqtt_topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", d.mqtt_topic_prefix).strip("/"),
            vision_metrics_url=os.getenv("VISION_METRICS_URL", d.vision_metrics_url),
            vision_poll_s=float(os.getenv("VISION_POLL_S", d.vision_poll_s)),
            settle_ms=float(os.getenv("SETTLE_MS", d.settle_ms)),
            push_interval_s=float(os.getenv("PUSH_INTERVAL_S", d.push_interval_s)),
            log_level=os.getenv("LOG_LEVEL", d.log_level),
        )
