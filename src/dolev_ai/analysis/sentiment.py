"""FinBERT sentiment wrapper. Lazy-loads model on first call."""
from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

SentimentLabel = Literal["positive", "negative", "neutral"]

_pipeline = None  # lazy


def _get_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    try:
        from transformers import pipeline as hf_pipeline
        _pipeline = hf_pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )
        logger.info("FinBERT loaded")
    except Exception as e:
        logger.warning(f"FinBERT unavailable ({e}); falling back to neutral")
        _pipeline = None
    return _pipeline


def score_tweet(text: str) -> tuple[SentimentLabel, float]:
    """Return (label, confidence) for a single tweet text."""
    pipe = _get_pipeline()
    if pipe is None:
        return "neutral", 0.5
    try:
        result = pipe(text[:512])[0]
        label = result["label"].lower()
        if label not in ("positive", "negative", "neutral"):
            label = "neutral"
        return label, float(result["score"])  # type: ignore[return-value]
    except Exception:
        return "neutral", 0.5


def score_batch(texts: list[str]) -> list[tuple[SentimentLabel, float]]:
    """Score multiple texts. Falls back to per-item if batch fails."""
    pipe = _get_pipeline()
    if pipe is None:
        return [("neutral", 0.5)] * len(texts)
    try:
        truncated = [t[:512] for t in texts]
        results = pipe(truncated)
        out = []
        for r in results:
            label = r["label"].lower()
            if label not in ("positive", "negative", "neutral"):
                label = "neutral"
            out.append((label, float(r["score"])))
        return out  # type: ignore[return-value]
    except Exception:
        return [score_tweet(t) for t in texts]
