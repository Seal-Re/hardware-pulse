"""Abstract base class for all HardwarePulse spiders."""
from __future__ import annotations

import abc
from typing import List, Dict, Any

from crawler.producer import RedisProducer


class BaseSpider(abc.ABC):
    """Every spider must implement ``crawl``."""

    PLATFORM: str = "UNKNOWN"

    def __init__(self, producer: RedisProducer) -> None:
        self.producer = producer

    @abc.abstractmethod
    async def crawl(self, keyword: str) -> List[Dict[str, Any]]:
        """Crawl listings for *keyword* and return raw dicts.

        Each dict follows the Phase-1 ``raw_data`` contract.
        The implementation should also call ``self.producer.push_task``
        for every item it extracts.
        """

    def _push(self, raw_data: Dict[str, Any]) -> None:
        """Convenience wrapper that enforces platform tag."""
        self.producer.push_task(self.PLATFORM, raw_data)
