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
from dolev_ai.db import (
    AlertLogRow,
    ExtractedThemeRow,
    ExtractedTickerRow,
    ExtractionRow,
    GraphEdgeRow,
    SignalRow,
    ThemeScoreRow,
    TickerScoreRow,
    TweetRow,
)
from dolev_ai.web.schemas import (
    AccountOut,
    DBStatsOut,
    DrilldownAccountContrib,
    DrilldownTweetOut,
    EdgeDetailOut,
    ExtractionOut,
    GraphEdgeOut,
    GraphNodeOut,
    GraphSnapshotOut,
    HealthOut,
    LLMStatusOut,
    SignalOut,
    ThemeMentionOut,
    ThemeScoreOut,
    TickerDrilldownOut,
    TickerMentionOut,
    TickerScoreOut,
)

router = APIRouter(prefix="/api")
auth_router = APIRouter(prefix="/api/auth")

SEEDS_PATH = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "seeds.yaml"

# These are set by server.py after the agent starts
_started_at: datetime = datetime.utcnow()
_threshold: float = 5.0
_get_session = None  # injected by server.py
_event_bus = None    # injected by server.py
_agent_control = None  # optional local scraper controller


def configure(started_at: datetime, threshold: float, get_session, event_bus, agent_control=None) -> None:
    global _started_at, _threshold, _get_session, _event_bus, _agent_control
    _started_at = started_at
    _threshold = threshold
    _get_session = get_session
    _event_bus = event_bus
    _agent_control = agent_control


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

    # Latest score per theme
    theme_rows = (
        db.query(ThemeScoreRow)
        .filter(ThemeScoreRow.window_end >= one_hour_ago)
        .order_by(ThemeScoreRow.window_end.desc())
        .all()
    )
    theme_scores: dict[str, ThemeScoreRow] = {}
    for r in theme_rows:
        if r.theme not in theme_scores:
            theme_scores[r.theme] = r

    # Edges from persistent graph_edges table (recent)
    edge_rows = (
        db.query(GraphEdgeRow)
        .filter(GraphEdgeRow.created_at >= one_hour_ago)
        .order_by(GraphEdgeRow.created_at.desc())
        .limit(500)
        .all()
    )

    seed_handles = _load_seed_handles()
    active_accounts: set[str] = set()
    for e in edge_rows:
        if e.from_id.startswith("acct:"):
            active_accounts.add(e.from_id.removeprefix("acct:"))

    nodes: list[GraphNodeOut] = []
    edges: list[GraphEdgeOut] = []

    for handle in active_accounts:
        tier = seed_handles.get(handle, 3)
        cred = get_credibility(handle)
        nodes.append(GraphNodeOut(
            id=f"acct:{handle}", type="account", label=f"@{handle}",
            size=cred, sentiment="neutral", tier=tier,
        ))

    for ticker, row in ticker_scores.items():
        sentiment = "positive" if row.score > 0 else ("negative" if row.score < 0 else "neutral")
        nodes.append(GraphNodeOut(
            id=f"ticker:{ticker}", type="ticker", label=f"${ticker}",
            size=min(1.0, abs(row.score) / (_threshold * 2)),
            sentiment=sentiment, tier=0,
        ))

    for theme, row in theme_scores.items():
        sentiment = "positive" if row.score > 0 else ("negative" if row.score < 0 else "neutral")
        nodes.append(GraphNodeOut(
            id=f"theme:{theme}", type="theme", label=theme,
            size=min(1.0, abs(row.score) / (_threshold * 2)),
            sentiment=sentiment, tier=0,
        ))

    for e in edge_rows:
        edges.append(GraphEdgeOut(
            id=e.id, source=e.from_id, target=e.to_id,
            edge_type=e.edge_type, weight=e.weight,
            sentiment=e.sentiment, tweet_id=e.tweet_id,
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


def _require_agent_control():
    if _agent_control is None:
        raise HTTPException(status_code=503, detail="Agent control is not available")
    return _agent_control


@router.get("/agent/status")
def agent_status():
    control = _require_agent_control()
    return control.status()


@router.post("/agent/start")
async def agent_start():
    control = _require_agent_control()
    started = await control.start_worker()
    return {**control.status(), "started": started}


@router.post("/agent/stop")
async def agent_stop():
    control = _require_agent_control()
    stopped = await control.stop_worker()
    return {**control.status(), "stopped": stopped}


# ── Auth endpoints ───────────────────────────────────────────────────────────

@auth_router.get("/x/status")
def x_auth_status():
    from dolev_ai.web import auth as auth_mod
    return {"state": auth_mod.get_state(), "logged_in": auth_mod.is_logged_in()}


@auth_router.post("/x/login")
async def x_auth_login():
    from dolev_ai.web import auth as auth_mod
    started = await auth_mod.start_login()
    return {"started": started, "state": auth_mod.get_state()}


# ── Extraction / theme / LLM endpoints ────────────────────────────────────────

@router.get("/extractions/recent", response_model=list[ExtractionOut])
def extractions_recent(limit: int = 50, db: Session = Depends(_session)):
    rows = (
        db.query(ExtractionRow, TweetRow)
        .join(TweetRow, ExtractionRow.tweet_id == TweetRow.id)
        .order_by(ExtractionRow.created_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for ex, tw in rows:
        tickers = db.query(ExtractedTickerRow).filter(
            ExtractedTickerRow.extraction_id == ex.id).all()
        themes = db.query(ExtractedThemeRow).filter(
            ExtractedThemeRow.extraction_id == ex.id).all()
        out.append(ExtractionOut(
            id=ex.id,
            tweet_id=ex.tweet_id,
            author=tw.author,
            text=tw.text,
            url=tw.url,
            is_finance=ex.is_finance,
            overall_sentiment=ex.overall_sentiment,
            summary=ex.summary,
            tickers=[TickerMentionOut(
                ticker=t.ticker, sentiment=t.sentiment,
                confidence=t.confidence, explicit=t.explicit
            ) for t in tickers],
            themes=[ThemeMentionOut(
                theme=t.theme, sentiment=t.sentiment, confidence=t.confidence
            ) for t in themes],
            model=ex.model,
            latency_ms=ex.latency_ms,
            created_at=ex.created_at,
        ))
    return out


@router.get("/themes/active", response_model=list[ThemeScoreOut])
def themes_active(limit: int = 20, db: Session = Depends(_session)):
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    rows = (
        db.query(ThemeScoreRow)
        .filter(ThemeScoreRow.window_end >= one_hour_ago)
        .order_by(ThemeScoreRow.window_end.desc())
        .all()
    )
    seen: dict[str, ThemeScoreRow] = {}
    for r in rows:
        if r.theme not in seen:
            seen[r.theme] = r
    top = sorted(seen.values(), key=lambda r: abs(r.score), reverse=True)[:limit]
    themes_cfg = _load_themes_config()
    return [
        ThemeScoreOut(
            theme=r.theme, score=r.score, voices=r.voices,
            tweet_count=r.tweet_count,
            threshold_progress=round(abs(r.score) / _threshold, 4) if _threshold > 0 else 0.0,
            cascade_targets=list((themes_cfg.get(r.theme, {}) or {}).get("tickers", {}).keys()),
        )
        for r in top
    ]


@router.get("/themes/{theme}")
def theme_drilldown(theme: str, db: Session = Depends(_session)):
    four_hours_ago = datetime.utcnow() - timedelta(hours=4)
    history = (
        db.query(ThemeScoreRow)
        .filter(ThemeScoreRow.theme == theme, ThemeScoreRow.window_end >= four_hours_ago)
        .order_by(ThemeScoreRow.window_end.asc())
        .all()
    )
    if not history:
        raise HTTPException(404, f"No data for theme {theme}")

    # Tweets that mentioned this theme
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    rows = (
        db.query(ExtractedThemeRow, ExtractionRow, TweetRow)
        .join(ExtractionRow, ExtractedThemeRow.extraction_id == ExtractionRow.id)
        .join(TweetRow, ExtractionRow.tweet_id == TweetRow.id)
        .filter(ExtractedThemeRow.theme == theme, ExtractionRow.created_at >= one_hour_ago)
        .order_by(ExtractionRow.created_at.desc())
        .limit(30).all()
    )
    latest = history[-1]
    themes_cfg = _load_themes_config()
    return {
        "theme": theme,
        "current_score": latest.score,
        "voices": latest.voices,
        "tweet_count": latest.tweet_count,
        "score_history": [[r.window_end.isoformat(), r.score] for r in history],
        "cascade_targets": (themes_cfg.get(theme, {}) or {}).get("tickers", {}),
        "recent_tweets": [
            {
                "id": tw.id, "author": tw.author, "text": tw.text,
                "url": tw.url, "created_at": tw.created_at.isoformat(),
                "sentiment": tm.sentiment, "confidence": tm.confidence,
            }
            for tm, _ex, tw in rows
        ],
    }


@router.get("/edges/{edge_id}", response_model=EdgeDetailOut)
def edge_detail(edge_id: int, db: Session = Depends(_session)):
    row = db.query(GraphEdgeRow).filter(GraphEdgeRow.id == edge_id).first()
    if row is None:
        raise HTTPException(404, "Edge not found")
    tweet_out = None
    if row.tweet_id:
        tw = db.query(TweetRow).filter(TweetRow.id == row.tweet_id).first()
        if tw:
            tweet_out = DrilldownTweetOut(
                id=tw.id, author=tw.author, text=tw.text,
                created_at=tw.created_at, like_count=tw.like_count,
                retweet_count=tw.retweet_count, url=tw.url,
            )
    return EdgeDetailOut(
        id=row.id, from_id=row.from_id, to_id=row.to_id,
        edge_type=row.edge_type, weight=row.weight, sentiment=row.sentiment,
        tweet_id=row.tweet_id, created_at=row.created_at, tweet=tweet_out,
    )


@router.get("/llm/status", response_model=LLMStatusOut)
async def llm_status():
    if _agent_control is None:
        return LLMStatusOut(provider="none", model="none", endpoint="",
                            healthy=False, backlog=0, capacity=0, running=False)
    info = _agent_control.llm_status()
    healthy = False
    try:
        if _agent_control._llm_provider:
            healthy = await _agent_control._llm_provider.healthcheck()
    except Exception:
        pass
    return LLMStatusOut(
        provider=info["provider"], model=info["model"], endpoint=info["endpoint"],
        healthy=healthy, backlog=info["backlog"], capacity=info["capacity"],
        running=info["running"],
        calls_total=info.get("calls_total", 0),
        calls_finance=info.get("calls_finance", 0),
        calls_errors=info.get("calls_errors", 0),
        avg_latency_ms=info.get("avg_latency_ms", 0),
        last_call_at=info.get("last_call_at"),
        dropped_total=info.get("dropped_total", 0),
    )


# ── DB browser endpoints ──────────────────────────────────────────────────────

@router.get("/db/stats", response_model=DBStatsOut)
def db_stats(db: Session = Depends(_session)):
    last_tw = db.query(TweetRow).order_by(TweetRow.collected_at.desc()).first()
    last_ex = db.query(ExtractionRow).order_by(ExtractionRow.created_at.desc()).first()
    return DBStatsOut(
        tweets=db.query(TweetRow).count(),
        extractions=db.query(ExtractionRow).count(),
        extractions_finance=db.query(ExtractionRow).filter(ExtractionRow.is_finance == True).count(),
        tickers=db.query(TickerScoreRow).count(),
        themes=db.query(ThemeScoreRow).count(),
        edges=db.query(GraphEdgeRow).count(),
        signals=db.query(SignalRow).count(),
        last_tweet_at=last_tw.collected_at if last_tw else None,
        last_extraction_at=last_ex.created_at if last_ex else None,
    )


@router.get("/db/tweets")
def db_tweets(limit: int = 50, offset: int = 0, q: str = "", db: Session = Depends(_session)):
    query = db.query(TweetRow)
    if q:
        query = query.filter(TweetRow.text.ilike(f"%{q}%") | TweetRow.author.ilike(f"%{q}%"))
    total = query.count()
    rows = query.order_by(TweetRow.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "rows": [{
            "id": r.id, "author": r.author, "text": r.text,
            "created_at": r.created_at.isoformat(),
            "collected_at": r.collected_at.isoformat() if r.collected_at else None,
            "like_count": r.like_count, "retweet_count": r.retweet_count,
            "reply_count": r.reply_count, "url": r.url,
        } for r in rows],
    }


@router.get("/db/extractions")
def db_extractions(limit: int = 50, offset: int = 0, finance_only: bool = False,
                   q: str = "", db: Session = Depends(_session)):
    query = db.query(ExtractionRow, TweetRow).join(TweetRow, ExtractionRow.tweet_id == TweetRow.id)
    if finance_only:
        query = query.filter(ExtractionRow.is_finance == True)
    if q:
        query = query.filter(TweetRow.text.ilike(f"%{q}%") | TweetRow.author.ilike(f"%{q}%"))
    total = query.count()
    rows = query.order_by(ExtractionRow.created_at.desc()).offset(offset).limit(limit).all()
    out = []
    for ex, tw in rows:
        tickers = db.query(ExtractedTickerRow).filter(
            ExtractedTickerRow.extraction_id == ex.id).all()
        themes = db.query(ExtractedThemeRow).filter(
            ExtractedThemeRow.extraction_id == ex.id).all()
        out.append({
            "id": ex.id,
            "tweet_id": ex.tweet_id,
            "author": tw.author,
            "text": tw.text,
            "url": tw.url,
            "is_finance": ex.is_finance,
            "overall_sentiment": ex.overall_sentiment,
            "summary": ex.summary,
            "model": ex.model,
            "latency_ms": ex.latency_ms,
            "created_at": ex.created_at.isoformat(),
            "tickers": [{"ticker": t.ticker, "sentiment": t.sentiment,
                         "confidence": t.confidence, "explicit": t.explicit}
                        for t in tickers],
            "themes": [{"theme": t.theme, "sentiment": t.sentiment, "confidence": t.confidence}
                       for t in themes],
            "raw_json": ex.raw_json,
        })
    return {"total": total, "rows": out}


@router.get("/db/ticker_scores")
def db_ticker_scores(limit: int = 50, offset: int = 0, db: Session = Depends(_session)):
    total = db.query(TickerScoreRow).count()
    rows = db.query(TickerScoreRow).order_by(TickerScoreRow.window_end.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "rows": [{
            "id": r.id, "ticker": r.ticker, "score": r.score,
            "voices": r.unique_credible_voices, "tweet_count": r.tweet_count,
            "window_start": r.window_start.isoformat(),
            "window_end": r.window_end.isoformat(),
        } for r in rows],
    }


@router.get("/db/theme_scores")
def db_theme_scores(limit: int = 50, offset: int = 0, db: Session = Depends(_session)):
    total = db.query(ThemeScoreRow).count()
    rows = db.query(ThemeScoreRow).order_by(ThemeScoreRow.window_end.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "rows": [{
            "id": r.id, "theme": r.theme, "score": r.score, "voices": r.voices,
            "tweet_count": r.tweet_count,
            "window_start": r.window_start.isoformat(),
            "window_end": r.window_end.isoformat(),
        } for r in rows],
    }


@router.get("/db/graph_edges")
def db_graph_edges(limit: int = 50, offset: int = 0, db: Session = Depends(_session)):
    total = db.query(GraphEdgeRow).count()
    rows = db.query(GraphEdgeRow).order_by(GraphEdgeRow.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "rows": [{
            "id": r.id, "from_id": r.from_id, "to_id": r.to_id,
            "edge_type": r.edge_type, "weight": r.weight,
            "sentiment": r.sentiment, "tweet_id": r.tweet_id,
            "created_at": r.created_at.isoformat(),
        } for r in rows],
    }


@router.post("/llm/test_extract")
async def llm_test_extract(limit: int = 5, db: Session = Depends(_session)):
    """DEBUG: re-submit the N most recent tweets to the extractor.
    Useful for verifying the LLM pipeline without waiting for a collect cycle.
    Not part of normal operation — clients should NOT poll this."""
    if _agent_control is None or _agent_control._extractor is None:
        raise HTTPException(503, "Extractor not running. Start the agent first.")
    from dolev_ai.models import RawTweet
    rows = db.query(TweetRow).order_by(TweetRow.created_at.desc()).limit(limit).all()
    if not rows:
        return {"submitted": 0, "message": "No tweets in DB yet"}
    for r in rows:
        tw = RawTweet(
            id=r.id, author=r.author, text=r.text,
            created_at=r.created_at, like_count=r.like_count,
            retweet_count=r.retweet_count, reply_count=r.reply_count,
            url=r.url,
        )
        await _agent_control._extractor.submit(tw)
    return {"submitted": len(rows), "tweets": [{"id": r.id, "author": r.author} for r in rows]}


@router.get("/db/signals")
def db_signals(limit: int = 50, offset: int = 0, db: Session = Depends(_session)):
    total = db.query(SignalRow).count()
    rows = db.query(SignalRow).order_by(SignalRow.generated_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "rows": [{
            "id": r.id, "ticker": r.ticker, "side": r.side,
            "conviction": r.conviction, "suggested_size_pct": r.suggested_size_pct,
            "rationale": r.rationale,
            "key_drivers": json.loads(r.key_drivers or "[]"),
            "generated_at": r.generated_at.isoformat(),
        } for r in rows],
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_themes_config() -> dict:
    themes_path = pathlib.Path(__file__).parent.parent.parent.parent / "config" / "themes.yaml"
    if not themes_path.exists():
        return {}
    with open(themes_path) as f:
        return yaml.safe_load(f) or {}


def _load_seed_handles() -> dict[str, int]:
    if not SEEDS_PATH.exists():
        return {}
    with open(SEEDS_PATH) as f:
        data = yaml.safe_load(f)
    return {
        a["handle"].lstrip("@").lower(): int(a.get("tier", 3))
        for a in data.get("accounts", [])
    }
