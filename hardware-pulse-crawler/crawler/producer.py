"""Redis producer for HardwarePulse crawler."""
from __future__ import annotations

import json
from typing import Any, Dict

import redis
from loguru import logger

from crawler.config import REDIS_DB, REDIS_HOST, REDIS_PORT, REDIS_QUEUE_NAME


class RedisProducer:
    """Pushes raw listing tasks to Redis list queue."""

    def __init__(self) -> None:
        self._client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

    def push_task(self, platform: str, raw_data_dict: Dict[str, Any]) -> None:
        payload = {
            "source": platform,
            "raw_data": raw_data_dict,
            "meta": {"priority": "LOW"},
        }
        message = json.dumps(payload, ensure_ascii=False)
        self._client.rpush(REDIS_QUEUE_NAME, message)
        logger.info("Queued raw listing: {}", raw_data_dict.get("external_id"))
