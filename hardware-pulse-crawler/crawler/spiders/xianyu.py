"""Xianyu (闲鱼) spider – Playwright async implementation."""
from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from loguru import logger
from playwright.async_api import async_playwright, Page, TimeoutError as PwTimeout

from crawler.config import CRAWLER_HEADLESS, DEFAULT_TIMEOUT_MS, USER_AGENTS
from crawler.producer import RedisProducer
from crawler.spiders.base import BaseSpider


class XianyuSpider(BaseSpider):
    """Scrape Xianyu search results for a given keyword."""

    PLATFORM = "XIANYU"
    SEARCH_URL = "https://www.goofish.com/search?q={keyword}"

    def __init__(self, producer: RedisProducer) -> None:
        super().__init__(producer)

    async def crawl(self, keyword: str) -> List[Dict[str, Any]]:
        """Launch browser, search *keyword*, extract raw listings."""
        results: List[Dict[str, Any]] = []
        url = self.SEARCH_URL.format(keyword=keyword)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=CRAWLER_HEADLESS)
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            try:
                logger.info("Navigating to {}", url)
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=DEFAULT_TIMEOUT_MS)

                # Wait for item cards to appear
                await page.wait_for_selector(
                    "[class*='item'] , [class*='feeds'] , [class*='card']",
                    timeout=DEFAULT_TIMEOUT_MS,
                )

                # Allow dynamic content to settle
                await page.wait_for_timeout(3000)

                results = await self._extract_items(page)
                logger.info("Extracted {} items for keyword '{}'",
                            len(results), keyword)

            except PwTimeout:
                logger.warning("Timeout while crawling keyword '{}'", keyword)
            except Exception as exc:
                logger.error("Unexpected error: {}", exc)
            finally:
                await browser.close()

        return results

    async def _extract_items(self, page: Page) -> List[Dict[str, Any]]:
        """Parse item cards from the current page."""
        items: List[Dict[str, Any]] = []

        # Try multiple possible selectors (Xianyu DOM changes often)
        card_selectors = [
            "[class*='ItemCard']",
            "[class*='item-card']",
            "[class*='feed-item']",
            "[class*='search-item']",
            "[class*='Card--']",
        ]

        cards = []
        for sel in card_selectors:
            cards = await page.query_selector_all(sel)
            if cards:
                logger.debug("Matched selector: {} ({} cards)", sel, len(cards))
                break

        if not cards:
            logger.warning("No item cards found – DOM selectors may need updating")
            return items

        for card in cards:
            try:
                raw_data = await self._parse_card(card)
                if raw_data:
                    self._push(raw_data)
                    items.append(raw_data)
            except Exception as exc:
                logger.debug("Skipping card due to error: {}", exc)

        return items

    async def _parse_card(self, card) -> Dict[str, Any] | None:
        """Extract raw fields from a single item card element."""
        # --- outer HTML snapshot (critical for LLM layer) ---
        raw_html = await card.evaluate("el => el.outerHTML")

        # --- title ---
        title_el = await card.query_selector(
            "[class*='title'], [class*='Title'], h3, h4"
        )
        raw_title = (await title_el.inner_text()).strip() if title_el else ""

        if not raw_title:
            return None

        # --- price ---
        price_el = await card.query_selector(
            "[class*='price'], [class*='Price']"
        )
        price_text = (await price_el.inner_text()).strip() if price_el else "0"
        raw_price = self._extract_price(price_text)

        # --- external id ---
        link_el = await card.query_selector("a[href]")
        href = await link_el.get_attribute("href") if link_el else ""
        external_id = self._extract_id(href) or f"xy-{hash(raw_title) & 0xFFFFFFFF:08x}"

        # --- seller info ---
        seller_el = await card.query_selector(
            "[class*='seller'], [class*='Seller'], [class*='user'], [class*='User']"
        )
        seller_name = (await seller_el.inner_text()).strip() if seller_el else ""

        location_el = await card.query_selector(
            "[class*='location'], [class*='Location'], [class*='area']"
        )
        location = (await location_el.inner_text()).strip() if location_el else ""

        now_iso = datetime.now(timezone.utc).isoformat()

        return {
            "platform": self.PLATFORM,
            "external_id": external_id,
            "raw_title": raw_title,
            "raw_price": raw_price,
            "seller_info": {"name": seller_name, "location": location},
            "raw_html_snapshot": raw_html,
            "crawled_at": now_iso,
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_price(text: str) -> float:
        """Pull the first numeric value out of a price string."""
        match = re.search(r"[\d]+(?:\.[\d]+)?", text.replace(",", ""))
        return float(match.group()) if match else 0.0

    @staticmethod
    def _extract_id(href: str) -> str:
        """Try to pull a unique item id from a URL path."""
        if not href:
            return ""
        match = re.search(r"/(?:item|detail)[/\-]?(\w+)", href)
        return match.group(1) if match else ""
