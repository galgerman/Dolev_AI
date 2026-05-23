"""PlaywrightSource — scrapes X.com using a persistent logged-in browser profile."""
from __future__ import annotations

import asyncio
import pathlib
import random
import re
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright, BrowserContext, Page

from dolev_ai.models import RawTweet
from dolev_ai.sources import TweetSource

# ── X DOM selectors — update here if X changes the DOM ──────────────────────
SEL_TWEET_ARTICLE = 'article[data-testid="tweet"]'
SEL_TWEET_TEXT = '[data-testid="tweetText"]'
SEL_LIKE_COUNT = '[data-testid="like"] span[data-testid="app-text-transition-container"]'
SEL_RETWEET_COUNT = '[data-testid="retweet"] span[data-testid="app-text-transition-container"]'
SEL_REPLY_COUNT = '[data-testid="reply"] span[data-testid="app-text-transition-container"]'
SEL_TIMESTAMP = "time"


def _parse_count(text: str) -> int:
    """Convert '1.2K' / '3M' / '42' to int."""
    text = text.strip().upper().replace(",", "")
    if not text:
        return 0
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1_000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        return int(text)
    except ValueError:
        return 0


def _extract_tweet_id(url: str) -> str:
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else url


class PlaywrightSource(TweetSource):
    def __init__(
        self,
        profile_dir: str | pathlib.Path,
        headless: bool = False,
        delay_min: float = 3.0,
        delay_max: float = 6.0,
        scroll_count: int = 3,
    ) -> None:
        self._profile_dir = str(profile_dir)
        self._headless = headless
        self._delay_min = delay_min
        self._delay_max = delay_max
        self._scroll_count = scroll_count
        self._context: BrowserContext | None = None
        self._playwright = None

    @staticmethod
    def _find_browser() -> str:
        import os
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        raise RuntimeError("Install Microsoft Edge or Google Chrome.")

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            self._profile_dir,
            executable_path=self._find_browser(),
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    async def _delay(self) -> None:
        await asyncio.sleep(random.uniform(self._delay_min, self._delay_max))

    async def _parse_tweets_on_page(
        self, page: Page, since: datetime
    ) -> list[RawTweet]:
        tweets: list[RawTweet] = []
        seen_ids: set[str] = set()

        for _ in range(self._scroll_count + 1):
            articles = await page.query_selector_all(SEL_TWEET_ARTICLE)
            for article in articles:
                try:
                    # timestamp + URL
                    time_el = await article.query_selector(SEL_TIMESTAMP)
                    if not time_el:
                        continue
                    dt_str = await time_el.get_attribute("datetime")
                    if not dt_str:
                        continue
                    created_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    if created_at < since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since:
                        continue

                    # tweet link → id + author
                    link_el = await article.query_selector(f'a[href*="/status/"]')
                    if not link_el:
                        continue
                    href = await link_el.get_attribute("href") or ""
                    tweet_id = _extract_tweet_id(href)
                    if tweet_id in seen_ids:
                        continue
                    seen_ids.add(tweet_id)

                    author_match = re.match(r"/([^/]+)/status/", href)
                    author = author_match.group(1) if author_match else "unknown"
                    url = f"https://x.com{href}"

                    # text
                    text_el = await article.query_selector(SEL_TWEET_TEXT)
                    text = (await text_el.inner_text()) if text_el else ""

                    # engagement counts
                    async def _count(sel: str) -> int:
                        el = await article.query_selector(sel)
                        if not el:
                            return 0
                        return _parse_count(await el.inner_text())

                    tweets.append(RawTweet(
                        id=tweet_id,
                        author=author,
                        text=text,
                        created_at=created_at.replace(tzinfo=None),
                        like_count=await _count(SEL_LIKE_COUNT),
                        retweet_count=await _count(SEL_RETWEET_COUNT),
                        reply_count=await _count(SEL_REPLY_COUNT),
                        url=url,
                    ))
                except Exception:
                    continue

            # scroll down
            await page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
            await self._delay()

        return tweets

    async def fetch_user_timeline(
        self, handle: str, since: datetime
    ) -> list[RawTweet]:
        assert self._context, "Call start() first"
        page = await self._context.new_page()
        try:
            await page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=15000)
            await self._delay()
            return await self._parse_tweets_on_page(page, since)
        finally:
            await page.close()

    async def search_query(
        self, query: str, max_tweets: int = 15, lookback_hours: int = 6
    ) -> list[RawTweet]:
        """Run a live X search and return matching tweets (any author).

        Used for ad-hoc investigations — e.g. when TradingView shows a ticker
        spiking but our Twitter universe is silent on it.
        """
        assert self._context, "Call start() first"
        from urllib.parse import quote
        since = datetime.utcnow() - timedelta(hours=lookback_hours)
        page = await self._context.new_page()
        try:
            url = f"https://x.com/search?q={quote(query)}&f=live"
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await self._delay()
            tweets = await self._parse_tweets_on_page(page, since)
            return tweets[:max_tweets]
        finally:
            await page.close()

    async def fetch_feed_engagements(
        self, handles: list[str], since: datetime
    ) -> list[RawTweet]:
        """Fetch tweets+replies from each handle's 'with_replies' tab."""
        assert self._context, "Call start() first"
        all_tweets: list[RawTweet] = []
        for handle in handles:
            page = await self._context.new_page()
            try:
                await page.goto(
                    f"https://x.com/{handle}/with_replies",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await self._delay()
                tweets = await self._parse_tweets_on_page(page, since)
                all_tweets.extend(tweets)
            except Exception:
                pass
            finally:
                await page.close()
            await self._delay()
        return all_tweets
