"""Quick smoke test for the Ollama-backed synthesizer."""
from datetime import datetime
from dolev_ai.synth.synthesizer import Synthesizer
from dolev_ai.models import TickerScore

ts = TickerScore(
    ticker="XLE",
    score=12.5,
    unique_credible_voices=7,
    tweet_count=15,
    window_start=datetime.utcnow(),
    window_end=datetime.utcnow(),
    top_tweet_urls=["https://x.com/user/status/1"],
    threshold_progress=0.83,
    direct_score=8.0,
    cascade_score=4.5,
)

tweets = [
    "Energy sector looking extremely strong. XLE breaking out of consolidation.",
    "Oil inventories down more than expected. Bullish for energy stocks $XLE $CVX $XOM",
    "OPEC+ surprise cut confirmed. Energy trade is on.",
]

print("Calling synthesizer (Ollama)...")
s = Synthesizer()
signal = s.synthesize("XLE", ts, tweets)
print(f"side:           {signal.side}")
print(f"conviction:     {signal.conviction:.2f}")
print(f"suggested_size: {signal.suggested_size_pct:.1%}")
print(f"rationale:      {signal.rationale}")
print(f"key_drivers:    {signal.key_drivers}")
