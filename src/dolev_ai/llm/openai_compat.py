"""OpenAI-compatible provider — covers Ollama (:11434/v1) and LM Studio (:1234/v1)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from dolev_ai.llm import Extraction, LLMProvider, TickerMention, ThemeMention
from dolev_ai.llm.prompt import batch_user_message, system_prompt, theme_keys

logger = logging.getLogger(__name__)

_VALID_SENTIMENTS = {"positive", "negative", "neutral"}


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_s)
        self._max_retries = max_retries
        self._valid_themes = set(theme_keys())

    async def aclose(self) -> None:
        await self._client.aclose()

    async def healthcheck(self) -> bool:
        try:
            r = await self._client.get("/models")
            if r.status_code != 200:
                return False
            models = [m.get("id", "") for m in r.json().get("data", [])]
            return any(m == self.model or m.startswith(self.model.split(":")[0]) for m in models)
        except Exception as e:
            logger.debug(f"healthcheck failed: {e}")
            return False

    async def extract_batch(self, tweets: list) -> list[Extraction]:
        """Process tweets one-at-a-time in parallel.

        Small local models reliably handle single-tweet prompts but fail
        on batched ones (often returning only the first item). Going single
        with bounded parallelism is both more accurate AND keeps throughput
        high through asyncio.gather.
        """
        if not tweets:
            return []
        results = await asyncio.gather(*[self._extract_one(t) for t in tweets])
        return list(results)

    async def _extract_one(self, tweet) -> Extraction:
        import json
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": json.dumps(
                    {"id": tweet.id, "author": tweet.author, "text": tweet.text},
                    ensure_ascii=False,
                )},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        t0 = time.perf_counter()
        try:
            r = await self._client.post("/chat/completions", json=payload)
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            latency_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            logger.warning(f"extract_one HTTP failure for {tweet.id}: {e}")
            return self._empty(tweet.id)

        data = self._parse_object(raw)
        if data is None and self._max_retries > 0:
            try:
                payload["messages"].append({
                    "role": "user",
                    "content": "Output was not valid JSON. Return ONLY one JSON object."
                })
                r2 = await self._client.post("/chat/completions", json=payload)
                r2.raise_for_status()
                raw = r2.json()["choices"][0]["message"]["content"]
                latency_ms = int((time.perf_counter() - t0) * 1000)
                data = self._parse_object(raw)
            except Exception as e:
                logger.warning(f"extract_one retry failed: {e}")

        if data is None:
            return self._empty(tweet.id, latency_ms=latency_ms, raw=raw)
        return self._coerce(tweet.id, data, latency_ms=latency_ms, raw=raw)

    @staticmethod
    def _parse_object(raw: str) -> dict | None:
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None
        if isinstance(data, dict):
            # Common wrapper: { "result": {...} }
            for k in ("result", "extraction", "data"):
                if isinstance(data.get(k), dict):
                    return data[k]
            return data
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return None

    @staticmethod
    def _parse_array(raw: str) -> list | None:
        raw = (raw or "").strip()
        # Some models wrap JSON in code fences despite instructions
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        # Find first '[' if model wrapped it in {"results": [...]}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Heuristic: locate the outermost array
            start = raw.find("[")
            end = raw.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Common wrapper keys
            for k in ("results", "extractions", "data", "items"):
                if isinstance(data.get(k), list):
                    return data[k]
            return [data]  # single-object response
        return None

    def _coerce(self, post_id: str, item: dict, *, latency_ms: int, raw: str) -> Extraction:
        tickers: list[TickerMention] = []
        for t in (item.get("tickers") or [])[:5]:
            if not isinstance(t, dict):
                continue
            ticker = str(t.get("ticker", "")).strip().upper().lstrip("$")
            if not ticker or not ticker.isalpha() or len(ticker) > 5:
                continue
            sent = t.get("sentiment")
            if sent not in _VALID_SENTIMENTS:
                continue
            tickers.append(TickerMention(
                ticker=ticker,
                sentiment=sent,
                confidence=float(t.get("confidence", 0.5) or 0.5),
                explicit=bool(t.get("explicit", False)),
            ))

        themes: list[ThemeMention] = []
        for th in (item.get("themes") or []):
            if not isinstance(th, dict):
                continue
            key = str(th.get("theme", "")).strip()
            if key not in self._valid_themes:
                continue
            sent = th.get("sentiment")
            if sent not in _VALID_SENTIMENTS:
                continue
            themes.append(ThemeMention(
                theme=key,
                sentiment=sent,
                confidence=float(th.get("confidence", 0.5) or 0.5),
            ))

        overall = item.get("overall_sentiment") if item.get("overall_sentiment") in _VALID_SENTIMENTS else "neutral"
        return Extraction(
            post_id=post_id,
            is_finance=bool(item.get("is_finance", bool(tickers or themes))),
            tickers=tickers,
            themes=themes,
            overall_sentiment=overall,
            summary=str(item.get("summary", ""))[:240],
            model=self.model,
            latency_ms=latency_ms,
            raw_json=raw,
        )

    def _empty(self, post_id: str, latency_ms: int = 0, raw: str = "") -> Extraction:
        return Extraction(
            post_id=post_id,
            is_finance=False,
            tickers=[],
            themes=[],
            overall_sentiment="neutral",
            summary="",
            model=self.model,
            latency_ms=latency_ms,
            raw_json=raw,
        )
