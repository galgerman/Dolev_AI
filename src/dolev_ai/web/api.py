"""REST API endpoints for the monitoring dashboard."""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta
from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dolev_ai.analysis.credibility import credibility as get_credibility
from dolev_ai.db import AlertLogRow, SignalRow, TickerScoreRow, TweetRow
from dolev_ai.web.schemas import (
    AccountOut,
    DrilldownAccountContrib,
    DrilldownTweetOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphSnapshotOut,
    HealthOut,
    SignalOut,
    TickerDrilldownOut,
    TickerScoreOut,
)

router = APIRouter(prefix="/api")

SEEDS_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "seeds.yaml"

# These are set by server.py after the agent starts
_started_at: datetime = datetime.utcnow()
_threshold: float = 5.0
_get_session = None  # injected by server.py
_event_bus = None    # injected by server.py


def configure(started_at: datetime, threshold: float, get_session, event_bus) -> None:
    global _started_at, _threshold, _get_session, _event_bus
    _started_at = started_at
    _threshold = threshold
    _get_session = get_session
    _event_bus = event_bus


def _session() -> Session:
    with _get_session() as s:
        yield s


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthOut)
def health():
    return HealthOut(
        ok=True,
        started_at=_started_at,
        threshold=_threshold,
        subscriber_count=_event_bus.subscriber_count() if _event_bus else 0,
    )


@router.get("/tickers/live", response_model=list[TickerScoreOut])
def tickers_live(limit: int = 20, db: Session = Depends(_session)):
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    rows = (
        db.query(TickerScoreRow)
        .filter(TickerScoreRow.window_end >= one_hour_ago)
        .order_by(TickerScoreRow.window_end.desc())
        .all()
    )
    # Deduplicate: keep only the latest score per ticker
    seen: dict[str, TickerScoreRow] = {}
    for row in rows:
        if row.ticker not in seen:
            seen[row.ticker] = row
    top = sorted(seen.values(), key=lambda r: abs(r.score), reverse=True)[:limit]
    return [
        TickerScoreOut(
            ticker=r.ticker, score=r.score,
            unique_credible_voices=r.unique_credible_voices,
            tweet_count=r.tweet_count,
            window_start=r.window_start, window_end=r.window_end,
            top_tweet_urls=json.loads(r.top_tweet_urls or "[]"),
            threshold_progress=round(abs(r.score) / _threshold, 4) if _threshold > 0 else 0.0,
        )
        for r in top
    ]


@router.get("/tickers/{ticker}", response_model=TickerDrilldownOut)
def ticker_drilldown(ticker: str, db: Session = Depends(_session)):
    ticker = ticker.upper()
    four_hours_ago = datetime.utcnow() - timedelta(hours=4)

    # Score history
    history_rows = (
        db.query(TickerScoreRow)
        .filter(TickerScoreRow.ticker == ticker, TickerScoreRow.window_end >= four_hours_ago)
        .order_by(TickerScoreRow.window_end.asc())
        .all()
    )
    if not history_rows:
        raise HTTPException(status_code=404, detail=f"No data for {ticker}")

    latest = history_rows[-1]
    score_history = [(r.window_end, r.score) for r in history_rows]

    # Contributing tweets
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    tweet_rows = (
        db.query(TweetRow)
        .filter(TweetRow.created_at >= one_hour_ago, TweetRow.text.contains(f"${ticker}"))
        .order_by(TweetRow.created_at.desc())
        .limit(30)
        .all()
    )

    # Contributing accounts (from tweets)
    author_counts: dict[str, int] = {}
    for t in tweet_rows:
        author_counts[t.author.lower()] = author_counts.get(t.author.lower(), 0) + 1

    seed_handles = _load_seed_handles()
    contributing: list[DrilldownAccountContrib] = []
    for handle, count in sorted(author_counts.items(), key=lambda x: -x[1]):
        tier = seed_handles.get(handle, 3)
        contributing.append(DrilldownAccountContrib(
            handle=handle, tier=tier,
            credibility=get_credibility(handle),
            tweet_count=count,
        ))

    return TickerDrilldownOut(
        ticker=ticker,
        current_score=latest.score,
        threshold_progress=round(abs(latest.score) / _threshold, 4) if _threshold > 0 else 0.0,
        unique_credible_voices=latest.unique_credible_voices,
        score_history=score_history,
        contributing_accounts=contributing,
        recent_tweets=[
            DrilldownTweetOut(
                id=t.id, author=t.author, text=t.text,
                created_at=t.created_at, like_count=t.like_count,
                retweet_count=t.retweet_count, url=t.url,
            )
            for t in tweet_rows
        ],
    )


@router.get("/signals/recent", response_model=list[SignalOut])
def signals_recent(limit: int = 20, db: Session = Depends(_session)):
    rows = (
        db.query(SignalRow)
        .order_by(SignalRow.generated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        SignalOut(
            ticker=r.ticker, side=r.side, conviction=r.conviction,
            suggested_size_pct=r.suggested_size_pct, rationale=r.rationale,
            key_drivers=json.loads(r.key_drivers or "[]"),
            generated_at=r.generated_at,
        )
        for r in rows
    ]


@router.get("/graph/snapshot", response_model=GraphSnapshotOut)
def graph_snapshot(db: Session = Depends(_session)):
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)

    # Latest score per ticker
    score_rows = (
        db.query(TickerScoreRow)
        .filter(TickerScoreRow.window_end >= one_hour_ago)
        .order_by(TickerScoreRow.window_end.desc())
        .all()
    )
    ticker_scores: dict[str, TickerScoreRow] = {}
    for r in score_rows:
        if r.ticker not in ticker_scores:
            ticker_scores[r.ticker] = r

    # Authors active in last hour
    tweet_rows = (
        db.query(TweetRow)
        .filter(TweetRow.created_at >= one_hour_ago)
        .all()
    )

    seed_handles = _load_seed_handles()
    author_set: set[str] = {t.author.lower() for t in tweet_rows}

    nodes: list[GraphNodeOut] = []
    edges: list[GraphEdgeOut] = []

    # Account nodes
    for handle in author_set:
        tier = seed_handles.get(handle, 3)
        cred = get_credibility(handle)
        nodes.append(GraphNodeOut(
            id=f"acct:{handle}", type="account", label=f"@{handle}",
            size=cred, sentiment="neutral", tier=tier,
        ))

    # Ticker nodes
    for ticker, row in ticker_scores.items():
        sentiment = "positive" if row.score > 0 else ("negative" if row.score < 0 else "neutral")
        nodes.append(GraphNodeOut(
            id=f"ticker:{ticker}", type="ticker", label=f"${ticker}",
            size=min(1.0, abs(row.score) / (_threshold * 2)),
            sentiment=sentiment, tier=0,
        ))

    # Edges: author → ticker via tweet text
    for tweet in tweet_rows:
        if f"${tweet.text}" in tweet.text.upper():
            continue
        # Check which tickers this tweet mentions
        for ticker in ticker_scores:
            if f"${ticker}" in tweet.text.upper():
                cred = get_credibility(tweet.author)
                edges.append(GraphEdgeOut(
                    source=f"acct:{tweet.author.lower()}",
                    target=f"ticker:{ticker}",
                    weight=round(cred, 3),
                    sentiment="neutral",
                ))

    return GraphSnapshotOut(nodes=nodes, edges=edges)


@router.get("/accounts", response_model=list[AccountOut])
def accounts():
    seed_handles = _load_seed_handles()
    result = []
    for handle, tier in seed_handles.items():
        result.append(AccountOut(
            handle=handle, tier=tier,
            credibility=get_credibility(handle),
        ))
    return sorted(result, key=lambda a: (a.tier, a.handle))


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_seed_handles() -> dict[str, int]:
    if not SEEDS_PATH.exists():
        return {}
    with open(SEEDS_PATH) as f:
        data = yaml.safe_load(f)
    return {
        a["handle"].lstrip("@").lower(): int(a.get("tier", 3))
        for a in data.get("accounts", [])
    }
