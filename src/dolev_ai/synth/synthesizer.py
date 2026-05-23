"""Ollama-powered synthesizer: converts raw scores + tweets into a Signal."""
from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx

from dolev_ai.llm.factory import load_active_config
from dolev_ai.models import Signal, TickerScore

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a quantitative financial analyst assistant.
Given a set of tweets and a ticker's aggregated sentiment score, produce a structured trade signal.
Reply ONLY with a valid JSON object — no markdown, no explanation outside the JSON.

Required JSON schema:
{
  "side": "buy" | "sell",
  "conviction": float between 0 and 1,
  "suggested_size_pct": float between 0 and 0.1,
  "rationale": "2-4 sentences explaining the signal",
  "key_drivers": ["tweet text or headline 1", "tweet text or headline 2"]
}

Rules:
- conviction reflects how strong and consistent the signal is (0 = noise, 1 = very high confidence)
- suggested_size_pct is conservative portfolio sizing (never exceed 0.10)
- rationale must explain WHAT is driving the signal and WHY it matters
- key_drivers lists the 2-5 most important pieces of evidence (tweet snippets or headlines)
- Be sceptical; social media signals are noisy; default to moderate conviction unless evidence is overwhelming"""


def _build_user_message(
    ticker: str,
    ts: TickerScore,
    tweet_texts: list[str],
    themes: list[tuple[str, float]] | None = None,
) -> str:
    tweets_block = "\n".join(f"- {t[:280]}" for t in tweet_texts[:10])
    direct = ts.direct_score if hasattr(ts, "direct_score") else 0.0
    cascade = ts.cascade_score if hasattr(ts, "cascade_score") else 0.0
    themes_block = ""
    if themes:
        lines = [f"  - {name}: {score:+.2f}" for name, score in themes]
        themes_block = "\nActive themes for this ticker (sector context):\n" + "\n".join(lines)
    return (
        f"Ticker: ${ticker}\n"
        f"Aggregate score: {ts.score:+.2f} "
        f"({'bullish' if ts.score > 0 else 'bearish'})\n"
        f"  - direct mentions: {direct:+.2f}\n"
        f"  - theme cascade : {cascade:+.2f}\n"
        f"Credible voices: {ts.unique_credible_voices}\n"
        f"Window: last 60 minutes"
        f"{themes_block}\n\n"
        f"Top tweets driving this signal:\n{tweets_block}"
    )


def _parse_json(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


class Synthesizer:
    def __init__(self, **_kwargs) -> None:
        cfg = load_active_config()
        self._endpoint = cfg["endpoint"].rstrip("/")
        self._model = cfg["name"]
        self._timeout = float(cfg.get("timeout_s", 60))
        self._client = httpx.Client(base_url=self._endpoint, timeout=self._timeout)
        logger.info(f"Synthesizer using Ollama model={self._model} endpoint={self._endpoint}")

    def _chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        r = self._client.post("/chat/completions", json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def synthesize(
        self,
        ticker: str,
        ts: TickerScore,
        tweet_texts: list[str],
        themes: list[tuple[str, float]] | None = None,
    ) -> Signal:
        user_msg = _build_user_message(ticker, ts, tweet_texts, themes)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        for attempt in range(2):
            try:
                raw = self._chat(messages)
                data = _parse_json(raw)
                if data is None:
                    raise ValueError("null parse")
                return Signal(
                    ticker=ticker,
                    side=data["side"],
                    conviction=float(data["conviction"]),
                    suggested_size_pct=float(data["suggested_size_pct"]),
                    rationale=data["rationale"],
                    key_drivers=data.get("key_drivers", ts.top_tweet_urls[:5]),
                    generated_at=datetime.utcnow(),
                )
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                if attempt == 0:
                    messages.append({"role": "assistant", "content": raw if "raw" in dir() else ""})
                    messages.append({"role": "user", "content": "Output was not valid JSON. Return ONLY the JSON object."})
                    continue
                logger.warning(f"Synthesizer JSON parse failed for {ticker}: {e}")
            except Exception as e:
                logger.warning(f"Synthesizer HTTP error for {ticker}: {e}")
                break

        # Fallback: derive signal directly from score
        side = "buy" if ts.score > 0 else "sell"
        return Signal(
            ticker=ticker,
            side=side,
            conviction=min(1.0, abs(ts.score) / 15.0),
            suggested_size_pct=0.02,
            rationale=f"Signal based on {ts.unique_credible_voices} credible voices (synthesizer unavailable).",
            key_drivers=ts.top_tweet_urls[:5],
            generated_at=datetime.utcnow(),
        )

    def analyze_retrospective(self, position) -> str:
        """2-3 sentence post-mortem on a closed paper trade."""
        direction = "profit" if (position.pnl_pct or 0) >= 0 else "loss"
        pnl_str = f"{(position.pnl_pct or 0):+.2%}"
        prompt = (
            f"A paper trade was just closed:\n"
            f"  Ticker: ${position.ticker}\n"
            f"  Side: {position.side}\n"
            f"  Entry: ${position.entry_price:.2f}  Exit: ${position.exit_price:.2f}\n"
            f"  P&L: {pnl_str} ({direction})\n"
            f"  Held from {position.opened_at} to {position.closed_at}\n\n"
            f"In 2-3 sentences, evaluate whether this was a good trade decision. "
            f"What likely went right or wrong? What would you watch for next time? "
            f"Be concise and direct. Reply with plain text, no JSON."
        )
        try:
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "stream": False,
            }
            r = self._client.post("/chat/completions", json=payload)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Retrospective failed for {position.ticker}: {e}")
            return f"Trade closed at {pnl_str}. Retrospective unavailable."
