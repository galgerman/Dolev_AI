"""Tests for ticker extraction."""
import pytest
from unittest.mock import patch
from dolev_ai.analysis.ticker import extract_tickers


# Patch universe to include test symbols
MOCK_UNIVERSE = frozenset({"NVDA", "TSLA", "MSFT", "AAPL", "AMZN", "SPY", "QQQ", "A", "T"})


@pytest.fixture(autouse=True)
def mock_universe(monkeypatch):
    import dolev_ai.analysis.ticker as ticker_mod
    monkeypatch.setattr(ticker_mod, "_universe", MOCK_UNIVERSE)


def test_simple_cashtag():
    assert "NVDA" in extract_tickers("$NVDA is ripping today")


def test_multiple_tickers():
    tickers = extract_tickers("$MSFT and $AAPL both hitting ATH")
    assert "MSFT" in tickers
    assert "AAPL" in tickers


def test_no_dollar_prefix_ignored():
    assert extract_tickers("Apple stock up 3 percent") == []


def test_single_letter_blocklist():
    # $A is in universe but in blocklist check — actually $A isn't in blocklist
    # but $USD is; let's test currency blocklist
    assert "USD" not in extract_tickers("The $USD is weakening vs EUR")


def test_false_positive_word_T():
    assert extract_tickers("the letter T means nothing here") == []


def test_nvda_in_sentence():
    tickers = extract_tickers("$NVDA hits ATH on massive earnings beat")
    assert tickers == ["NVDA"]


def test_no_tickers():
    assert extract_tickers("The market is up today, feeling good") == []


def test_deduplication():
    tickers = extract_tickers("$NVDA is up, $NVDA is really up")
    assert tickers.count("NVDA") == 1


def test_valid_etf():
    tickers = extract_tickers("Rotating into $SPY and $QQQ")
    assert "SPY" in tickers
    assert "QQQ" in tickers
