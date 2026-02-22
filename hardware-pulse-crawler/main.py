"""HardwarePulse Crawler – CLI entry point.

Usage:
    python main.py "RTX 4060"

NOTE:
- This file is kept for backwards compatibility with the original Playwright spider.
- The new Termux bare-metal feeder entry is `crawler_wg_xianyu.py` (uiautomator2 + Redis ZSET scheduler).
"""
from __future__ import annotations

import asyncio
import sys

from loguru import logger

from crawler.producer import RedisProducer
from crawler.spiders.xianyu import XianyuSpider


async def run(keyword: str) -> None:
    producer = RedisProducer()
    spider = XianyuSpider(producer)

    logger.info("Starting Xianyu crawl for keyword: '{}'", keyword)
    results = await spider.crawl(keyword)
    logger.info("Crawl finished. Total items pushed to queue: {}", len(results))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py <keyword>")
        print('Example: python main.py "RTX 4060"')
        sys.exit(1)

    keyword = sys.argv[1]
    asyncio.run(run(keyword))


if __name__ == "__main__":
    main()
