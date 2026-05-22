"""Claude-powered synthesizer: converts raw scores + tweets into a Signal."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import anthropic

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


class Synthesizer:
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        cache_system_prompt: bool = True,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._model = model
        self._cache = cache_system_prompt

    def synthesize(
        self,
        ticker: str,
        ts: TickerScore,
        tweet_texts: list[str],
        themes: list[tuple[str, float]] | None = None,
    ) -> Signal:
        system: list[dict] = [{"type": "text", "text": _SYSTEM_PROMPT}]
        if self._cache:
            system[0]["cache_control"] = {"type": "ephemeral"}  # type: ignore[index]

        user_msg = _build_user_message(ticker, ts, tweet_texts, themes)

        for attempt in range(2):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=512,
                    system=system,  # type: ignore[arg-type]
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw = response.content[0].text.strip()
                data = json.loads(raw)
                return Signal(
                    ticker=ticker,
                    side=data["side"],
                    conviction=float(data["conviction"]),
                    suggested_size_pct=float(data["suggested_size_pct"]),
                    rationale=data["rationale"],
                    key_drivers=data.get("key_drivers", ts.top_tweet_urls[:5]),
                    generated_at=datetime.utcnow(),
                )
            except (json.JSONDecodeError, KeyError) as e:
                if attempt == 1:
                    logger.warning(f"Synthesizer JSON parse failed for {ticker}: {e}")
                    # Return a minimal signal rather than crashing
                    side = "buy" if ts.score > 0 else "sell"
                    return Signal(
                        ticker=ticker,
                        side=side,
                        conviction=min(1.0, abs(ts.score) / 15.0),
                        suggested_size_pct=0.02,
                        rationale=f"Signal based on {ts.unique_credible_voices} credible voices; synthesizer parse error.",
                        key_drivers=ts.top_tweet_urls[:5],
                        generated_at=datetime.utcnow(),
                    )
