"""SQLite persistence via SQLAlchemy (ORM-free, Core only for simplicity)."""
from __future__ import annotations

import json
import pathlib
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from dolev_ai.models import RawTweet, Signal, TickerScore

DB_PATH = pathlib.Path(__file__).parent.parent.parent / "data" / "dolev.db"


class Base(DeclarativeBase):
    pass


class TweetRow(Base):
    __tablename__ = "tweets"
    id = Column(String, primary_key=True)
    author = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    like_count = Column(Integer, default=0)
    retweet_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    url = Column(String, nullable=False)
    collected_at = Column(DateTime, default=datetime.utcnow)


class TickerScoreRow(Base):
    __tablename__ = "ticker_scores"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    score = Column(Float, nullable=False)
    unique_credible_voices = Column(Integer, default=0)
    tweet_count = Column(Integer, default=0)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    top_tweet_urls = Column(Text, default="[]")  # JSON list
    confirmation_factor = Column(Float, default=1.0)  # TradingView multiplier applied
    movement_pct = Column(Float, nullable=True)        # latest pct change at score time


class SignalRow(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    conviction = Column(Float, nullable=False)
    suggested_size_pct = Column(Float, nullable=False)
    rationale = Column(Text, nullable=False)
    key_drivers = Column(Text, default="[]")  # JSON list
    generated_at = Column(DateTime, nullable=False)


class AlertLogRow(Base):
    __tablename__ = "alert_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    conviction = Column(Float, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    channel = Column(String, default="telegram")


class PaperPositionRow(Base):
    __tablename__ = "paper_positions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)           # 'buy' | 'sell'
    entry_signal_id = Column(Integer, nullable=False)
    entry_price = Column(Float, nullable=False)
    shares = Column(Integer, nullable=True)          # position size in shares
    stop_price = Column(Float, nullable=True)        # stop-loss price
    ibkr_order_id = Column(Integer, nullable=True)  # IBKR entry order id
    ibkr_stop_order_id = Column(Integer, nullable=True)  # IBKR stop order id
    opened_at = Column(DateTime, default=datetime.utcnow, index=True)
    exit_signal_id = Column(Integer, nullable=True)
    exit_price = Column(Float, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    pnl_dollars = Column(Float, nullable=True)       # absolute P&L in USD
    status = Column(String, default="open")         # 'open' | 'closed'
    retrospective = Column(Text, nullable=True)
    retrospective_at = Column(DateTime, nullable=True)


class SignalApprovalRow(Base):
    __tablename__ = "signal_approvals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=False, index=True)
    kind = Column(String, nullable=False)           # 'open' | 'close'
    position_id = Column(Integer, nullable=True)
    telegram_message_id = Column(Integer, nullable=True)
    status = Column(String, default="pending", index=True)  # pending|approved|rejected|expired
    prompted_at = Column(DateTime, default=datetime.utcnow, index=True)
    responded_at = Column(DateTime, nullable=True)


# ── LLM extraction tables ────────────────────────────────────────────────

class ExtractionRow(Base):
    __tablename__ = "extractions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tweet_id = Column(String, ForeignKey("tweets.id"), nullable=False, index=True)
    model = Column(String, nullable=False)
    is_finance = Column(Boolean, default=False)
    overall_sentiment = Column(String, default="neutral")
    summary = Column(Text, default="")
    raw_json = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ExtractedTickerRow(Base):
    __tablename__ = "extracted_tickers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    extraction_id = Column(Integer, ForeignKey("extractions.id"), nullable=False, index=True)
    ticker = Column(String, nullable=False, index=True)
    sentiment = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    explicit = Column(Boolean, default=False)


class ExtractedThemeRow(Base):
    __tablename__ = "extracted_themes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    extraction_id = Column(Integer, ForeignKey("extractions.id"), nullable=False, index=True)
    theme = Column(String, nullable=False, index=True)
    sentiment = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)


class GraphEdgeRow(Base):
    __tablename__ = "graph_edges"
    id = Column(Integer, primary_key=True, autoincrement=True)
    from_id = Column(String, nullable=False, index=True)   # "acct:handle" or "theme:key"
    to_id = Column(String, nullable=False, index=True)     # "ticker:NVDA" or "theme:key"
    edge_type = Column(String, nullable=False)             # acct_ticker | acct_theme | theme_ticker
    weight = Column(Float, nullable=False)
    sentiment = Column(String, default="neutral")
    tweet_id = Column(String, nullable=True, index=True)   # source post (null for theme_ticker)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ThemeScoreRow(Base):
    __tablename__ = "theme_scores"
    id = Column(Integer, primary_key=True, autoincrement=True)
    theme = Column(String, nullable=False, index=True)
    score = Column(Float, nullable=False)
    voices = Column(Integer, default=0)
    tweet_count = Column(Integer, default=0)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)


class TickerMovementRow(Base):
    __tablename__ = "ticker_movements"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    pct_change = Column(Float, nullable=False)         # signed: -8.2 = down 8.2%
    last_price = Column(Float, nullable=False)
    rel_volume = Column(Float, default=1.0)            # vs 10-day average
    market_cap = Column(Float, default=0.0)
    rank = Column(Integer, default=0)                  # rank within fetched side
    side = Column(String, default="gainer")            # "gainer" | "loser"
    gradient = Column(Float, default=0.0)              # %/min linear slope (IBKR only)
    gradient_bars = Column(Integer, default=0)         # bars used to compute gradient
    captured_at = Column(DateTime, default=datetime.utcnow, index=True)


Index("ix_extractions_tweet_created", ExtractionRow.tweet_id, ExtractionRow.created_at)
Index("ix_graph_edges_recent", GraphEdgeRow.created_at, GraphEdgeRow.edge_type)
Index("ix_movements_ticker_time", TickerMovementRow.ticker, TickerMovementRow.captured_at)


def init_db(db_path: pathlib.Path = DB_PATH) -> sessionmaker:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# ── helpers ──────────────────────────────────────────────────────────────────

def save_tweets(session: Session, tweets: list[RawTweet]) -> int:
    new_count = 0
    for t in tweets:
        if session.get(TweetRow, t.id) is None:
            session.add(TweetRow(
                id=t.id, author=t.author, text=t.text,
                created_at=t.created_at, like_count=t.like_count,
                retweet_count=t.retweet_count, reply_count=t.reply_count,
                url=t.url,
            ))
            new_count += 1
    session.commit()
    return new_count


def load_tweets_since(session: Session, since: datetime) -> list[RawTweet]:
    rows = session.query(TweetRow).filter(TweetRow.created_at >= since).all()
    return [
        RawTweet(
            id=r.id, author=r.author, text=r.text,
            created_at=r.created_at, like_count=r.like_count,
            retweet_count=r.retweet_count, reply_count=r.reply_count,
            url=r.url,
        )
        for r in rows
    ]


def save_ticker_score(session: Session, ts: TickerScore) -> None:
    session.add(TickerScoreRow(
        ticker=ts.ticker, score=ts.score,
        unique_credible_voices=ts.unique_credible_voices,
        tweet_count=ts.tweet_count,
        window_start=ts.window_start, window_end=ts.window_end,
        top_tweet_urls=json.dumps(ts.top_tweet_urls),
        confirmation_factor=ts.confirmation_factor,
        movement_pct=ts.movement_pct,
    ))
    session.commit()


def save_signal(session: Session, sig: Signal) -> int:
    """Persist a Signal and return its DB row id."""
    row = SignalRow(
        ticker=sig.ticker, side=sig.side,
        conviction=sig.conviction,
        suggested_size_pct=sig.suggested_size_pct,
        rationale=sig.rationale,
        key_drivers=json.dumps(sig.key_drivers),
        generated_at=sig.generated_at,
    )
    session.add(row)
    session.commit()
    return row.id


def last_alert_time(session: Session, ticker: str) -> datetime | None:
    row = (
        session.query(AlertLogRow)
        .filter(AlertLogRow.ticker == ticker)
        .order_by(AlertLogRow.sent_at.desc())
        .first()
    )
    return row.sent_at if row else None


def log_alert(session: Session, sig: Signal, channel: str = "telegram") -> None:
    session.add(AlertLogRow(
        ticker=sig.ticker, side=sig.side,
        conviction=sig.conviction, sent_at=sig.generated_at,
        channel=channel,
    ))
    session.commit()


# ── Extraction persistence ──────────────────────────────────────────────

def save_extraction(session: Session, ex) -> int:
    """Persist an Extraction. Returns the extraction row id."""
    row = ExtractionRow(
        tweet_id=ex.post_id,
        model=ex.model,
        is_finance=ex.is_finance,
        overall_sentiment=ex.overall_sentiment,
        summary=ex.summary,
        raw_json=ex.raw_json,
        latency_ms=ex.latency_ms,
    )
    session.add(row)
    session.flush()  # populate row.id
    for tm in ex.tickers:
        session.add(ExtractedTickerRow(
            extraction_id=row.id, ticker=tm.ticker,
            sentiment=tm.sentiment, confidence=tm.confidence,
            explicit=tm.explicit,
        ))
    for th in ex.themes:
        session.add(ExtractedThemeRow(
            extraction_id=row.id, theme=th.theme,
            sentiment=th.sentiment, confidence=th.confidence,
        ))
    session.commit()
    return row.id


def save_graph_edge(
    session: Session, from_id: str, to_id: str, edge_type: str,
    weight: float, sentiment: str = "neutral", tweet_id: str | None = None,
) -> None:
    session.add(GraphEdgeRow(
        from_id=from_id, to_id=to_id, edge_type=edge_type,
        weight=weight, sentiment=sentiment, tweet_id=tweet_id,
    ))
    session.commit()


def save_theme_score(
    session: Session, theme: str, score: float, voices: int,
    tweet_count: int, window_start: datetime, window_end: datetime,
) -> None:
    session.add(ThemeScoreRow(
        theme=theme, score=score, voices=voices, tweet_count=tweet_count,
        window_start=window_start, window_end=window_end,
    ))
    session.commit()


def save_movements(session: Session, movers: list) -> int:
    """Insert one TickerMovementRow per Mover or GradientMover. Returns count inserted."""
    n = 0
    for m in movers:
        session.add(TickerMovementRow(
            ticker=m.ticker,
            pct_change=m.pct_change,
            last_price=m.last_price,
            rel_volume=getattr(m, "rel_volume", 0.0),
            market_cap=getattr(m, "market_cap", 0.0),
            rank=m.rank,
            side=m.side,
            gradient=getattr(m, "gradient", 0.0),
            gradient_bars=getattr(m, "gradient_bars", 0),
            captured_at=m.captured_at,
        ))
        n += 1
    session.commit()
    return n


def load_latest_movements(session: Session, max_age_minutes: int = 30) -> dict:
    """Return {ticker: MovementSnapshot} with the most recent row per ticker."""
    from datetime import timedelta
    from dolev_ai.models import MovementSnapshot
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    rows = (
        session.query(TickerMovementRow)
        .filter(TickerMovementRow.captured_at >= cutoff)
        .order_by(TickerMovementRow.captured_at.desc())
        .all()
    )
    out: dict[str, MovementSnapshot] = {}
    for r in rows:
        if r.ticker in out:
            continue
        out[r.ticker] = MovementSnapshot(
            ticker=r.ticker, pct_change=r.pct_change,
            last_price=r.last_price, rel_volume=r.rel_volume,
            captured_at=r.captured_at,
            gradient=r.gradient or 0.0,
        )
    return out


def prune_old_data(
    session: Session,
    tweets_keep_days: int = 30,
    scores_keep_hours: int = 24,
    edges_keep_hours: int = 24,
) -> dict:
    """Trim the DB to bounded sizes. Returns counts of rows deleted per table."""
    from datetime import timedelta
    now = datetime.utcnow()
    tweets_cutoff = now - timedelta(days=tweets_keep_days)
    scores_cutoff = now - timedelta(hours=scores_keep_hours)
    edges_cutoff = now - timedelta(hours=edges_keep_hours)

    counts: dict[str, int] = {}

    # Time-series tables (regenerated every cycle)
    counts["ticker_scores"] = session.query(TickerScoreRow).filter(
        TickerScoreRow.window_end < scores_cutoff
    ).delete(synchronize_session=False)

    counts["theme_scores"] = session.query(ThemeScoreRow).filter(
        ThemeScoreRow.window_end < scores_cutoff
    ).delete(synchronize_session=False)

    counts["graph_edges"] = session.query(GraphEdgeRow).filter(
        GraphEdgeRow.created_at < edges_cutoff
    ).delete(synchronize_session=False)

    counts["ticker_movements"] = session.query(TickerMovementRow).filter(
        TickerMovementRow.captured_at < scores_cutoff
    ).delete(synchronize_session=False)

    # Find old tweets first so we can cascade-delete their extractions
    old_tweet_ids = [
        t.id for t in session.query(TweetRow.id)
        .filter(TweetRow.created_at < tweets_cutoff).all()
    ]
    if old_tweet_ids:
        # Delete dependent extracted_tickers/themes first (no ON DELETE CASCADE in SQLite by default)
        old_ex_ids = [
            r.id for r in session.query(ExtractionRow.id)
            .filter(ExtractionRow.tweet_id.in_(old_tweet_ids)).all()
        ]
        if old_ex_ids:
            session.query(ExtractedTickerRow).filter(
                ExtractedTickerRow.extraction_id.in_(old_ex_ids)
            ).delete(synchronize_session=False)
            session.query(ExtractedThemeRow).filter(
                ExtractedThemeRow.extraction_id.in_(old_ex_ids)
            ).delete(synchronize_session=False)
        counts["extractions"] = session.query(ExtractionRow).filter(
            ExtractionRow.tweet_id.in_(old_tweet_ids)
        ).delete(synchronize_session=False)
        counts["tweets"] = session.query(TweetRow).filter(
            TweetRow.id.in_(old_tweet_ids)
        ).delete(synchronize_session=False)
    else:
        counts["extractions"] = 0
        counts["tweets"] = 0

    session.commit()
    return counts


def vacuum_db(session: Session) -> None:
    """Reclaim space after big deletes. SQLite-only."""
    session.execute(text("VACUUM"))


# ── Paper trading helpers ─────────────────────────────────────────────────────

def open_positions(session: Session) -> list[PaperPositionRow]:
    return session.query(PaperPositionRow).filter(PaperPositionRow.status == "open").all()


def position_for_ticker(session: Session, ticker: str) -> PaperPositionRow | None:
    return (
        session.query(PaperPositionRow)
        .filter(PaperPositionRow.ticker == ticker, PaperPositionRow.status == "open")
        .order_by(PaperPositionRow.opened_at.desc())
        .first()
    )


def save_pending_approval(
    session: Session,
    kind: str,
    signal_id: int,
    position_id: int | None = None,
) -> int:
    row = SignalApprovalRow(kind=kind, signal_id=signal_id, position_id=position_id)
    session.add(row)
    session.commit()
    return row.id


def set_approval_message_id(session: Session, approval_id: int, msg_id: int) -> None:
    row = session.get(SignalApprovalRow, approval_id)
    if row:
        row.telegram_message_id = msg_id
        session.commit()


def update_approval_status(session: Session, approval_id: int, status: str) -> None:
    row = session.get(SignalApprovalRow, approval_id)
    if row:
        row.status = status
        row.responded_at = datetime.utcnow()
        session.commit()


def pending_approvals_older_than(session: Session, cutoff: datetime) -> list[SignalApprovalRow]:
    return (
        session.query(SignalApprovalRow)
        .filter(SignalApprovalRow.status == "pending", SignalApprovalRow.prompted_at < cutoff)
        .all()
    )


def get_approval(session: Session, approval_id: int) -> SignalApprovalRow | None:
    return session.get(SignalApprovalRow, approval_id)


def positions_opened_on(session: Session, day: datetime) -> list[PaperPositionRow]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59)
    return (
        session.query(PaperPositionRow)
        .filter(PaperPositionRow.opened_at >= start, PaperPositionRow.opened_at <= end)
        .all()
    )


def positions_closed_on(session: Session, day: datetime) -> list[PaperPositionRow]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59)
    return (
        session.query(PaperPositionRow)
        .filter(PaperPositionRow.closed_at >= start, PaperPositionRow.closed_at <= end)
        .all()
    )


def approvals_on(session: Session, day: datetime) -> list[SignalApprovalRow]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59)
    return (
        session.query(SignalApprovalRow)
        .filter(SignalApprovalRow.prompted_at >= start, SignalApprovalRow.prompted_at <= end)
        .all()
    )


def load_extractions_since(session: Session, since: datetime) -> list:
    """Load extractions joined with their tweets, since the given time."""
    rows = (
        session.query(ExtractionRow, TweetRow)
        .join(TweetRow, ExtractionRow.tweet_id == TweetRow.id)
        .filter(ExtractionRow.created_at >= since, ExtractionRow.is_finance == True)
        .all()
    )
    out = []
    for ex_row, tw_row in rows:
        tickers = session.query(ExtractedTickerRow).filter(
            ExtractedTickerRow.extraction_id == ex_row.id).all()
        themes = session.query(ExtractedThemeRow).filter(
            ExtractedThemeRow.extraction_id == ex_row.id).all()
        out.append({
            "extraction": ex_row,
            "tweet": tw_row,
            "tickers": tickers,
            "themes": themes,
        })
    return out
