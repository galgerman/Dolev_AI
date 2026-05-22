"""Anthropic provider — Claude Haiku for cloud-fallback extraction.

Stub for now. Implements the same shape as OpenAICompatProvider so it can be
swapped in via factory once Phase 2 needs cloud capacity. Production use
requires ANTHROPIC_API_KEY env var.
"""
from __future__ import annotations

import json
import logging
import os
import time

from dolev_ai.llm import Extraction, LLMProvider
from dolev_ai.llm.openai_compat import OpenAICompatProvider  # reuse _coerce / _parse_array
from dolev_ai.llm.prompt import batch_user_message, system_prompt

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str = "claude-haiku-4-5-20251001", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = None  # lazy-init the SDK client so import doesn't fail without the key
        # Borrow coercion helpers from openai_compat
        self._coercer = OpenAICompatProvider.__new__(OpenAICompatProvider)
        self._coercer.model = model
        from dolev_ai.llm.prompt import theme_keys
        self._coercer._valid_themes = set(theme_keys())

    def _client_lazy(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
            except ImportError as e:
                raise RuntimeError("anthropic SDK not installed") from e
        return self._client

    async def healthcheck(self) -> bool:
        return bool(self._api_key) and not self._api_key.endswith("dummy")

    async def extract_batch(self, tweets: list) -> list[Extraction]:
        if not tweets:
            return []
        if not await self.healthcheck():
            return [self._coercer._empty(t.id) for t in tweets]

        client = self._client_lazy()
        t0 = time.perf_counter()
        try:
            resp = await client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system_prompt(),
                messages=[{"role": "user", "content": batch_user_message(tweets)}],
            )
            raw = "".join(block.text for block in resp.content if hasattr(block, "text"))
            latency_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            logger.warning(f"anthropic extract_batch failed: {e}")
            return [self._coercer._empty(t.id) for t in tweets]

        parsed = OpenAICompatProvider._parse_array(raw)
        if parsed is None:
            return [self._coercer._empty(t.id, latency_ms=latency_ms, raw=raw) for t in tweets]

        by_id: dict[str, dict] = {}
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            pid = item.get("post_id") or (tweets[i].id if i < len(tweets) else None)
            if pid:
                by_id[pid] = item

        results: list[Extraction] = []
        for tw in tweets:
            item = by_id.get(tw.id)
            if item is None:
                results.append(self._coercer._empty(tw.id, latency_ms=latency_ms, raw=raw))
            else:
                results.append(self._coercer._coerce(tw.id, item, latency_ms=latency_ms, raw=raw))
        return results
