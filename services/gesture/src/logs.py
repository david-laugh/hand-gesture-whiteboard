"""JSON 한 줄 로그."""
import json
import logging

from . import config

logger = logging.getLogger(config.SERVICE_NAME)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.propagate = False


def log_json(level: int = logging.INFO, **fields) -> None:
    logger.log(level, json.dumps({"service": config.SERVICE_NAME, **fields}, ensure_ascii=False))
