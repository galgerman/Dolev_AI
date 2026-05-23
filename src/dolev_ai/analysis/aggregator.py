"""Per-ticker + per-theme aggregation from LLM extractions.

New scoring (replaces FinBERT + regex sentiment):

    direct_score(ticker) = Σ extractions mentioning ticker:
        sign(sentiment) × confidence × credibility(author) × engagement × decay

    theme_score(theme)   = same formula, summed over theme mentions

    cascade(ticker)      = Σ themes containing ticker:
        theme_score × theme.tickers[ticker] × CASCADE_FACTOR

    final_score(ticker)  = direct_score + cascade
"""
from __future__ import annotations

import logging
import math
import pathlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

import yaml

from dolev_ai.analysis.credibility import credibility
from dolev_ai.models import MovementSnapshot, RawTweet, ThemeScore, TickerScore

logger = logging.getLogger(__name__)

_SENTIMENT_SIGN = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
_HALF_LIFE_MINUTES = 30.0
CASCADE_FACTOR = 0.5

THEMES_YAML = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "themes.yaml"


@lru_cache(maxsize=1)
def _themes_config() -> dict[str, dict]:
    if not THEMES_YAML.exists():
        return {}
    with open(THEMES_YAML) as f:
        return yaml.safe_load(f) or {}


def _time_decay(tweet_time: datetime, now: datetime) -> float:
    delta_minutes = max(0.0, (now - tweet_time).total_seconds() / 60.0)
    return math.exp(-delta_minutes * math.log(2) / _HALF_LIFE_MINUTES)


def _engagement_weight(tweet: RawTweet) -> float:
    return math.log1p(tweet.like_count + 2 * tweet.retweet_count)


@dataclass
class GraphEdge:
    """A single edge in the trust graph, with the source tweet for hover-to-reveal."""
    from_id: str          # "acct:handle" or "theme:key"
    to_id: str            # "ticker:NVDA" or "theme:key"
    edge_type: str        # "acct_ticker" | "acct_theme" | "theme_ticker"
    weight: float
    sentiment: str        # "positive" | "negative" | "neutral"
    tweet_id: str | None  # source tweet (None for theme_ticker static edges)
    tweet_url: str = ""


@dataclass
class ExtractionRecord:
    """Lightweight view of an extraction joined with its tweet, used by aggregate()."""
    tweet: RawTweet
    is_finance: bool
    overall_sentiment: str
    tickers: list[tuple[str, str, float, bool]]  # (ticker, sentiment, confidence, explicit)
    themes: list[tuple[str, str, float]]         # (theme, sentiment, confidence)


def _confirmation_factor(twitter_score: float, pct_change: float,
                         confirmation_weight: float, strong_move_pct: float) -> float:
    """Multiplier in [1 - weight, 1 + weight] that boosts confirmed sentiment
    and damps contradicting sentiment. 1.0 = no adjustment."""
    if twitter_score == 0 or pct_change == 0 or strong_move_pct <= 0:
        return 1.0
    alignment = 1.0 if (twitter_score > 0) == (pct_change > 0) else -1.0
    intensity = min(1.0, abs(pct_change) / strong_move_pct)
    return 1.0 + alignment * confirmation_weight * intensity


def aggregate(
    records: list[ExtractionRecord],
    window_minutes: int = 60,
    threshold: float = 5.0,
    now: datetime | None = None,
    latest_movements: dict[str, MovementSnapshot] | None = None,
    confirmation_weight: float = 0.5,
    strong_move_pct: float = 5.0,
    discovery_weight: float = 0.5,
) -> tuple[dict[str, TickerScore], dict[str, ThemeScore], list[GraphEdge]]:
    """Compute per-ticker + per-theme scores. Returns (ticker_scores, theme_scores, edges).

    If `latest_movements` is supplied, applies a confirmation multiplier to Twitter
    scores and folds in discovery-only tickers (movers with no Twitter data).
    """
    if now is None:
        now = datetime.utcnow()
    window_start = now - timedelta(minutes=window_minutes)
    themes_cfg = _themes_config()

    in_window = [r for r in records if r.tweet.created_at >= window_start]
    if not in_window and not (latest_movements or {}):
        return {}, {}, []

    # ── Direct ticker scoring ────────────────────────────────────────────
    direct: dict[str, float] = defaultdict(float)
    ticker_voices: dict[str, set[str]] = defaultdict(set)
    ticker_tweet_counts: dict[str, int] = defaultdict(int)
    ticker_top_urls: dict[str, list[tuple[float, str]]] = defaultdict(list)

    # ── Theme scoring ───────────────────────────────────────────────────
    theme_score: dict[str, float] = defaultdict(float)
    theme_voices: dict[str, set[str]] = defaultdict(set)
    theme_tweet_counts: dict[str, int] = defaultdict(int)

    edges: list[GraphEdge] = []

    for r in in_window:
        tw = r.tweet
        cred = credibility(tw.author)
        eng = _engagement_weight(tw)
        decay = _time_decay(tw.created_at, now)

        for ticker, sent, conf, explicit in r.tickers:
            sign = _SENTIMENT_SIGN.get(sent, 0.0)
            contrib = sign * conf * cred * eng * decay
            if sign != 0.0:
                direct[ticker] += contrib
            ticker_voices[ticker].add(tw.author.lower())
            ticker_tweet_counts[ticker] += 1
            ticker_top_urls[ticker].append((abs(contrib), tw.url))
            edges.append(GraphEdge(
                from_id=f"acct:{tw.author.lower()}",
                to_id=f"ticker:{ticker}",
                edge_type="acct_ticker",
                weight=round(abs(contrib), 4),
                sentiment=sent,
                tweet_id=tw.id,
                tweet_url=tw.url,
            ))

        for theme, sent, conf in r.themes:
            sign = _SENTIMENT_SIGN.get(sent, 0.0)
            contrib = sign * conf * cred * eng * decay
            if sign != 0.0:
                theme_score[theme] += contrib
            theme_voices[theme].add(tw.author.lower())
            theme_tweet_counts[theme] += 1
            edges.append(GraphEdge(
                from_id=f"acct:{tw.author.lower()}",
                to_id=f"theme:{theme}",
                edge_type="acct_theme",
                weight=round(abs(contrib), 4),
                sentiment=sent,
                tweet_id=tw.id,
                tweet_url=tw.url,
            ))

    # ── Cascade theme → ticker ───────────────────────────────────────────
    cascaded: dict[str, float] = defaultdict(float)
    for theme, tscore in theme_score.items():
        ticker_weights = (themes_cfg.get(theme, {}) or {}).get("tickers", {}) or {}
        for ticker, weight in ticker_weights.items():
            if not weight:
                continue
            boost = tscore * weight * CASCADE_FACTOR
            cascaded[ticker] += boost
            edges.append(GraphEdge(
                from_id=f"theme:{theme}",
                to_id=f"ticker:{ticker}",
                edge_type="theme_ticker",
                weight=round(abs(boost), 4),
                sentiment=("positive" if boost > 0 else "negative" if boost < 0 else "neutral"),
                tweet_id=None,
            ))

    # ── Combine into TickerScore objects ─────────────────────────────────
    movements = latest_movements or {}
    ticker_scores: dict[str, TickerScore] = {}
    all_tickers = set(direct) | set(cascaded) | set(ticker_voices)
    for t in all_tickers:
        twitter_score = direct.get(t, 0.0) + cascaded.get(t, 0.0)
        mv = movements.get(t)
        factor = 1.0
        movement_pct: float | None = None
        if mv is not None:
            movement_pct = mv.pct_change
            factor = _confirmation_factor(
                twitter_score, mv.pct_change, confirmation_weight, strong_move_pct,
            )
        final = twitter_score * factor
        sorted_urls = [u for _, u in sorted(ticker_top_urls.get(t, []), reverse=True)]
        ticker_scores[t] = TickerScore(
            ticker=t,
            score=round(final, 4),
            unique_credible_voices=len(ticker_voices.get(t, set())),
            tweet_count=ticker_tweet_counts.get(t, 0),
            window_start=window_start,
            window_end=now,
            top_tweet_urls=sorted_urls[:10],
            threshold_progress=round(abs(final) / threshold, 4) if threshold > 0 else 0.0,
            direct_score=round(direct.get(t, 0.0), 4),
            cascade_score=round(cascaded.get(t, 0.0), 4),
            confirmation_factor=round(factor, 4),
            movement_pct=round(movement_pct, 4) if movement_pct is not None else None,
        )

    # ── Discovery: movers with no Twitter data ──────────────────────────
    for t, mv in movements.items():
        if t in ticker_scores:
            continue
        disc_score = mv.pct_change * discovery_weight
        ticker_scores[t] = TickerScore(
            ticker=t,
            score=round(disc_score, 4),
            unique_credible_voices=0,
            tweet_count=0,
            window_start=window_start,
            window_end=now,
            top_tweet_urls=[],
            threshold_progress=round(abs(disc_score) / threshold, 4) if threshold > 0 else 0.0,
            direct_score=0.0,
            cascade_score=0.0,
            confirmation_factor=1.0,
            movement_pct=round(mv.pct_change, 4),
        )

    theme_scores: dict[str, ThemeScore] = {}
    for theme, score in theme_score.items():
        theme_scores[theme] = ThemeScore(
            theme=theme,
            score=round(score, 4),
            voices=len(theme_voices.get(theme, set())),
            tweet_count=theme_tweet_counts.get(theme, 0),
            window_start=window_start,
            window_end=now,
        )

    return ticker_scores, theme_scores, edges
