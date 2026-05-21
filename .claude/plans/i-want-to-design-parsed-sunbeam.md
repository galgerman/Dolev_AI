# Twitter-Driven Stock Trend Agent — Phase 1 (Research & Alerting)

## Context

Build a Python agent that watches finance Twitter, detects emerging trends per US equity ticker (NYSE + NASDAQ, open universe), and delivers structured real-time **trade-signal alerts** to Telegram when conviction crosses a threshold.

This is phase 1: **no order execution**. The goal is to validate signal quality. Phase 2 (autonomous buy/sell via a broker like Alpaca or IBKR) will consume the same signal objects this phase emits, so no rewrite is needed.

Greenfield project — `C:\CodeProjects\dolev_ai` is empty.

### Decisions already locked

| Decision | Choice |
|---|---|
| Language | Python 3.11+ |
| Data access | Playwright with user's logged-in X session, behind a `TweetSource` abstraction so X API / Apify / StockTwits can swap in later |
| Signal strategy | Hybrid trust graph (curated seeds + their retweets/replies), behind a `SignalStrategy` abstraction |
| Universe | Open — any NYSE/NASDAQ ticker that gets mentioned by seed accounts |
| Output | Real-time Telegram alerts on threshold breach |
| Alert shape | Structured trade-signal objects from day 1 (`ticker, side, conviction, suggested_size_pct, rationale, supporting_tweets`) |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    main.py  (asyncio daemon)                    │
└──────────┬──────────────────────────────────────────────────────┘
           │ schedules every N minutes
           ▼
┌──────────────────────┐    ┌──────────────────────────────────┐
│   TweetSource (ABC)  │◄───┤  PlaywrightSource                │
│   .fetch(query/acct) │    │  - persistent browser profile    │
└──────────┬───────────┘    │  - polls seed accounts + their   │
           │                │    retweets/replies in feed      │
           ▼                └──────────────────────────────────┘
┌──────────────────────┐
│  analysis pipeline   │
│  ──────────────────  │
│  TickerExtractor     │  regex $[A-Z]{1,5} + validate against universe.csv
│  Sentiment (FinBERT) │  bullish / bearish / neutral + confidence
│  Credibility         │  per-account weight: followers, verified, hit-rate
│  Aggregator          │  rolling per-ticker score with time decay
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐   ┌──────────────────────────────────┐
│ SignalStrategy (ABC) │◄──┤ TrustGraphStrategy               │
│ .evaluate(scores)    │   │  - ≥N independent credible voices │
│  → list[Signal]      │   │  - cooldown per ticker            │
└──────────┬───────────┘   │  - threshold breach detection     │
           │               └──────────────────────────────────┘
           ▼
┌──────────────────────┐
│  Synthesizer         │  Claude API: top tweets for ticker → JSON
│  (Anthropic SDK)     │  {side, conviction, suggested_size_pct,
│                      │   rationale, key_drivers}
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐    ┌──────────────────────────────────┐
│   Alerter            │───►│  Telegram Bot API                │
│   (formats & sends)  │    │  rich message + supporting links │
└──────────────────────┘    └──────────────────────────────────┘

           ▲
           │  all stages persist to:
           │
┌──────────────────────┐
│  SQLite (SQLAlchemy) │  tweets, accounts, ticker_scores,
│                      │  signals, alert_log
└──────────────────────┘
```

## Pluggable Interfaces

These two interfaces are the key extension points — phase 2 and future signal strategies plug in here without touching the rest.

```python
# src/dolev_ai/sources/__init__.py
class TweetSource(ABC):
    @abstractmethod
    async def fetch_user_timeline(self, handle: str, since: datetime) -> list[RawTweet]: ...
    @abstractmethod
    async def fetch_feed_engagements(self, handles: list[str], since: datetime) -> list[RawTweet]: ...

# src/dolev_ai/strategies/__init__.py
class SignalStrategy(ABC):
    @abstractmethod
    def evaluate(self, ticker_scores: dict[str, TickerScore]) -> list[Signal]: ...
```

`Signal` is the contract phase 2 will consume:

```python
@dataclass
class Signal:
    ticker: str
    side: Literal["buy", "sell"]
    conviction: float           # 0..1
    suggested_size_pct: float   # of portfolio, 0..1
    rationale: str              # 2-4 sentences, from Claude
    key_drivers: list[str]      # tweet IDs / URLs
    generated_at: datetime
```

## File Layout

```
dolev_ai/
├── pyproject.toml                      # uv / hatch
├── README.md
├── .env.example                        # ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
├── config/
│   ├── seeds.yaml                      # curated account list (see below)
│   ├── universe.csv                    # NYSE + NASDAQ symbols (sourced from nasdaqtrader.com FTP)
│   └── settings.yaml                   # thresholds, poll cadences, cooldowns
├── src/dolev_ai/
│   ├── main.py                         # daemon entry, asyncio scheduler
│   ├── models.py                       # Tweet, Account, TickerScore, Signal
│   ├── db.py                           # SQLAlchemy + SQLite (data/dolev.db)
│   ├── sources/
│   │   ├── __init__.py                 # TweetSource ABC
│   │   └── playwright_source.py
│   ├── strategies/
│   │   ├── __init__.py                 # SignalStrategy ABC
│   │   └── trust_graph.py
│   ├── analysis/
│   │   ├── ticker.py                   # $XYZ extractor + universe validation
│   │   ├── sentiment.py                # FinBERT wrapper (transformers)
│   │   ├── credibility.py              # per-account weighting
│   │   └── aggregator.py               # rolling per-ticker score + time decay
│   ├── synth/
│   │   └── synthesizer.py              # Claude call → Signal
│   └── alert/
│       └── telegram.py
└── tests/
    ├── test_ticker.py
    ├── test_aggregator.py
    ├── test_trust_graph.py
    └── fixtures/tweets.json
```

## Key Components

### 1. PlaywrightSource (`sources/playwright_source.py`)
- Launches Chromium with a **persistent profile dir** so login cookies survive restarts. User does X login manually once.
- Two operations:
  - `fetch_user_timeline(handle)` → navigates `x.com/<handle>`, scrolls, parses tweets, extracts text + engagement counts + timestamp.
  - `fetch_feed_engagements(handles)` → for trust-graph expansion, scrape replies/quotes/retweets *by* seed accounts (`x.com/<handle>/with_replies`).
- Rate-limit defensively: 1 request per 3-5s with jitter, randomized User-Agent, headful is fine. Cap total accounts/poll at ~50.
- Selectors live in one module so they can be patched fast when X changes the DOM.
- Returns normalized `RawTweet(id, author, text, created_at, like_count, retweet_count, reply_count, url)`.

### 2. Universe + Ticker Extraction (`analysis/ticker.py`)
- `config/universe.csv` — download from `ftp://ftp.nasdaqtrader.com/symboldirectory/` (nasdaqlisted.txt + otherlisted.txt). Refresh weekly.
- Extractor: regex `\$[A-Z]{1,5}\b` → validate against universe set. **Reject** $A, $T, $I, etc. unless `$`-prefixed (the prefix is what makes it a ticker, not the letter).
- Blocklist common false-positive caps that overlap symbols (e.g. $USD when discussing dollars, not the ProShares ETF — context check).

### 3. Sentiment (`analysis/sentiment.py`)
- Use `ProsusAI/finbert` (transformers, runs locally, free). Per tweet → `(label, confidence)` where label ∈ {positive, negative, neutral}.
- Lazy-load model once at startup.
- If hardware can't run FinBERT comfortably, fallback: small Claude Haiku batch call per N tweets.

### 4. Credibility (`analysis/credibility.py`)
- Per-account score `0..1` from: followers (log-scaled), verified flag, manual tier override in `seeds.yaml` (`tier: 1-3`).
- Phase 1: static. Reserve hook for future "historical accuracy" score (compare past signals vs. market move).

### 5. Aggregator (`analysis/aggregator.py`)
- For each `(ticker, time_window=60min)` compute:
  ```
  score = Σ tweets [ sentiment_signed × confidence × credibility(author) × engagement_weight × time_decay ]
  ```
  - `sentiment_signed`: +1/-1/0 from FinBERT label
  - `time_decay`: exp(−Δt/30min)
  - `engagement_weight`: log(1 + likes + 2·retweets)
- Also track `unique_credible_voices` count per ticker.
- Persists `TickerScore` rows for later inspection and threshold tuning.

### 6. TrustGraphStrategy (`strategies/trust_graph.py`)
- Emits a `Signal` when **all** hold:
  - `unique_credible_voices ≥ 3` (configurable) in last 30 min
  - `|score| ≥ THRESHOLD` (start at 5.0, tune from logs)
  - ticker not in cooldown (default 4 hours; bypass if `|score|` doubles vs. last alert)
- Side is `buy` if score > 0, `sell` if score < 0.
- Initial size hint: `min(0.05, conviction × 0.10)` — Synthesizer can override.

### 7. Synthesizer (`synth/synthesizer.py`)
- Input: ticker + top 10 driving tweets + aggregate score + recent price context (optional, yfinance).
- Calls Claude (claude-sonnet-4-6, lower cost than Opus for this routine task) with a tight prompt requesting **strict JSON** matching the `Signal` schema, plus rationale ≤ 4 sentences.
- Uses Anthropic SDK with **prompt caching** on the system prompt (it's stable across calls).
- Validates JSON; rejects/retries once on schema failure.

### 8. Telegram Alerter (`alert/telegram.py`)
- Plain `httpx` POST to `https://api.telegram.org/bot<TOKEN>/sendMessage`. No heavy framework needed.
- Message format (Markdown):
  ```
  🟢 BUY  $NVDA   conviction 0.78  size hint 7%
  Driven by 5 credible voices in the last 28 min.
  Rationale: <synthesizer text>
  Top tweets: <urls>
  ```
- Logs every alert to `alert_log` table (idempotency: don't double-fire on daemon restart).

### 9. Daemon Loop (`main.py`)
- `asyncio` with `apscheduler` (AsyncIOScheduler).
- Two jobs:
  - **collect** every 5 min: PlaywrightSource → DB
  - **evaluate** every 2 min: load recent tweets → analysis pipeline → strategy → alerter
- Graceful shutdown on SIGINT/SIGTERM, flushes DB.

## Initial Seed Accounts (`config/seeds.yaml`)

Start with ~50, organized by tier (credibility multiplier). The implementer should expand to 80-100. **Anchor set:**

- **News/wire (tier 1):** `@DeItaone`, `@FirstSquawk`, `@financialjuice`, `@WSJmarkets`, `@markets`, `@CNBCnow`, `@Reuters`, `@business`, `@StockMKTNewz`
- **Macro/strategy (tier 1):** `@LizAnnSonders`, `@charliebilello`, `@SoberLook`, `@lisaabramowicz1`, `@LawrenceMcDonald`
- **Options/flow (tier 2):** `@unusual_whales`, `@cheddar_flow`, `@SqueezeMetrics`
- **Fund managers / writers (tier 2):** `@michaelbatnick`, `@ritholtz`, `@morganhousel`, `@HowardLindzon`
- **Retail/momentum watchers (tier 3):** `@zerohedge`, `@Mayhem4Markets`

Tier 1 = ×1.0, Tier 2 = ×0.7, Tier 3 = ×0.4 credibility weight. Override per-account in YAML.

## Configuration (`config/settings.yaml`)

```yaml
poll_cadence_minutes: 5
eval_cadence_minutes: 2
score_window_minutes: 60
trust_graph:
  min_credible_voices: 3
  score_threshold: 5.0
  cooldown_hours: 4
  cooldown_override_multiplier: 2.0
sentiment:
  model: ProsusAI/finbert
  device: cpu        # or cuda
synth:
  model: claude-sonnet-4-6
  cache_system_prompt: true
universe:
  refresh_days: 7
```

## Critical Files to Create

- `pyproject.toml` — deps: `playwright`, `anthropic`, `transformers`, `torch`, `sqlalchemy`, `apscheduler`, `httpx`, `pyyaml`, `python-dotenv`, `pandas`, `yfinance` (optional)
- All files under `src/dolev_ai/` as laid out above
- `config/seeds.yaml`, `config/settings.yaml`
- `scripts/download_universe.py` — pulls NYSE/NASDAQ symbol files
- `scripts/login_x.py` — opens headful Playwright so user logs in once

## Verification

**Unit (pytest):**
- `test_ticker.py` — `$NVDA hits ATH`, `Apple stock up`, `$A vs Agilent`, `the letter T means nothing here`. Confirm only valid `$`-prefixed universe tickers extracted.
- `test_aggregator.py` — fixture of 20 tweets across 3 tickers, assert ranked scores, time-decay correctness.
- `test_trust_graph.py` — assert no signal under threshold, signal at threshold, cooldown enforced, cooldown override on 2× score.

**Integration:**
- `test_playwright_source.py` (slow, skipped by default) — fetch own timeline, assert ≥1 tweet with required fields.
- End-to-end with fixture tweets seeded into DB → run one eval cycle → assert Telegram alerter called with correct `Signal`.

**Manual smoke test:**
1. `python scripts/download_universe.py` — confirm `universe.csv` populated.
2. `python scripts/login_x.py` — log in to X, close browser.
3. `python -m dolev_ai.main --dry-run` — runs collector + eval but routes alerts to stdout instead of Telegram. Leave running 2-4 hours during market hours; inspect emitted signals, hand-check 2-3 against the actual tweets and intraday price moves.
4. Flip off `--dry-run`, confirm alerts arrive on Telegram.
5. Run for 1 week, calibrate `score_threshold` from `ticker_scores` table histogram.

## Out of Scope (Phase 2+)

- Order execution (Alpaca/IBKR adapter consuming `Signal`)
- Historical-accuracy credibility scoring (compare past signals vs. realized returns)
- Cashtag firehose + news-anchored signal strategies (plug into `SignalStrategy` ABC)
- StockTwits / X API alternate `TweetSource` implementations
- Position management, risk limits, portfolio-level exposure caps
- Backtester
