"""SQLite persistence via SQLAlchemy (ORM-free, Core only for simplicity)."""
from __future__ import annotations

import json
import pathlib
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
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
    ))
    session.commit()


def save_signal(session: Session, sig: Signal) -> None:
    session.add(SignalRow(
        ticker=sig.ticker, side=sig.side,
        conviction=sig.conviction,
        suggested_size_pct=sig.suggested_size_pct,
        rationale=sig.rationale,
        key_drivers=json.dumps(sig.key_drivers),
        generated_at=sig.generated_at,
    ))
    session.commit()


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
