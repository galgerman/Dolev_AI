"""Tests for prune_old_data."""
from datetime import datetime, timedelta

from dolev_ai.db import (
    ExtractedThemeRow,
    ExtractedTickerRow,
    ExtractionRow,
    GraphEdgeRow,
    ThemeScoreRow,
    TickerScoreRow,
    TweetRow,
    init_db,
    prune_old_data,
)


def _make_session(tmp_path):
    return init_db(tmp_path / "prune.db")


def test_prune_drops_old_scores_and_edges(tmp_path):
    factory = _make_session(tmp_path)
    now = datetime.utcnow()
    old = now - timedelta(hours=48)

    with factory() as s:
        s.add(TickerScoreRow(ticker="NVDA", score=1.0, unique_credible_voices=1,
                             tweet_count=1, window_start=old, window_end=old))
        s.add(TickerScoreRow(ticker="NVDA", score=2.0, unique_credible_voices=1,
                             tweet_count=1, window_start=now, window_end=now))
        s.add(ThemeScoreRow(theme="ai_infrastructure", score=1.0, voices=1,
                            tweet_count=1, window_start=old, window_end=old))
        s.add(GraphEdgeRow(from_id="acct:x", to_id="ticker:NVDA",
                           edge_type="acct_ticker", weight=1.0, sentiment="positive",
                           tweet_id=None, created_at=old))
        s.commit()

    with factory() as s:
        counts = prune_old_data(s, scores_keep_hours=24, edges_keep_hours=24)
        assert counts["ticker_scores"] == 1
        assert counts["theme_scores"] == 1
        assert counts["graph_edges"] == 1
        # Remaining current row survives
        assert s.query(TickerScoreRow).count() == 1


def test_prune_cascades_through_extractions(tmp_path):
    factory = _make_session(tmp_path)
    now = datetime.utcnow()
    old = now - timedelta(days=40)

    with factory() as s:
        s.add(TweetRow(id="t-old", author="a", text="old",
                       created_at=old, url="x", like_count=0,
                       retweet_count=0, reply_count=0))
        s.add(TweetRow(id="t-new", author="a", text="new",
                       created_at=now, url="x", like_count=0,
                       retweet_count=0, reply_count=0))
        s.commit()
        s.add(ExtractionRow(tweet_id="t-old", model="x", is_finance=True,
                            overall_sentiment="positive", summary="s",
                            raw_json="{}", latency_ms=10, created_at=old))
        s.commit()
        ex_id = s.query(ExtractionRow).filter(ExtractionRow.tweet_id == "t-old").first().id
        s.add(ExtractedTickerRow(extraction_id=ex_id, ticker="NVDA",
                                 sentiment="positive", confidence=0.9, explicit=True))
        s.add(ExtractedThemeRow(extraction_id=ex_id, theme="ai_infrastructure",
                                sentiment="positive", confidence=0.9))
        s.commit()

    with factory() as s:
        counts = prune_old_data(s, tweets_keep_days=30)
        assert counts["tweets"] == 1
        assert counts["extractions"] == 1
        # Recent tweet survives
        assert s.query(TweetRow).count() == 1
        # No orphan rows
        assert s.query(ExtractedTickerRow).count() == 0
        assert s.query(ExtractedThemeRow).count() == 0


def test_prune_no_op_on_empty_db(tmp_path):
    factory = _make_session(tmp_path)
    with factory() as s:
        counts = prune_old_data(s)
    assert sum(counts.values()) == 0
