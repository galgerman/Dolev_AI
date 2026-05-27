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
    # Research/journaling fields
    signal_price = Column(Float, nullable=True)       # quote at signal-time (pre-fill)
    fill_latency_ms = Column(Integer, nullable=True)  # signal -> fill duration
    slippage_bps = Column(Float, nullable=True)       # (fill - signal)/signal * 10000, signed
    mfe_pct = Column(Float, nullable=True)            # max favorable excursion (signed)
    mae_pct = Column(Float, nullable=True)            # max adverse excursion (signed)
    mfe_at = Column(DateTime, nullable=True)
    mae_at = Column(DateTime, nullable=True)
    exit_reason = Column(String, nullable=True)       # stop_loss|eod_flatten|opposite_signal|manual
    telegram_message_id = Column(Integer, nullable=True)  # for editing on close
    confidence_at_entry = Column(Float, nullable=True)
    feature_snapshot_json = Column(Text, nullable=True)   # FeatureSnapshot as JSON


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
    # Extended features (research system)
    roc_1m = Column(Float, default=0.0)
    roc_3m = Column(Float, default=0.0)
    roc_5m = Column(Float, default=0.0)
    acceleration = Column(Float, default=0.0)
    vwap = Column(Float, default=0.0)
    vwap_state = Column(String, default="unknown")
    extension_pct = Column(Float, default=0.0)
    broke_pmh = Column(Boolean, default=False)
    broke_pml = Column(Boolean, default=False)
    rel_strength_spy = Column(Float, default=0.0)
    rel_strength_qqq = Column(Float, default=0.0)
    rel_strength_sector = Column(Float, default=0.0)
    sector_etf = Column(String, nullable=True)
    bid = Column(Float, default=0.0)
    ask = Column(Float, default=0.0)
    spread_pct = Column(Float, default=0.0)
    breakout_volume_ratio = Column(Float, default=0.0)


class SignalObservationRow(Base):
    """One row per signal — records forward returns at fixed horizons.

    Populated in every pipeline mode (observe/paper/live) so we always have
    research data even while paper-trading. The ForwardReturnObserver task
    fills in return_<horizon> and mfe/mae over time.
    """
    __tablename__ = "signal_observations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=True, index=True)  # may be null in observe mode
    ticker = Column(String, nullable=False, index=True)
    side = Column(String, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow, index=True)
    entry_price = Column(Float, nullable=False)
    confidence = Column(Float, default=0.0)
    components_json = Column(Text, default="{}")
    features_json = Column(Text, default="{}")
    sector_etf = Column(String, nullable=True)
    # Horizon scheduling (state)
    horizons_remaining_json = Column(Text, default="[]")     # list of seconds
    next_due_at = Column(DateTime, nullable=False, index=True)
    # Outcome columns (filled by observer as each horizon comes due)
    return_30s = Column(Float, nullable=True)
    return_1m = Column(Float, nullable=True)
    return_3m = Column(Float, nullable=True)
    return_5m = Column(Float, nullable=True)
    return_15m = Column(Float, nullable=True)
    return_30m = Column(Float, nullable=True)
    mfe_pct = Column(Float, nullable=True)   # signed in direction of signal
    mae_pct = Column(Float, nullable=True)
    status = Column(String, default="pending", index=True)   # pending | complete | abandoned


Index("ix_extractions_tweet_created", ExtractionRow.tweet_id, ExtractionRow.created_at)
Index("ix_graph_edges_recent", GraphEdgeRow.created_at, GraphEdgeRow.edge_type)
Index("ix_movements_ticker_time", TickerMovementRow.ticker, TickerMovementRow.captured_at)


def _migrate_add_columns(engine) -> None:
    """Lightweight schema migration: ALTER TABLE for columns the ORM expects but
    that aren't yet in the existing SQLite file. Skips columns already present.
    """
    from sqlalchemy import inspect, text
    expected: dict[str, list[tuple[str, str]]] = {
        "paper_positions": [
            ("shares", "INTEGER"),
            ("stop_price", "FLOAT"),
            ("ibkr_order_id", "INTEGER"),
            ("ibkr_stop_order_id", "INTEGER"),
            ("pnl_dollars", "FLOAT"),
            ("signal_price", "FLOAT"),
            ("fill_latency_ms", "INTEGER"),
            ("slippage_bps", "FLOAT"),
            ("mfe_pct", "FLOAT"),
            ("mae_pct", "FLOAT"),
            ("mfe_at", "DATETIME"),
            ("mae_at", "DATETIME"),
            ("exit_reason", "VARCHAR"),
            ("telegram_message_id", "INTEGER"),
            ("confidence_at_entry", "FLOAT"),
            ("feature_snapshot_json", "TEXT"),
        ],
        "ticker_movements": [
            ("gradient", "FLOAT DEFAULT 0.0"),
            ("gradient_bars", "INTEGER DEFAULT 0"),
            ("roc_1m", "FLOAT DEFAULT 0.0"),
            ("roc_3m", "FLOAT DEFAULT 0.0"),
            ("roc_5m", "FLOAT DEFAULT 0.0"),
            ("acceleration", "FLOAT DEFAULT 0.0"),
            ("vwap", "FLOAT DEFAULT 0.0"),
            ("vwap_state", "VARCHAR DEFAULT 'unknown'"),
            ("extension_pct", "FLOAT DEFAULT 0.0"),
            ("broke_pmh", "BOOLEAN DEFAULT 0"),
            ("broke_pml", "BOOLEAN DEFAULT 0"),
            ("rel_strength_spy", "FLOAT DEFAULT 0.0"),
            ("rel_strength_qqq", "FLOAT DEFAULT 0.0"),
            ("rel_strength_sector", "FLOAT DEFAULT 0.0"),
            ("sector_etf", "VARCHAR"),
            ("bid", "FLOAT DEFAULT 0.0"),
            ("ask", "FLOAT DEFAULT 0.0"),
            ("spread_pct", "FLOAT DEFAULT 0.0"),
            ("breakout_volume_ratio", "FLOAT DEFAULT 0.0"),
        ],
        "ticker_scores": [
            ("confirmation_factor", "FLOAT DEFAULT 1.0"),
            ("movement_pct", "FLOAT"),
        ],
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, cols in expected.items():
            if not inspector.has_table(table):
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, col_type in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"))


def init_db(db_path: pathlib.Path = DB_PATH) -> sessionmaker:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    _migrate_add_columns(engine)
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
    """Insert one TickerMovementRow per Mover/GradientMover/EnrichedMover. Returns count inserted.

    Tolerates any attribute being missing — defaults to 0/False/None.
    """
    def _g(m, attr, default):
        return getattr(m, attr, default)

    n = 0
    for m in movers:
        session.add(TickerMovementRow(
            ticker=m.ticker,
            pct_change=m.pct_change,
            last_price=m.last_price,
            rel_volume=_g(m, "rel_volume", 0.0),
            market_cap=_g(m, "market_cap", 0.0),
            rank=m.rank,
            side=m.side,
            gradient=_g(m, "gradient", 0.0),
            gradient_bars=_g(m, "gradient_bars", 0),
            captured_at=m.captured_at,
            roc_1m=_g(m, "roc_1m", 0.0),
            roc_3m=_g(m, "roc_3m", 0.0),
            roc_5m=_g(m, "roc_5m", 0.0),
            acceleration=_g(m, "acceleration", 0.0),
            vwap=_g(m, "vwap", 0.0),
            vwap_state=_g(m, "vwap_state", "unknown"),
            extension_pct=_g(m, "extension_pct", 0.0),
            broke_pmh=_g(m, "broke_pmh", False),
            broke_pml=_g(m, "broke_pml", False),
            rel_strength_spy=_g(m, "rel_strength_spy", 0.0),
            rel_strength_qqq=_g(m, "rel_strength_qqq", 0.0),
            rel_strength_sector=_g(m, "rel_strength_sector", 0.0),
            sector_etf=_g(m, "sector_etf", None),
            bid=_g(m, "bid", 0.0),
            ask=_g(m, "ask", 0.0),
            spread_pct=_g(m, "spread_pct", 0.0),
            breakout_volume_ratio=_g(m, "breakout_volume_ratio", 0.0),
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
            roc_1m=r.roc_1m or 0.0,
            roc_3m=r.roc_3m or 0.0,
            roc_5m=r.roc_5m or 0.0,
            acceleration=r.acceleration or 0.0,
            vwap=r.vwap or 0.0,
            vwap_state=r.vwap_state or "unknown",
            extension_pct=r.extension_pct or 0.0,
            broke_pmh=bool(r.broke_pmh),
            broke_pml=bool(r.broke_pml),
            rel_strength_spy=r.rel_strength_spy or 0.0,
            rel_strength_qqq=r.rel_strength_qqq or 0.0,
            rel_strength_sector=r.rel_strength_sector or 0.0,
            sector_etf=r.sector_etf,
            bid=r.bid or 0.0,
            ask=r.ask or 0.0,
            spread_pct=r.spread_pct or 0.0,
            breakout_volume_ratio=r.breakout_volume_ratio or 0.0,
        )
    return out


# ── Signal observations (forward-return tracking) ────────────────────────────

def save_signal_observation(
    session: Session,
    *,
    ticker: str,
    side: str,
    entry_price: float,
    confidence: float,
    components: dict,
    features: dict,
    horizons_seconds: list[int],
    signal_id: int | None = None,
    sector_etf: str | None = None,
    generated_at: datetime | None = None,
) -> int:
    """Persist a signal observation row; returns its id.

    horizons_seconds: e.g. [30, 60, 180, 300, 900, 1800]. The first one becomes
    next_due_at; the remainder are stored as horizons_remaining_json.
    """
    from datetime import timedelta
    if not horizons_seconds:
        raise ValueError("horizons_seconds must not be empty")
    gen_at = generated_at or datetime.utcnow()
    sorted_h = sorted(horizons_seconds)
    next_due = gen_at + timedelta(seconds=sorted_h[0])
    row = SignalObservationRow(
        signal_id=signal_id,
        ticker=ticker,
        side=side,
        generated_at=gen_at,
        entry_price=entry_price,
        confidence=confidence,
        components_json=json.dumps(components),
        features_json=json.dumps(features),
        sector_etf=sector_etf,
        horizons_remaining_json=json.dumps(sorted_h),
        next_due_at=next_due,
        status="pending",
    )
    session.add(row)
    session.commit()
    return row.id


def due_observations(session: Session, now: datetime | None = None) -> list[SignalObservationRow]:
    """Pending observations whose next_due_at has passed."""
    now = now or datetime.utcnow()
    return (
        session.query(SignalObservationRow)
        .filter(
            SignalObservationRow.status == "pending",
            SignalObservationRow.next_due_at <= now,
        )
        .all()
    )


_RETURN_COLUMN_FOR_HORIZON = {
    30: "return_30s",
    60: "return_1m",
    180: "return_3m",
    300: "return_5m",
    900: "return_15m",
    1800: "return_30m",
}


def record_observation_return(
    session: Session,
    obs_id: int,
    horizon_seconds: int,
    current_price: float,
) -> SignalObservationRow | None:
    """Fill in the return_<horizon> column for one observation and advance state.

    Computes return as signed in the direction of the signal: positive return
    means the trade idea was correct.
    """
    from datetime import timedelta
    row = session.get(SignalObservationRow, obs_id)
    if row is None or row.status != "pending":
        return None
    if row.entry_price <= 0:
        row.status = "abandoned"
        session.commit()
        return row

    raw = (current_price - row.entry_price) / row.entry_price * 100.0
    directional = raw if row.side == "buy" else -raw

    col = _RETURN_COLUMN_FOR_HORIZON.get(horizon_seconds)
    if col is not None:
        setattr(row, col, round(directional, 4))

    # Update MFE/MAE
    if row.mfe_pct is None or directional > row.mfe_pct:
        row.mfe_pct = round(directional, 4)
    if row.mae_pct is None or directional < row.mae_pct:
        row.mae_pct = round(directional, 4)

    # Advance horizon list
    remaining: list[int] = json.loads(row.horizons_remaining_json or "[]")
    remaining = [h for h in remaining if h != horizon_seconds]
    row.horizons_remaining_json = json.dumps(remaining)
    if remaining:
        next_h = remaining[0]
        row.next_due_at = row.generated_at + timedelta(seconds=next_h)
    else:
        row.status = "complete"
        row.next_due_at = row.generated_at + timedelta(days=365)  # park far in future
    session.commit()
    return row


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
