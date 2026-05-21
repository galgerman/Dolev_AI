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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 1.5 — Setup Skills + Live Monitoring Web UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## Context (Phase 1.5)

Phase 1 emits Telegram alerts but is a black box — the user has no visibility into *why* the agent is on the path it's on, and no way to watch conviction build toward the threshold before a signal fires. Two additions:

1. **Two Claude Code skills** for repeatable setup / run, so a fresh clone is one command away from running.
2. **A live monitoring web UI** that visualizes the trust graph filling in, ticker scores climbing toward the threshold, and the live tweet stream feeding the score — answering "how and why is the agent on this path?" in real time.

The UI is the more important piece: it's the validation layer that lets the user trust the system enough to eventually grant it execution autonomy in Phase 2.

### Locked decisions (Phase 1.5)

| Decision | Choice |
|---|---|
| Frontend | React + Vite + TypeScript + Tailwind + D3 + Recharts |
| Real-time channel | WebSocket (push from agent → browser) |
| Backend web layer | FastAPI mounted into the existing asyncio daemon process |
| Views | Threshold leaderboard, trust graph, score-over-time chart, ticker drilldown |
| Deployment | Local only, `http://localhost:8000` (no auth, no TLS) |

---

## A. Skills

Two skills under `.claude/skills/`, each with a `SKILL.md` containing frontmatter + procedure.

### A1. `setup-dolev-ai`

`.claude/skills/setup-dolev-ai/SKILL.md`

Frontmatter:
```
---
name: setup-dolev-ai
description: One-shot setup for the Dolev AI Twitter trend agent on a fresh clone. Installs Python deps, Playwright Chromium, Node deps for the web UI, downloads NYSE/NASDAQ symbol universe, scaffolds .env, and walks the user through one-time X login.
---
```

Procedure the skill executes (in order):
1. Verify Python 3.11+ (`python --version`).
2. `pip install -e ".[dev]"`.
3. `playwright install chromium`.
4. Verify Node 18+ (`node --version`); if missing, instruct user to install from nodejs.org and stop.
5. `cd web && npm install` (frontend dependencies — see Section B).
6. If `.env` missing: copy from `.env.example` and prompt user to fill `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (skill explains how to create a Telegram bot via @BotFather, and how to get a chat ID).
7. If `config/universe.csv` missing or older than 7 days: `python scripts/download_universe.py`.
8. If `browser_profile/` missing or empty: `python scripts/login_x.py` and wait for the user to log in.
9. Run `pytest -q -m "not slow"` to confirm the install works.
10. Print "Setup complete. Run `/run-dolev-ai` to start the agent."

### A2. `run-dolev-ai`

`.claude/skills/run-dolev-ai/SKILL.md`

Frontmatter:
```
---
name: run-dolev-ai
description: Run the Dolev AI agent + monitoring UI. Optional flags for dry-run mode (no Telegram alerts) and dev mode (live-reload frontend on port 5173). Runs pytest first as a smoke test.
---
```

Procedure:
1. Run `pytest -q -m "not slow"` — abort if any test fails.
2. Verify `.env` exists and contains all required keys.
3. Build frontend if `web/dist/` missing or out of date: `cd web && npm run build`.
4. Ask user: dry-run (alerts to stdout) or live (Telegram)? Default dry-run.
5. Ask user: dev mode (Vite live-reload at :5173 + uvicorn at :8000) or production (single FastAPI server on :8000 serving built assets)? Default production.
6. Start the agent: `python -m dolev_ai.main [--dry-run]`.
7. Open `http://localhost:8000` in the default browser.
8. Tail the logs in the terminal until Ctrl+C.

---

## B. Live Monitoring Web UI

### B1. Architecture

```
┌──────────────────────────── dolev_ai.main process ──────────────────────────┐
│                                                                              │
│  Agent (existing)                              FastAPI app (new)             │
│  ──────────────────────                        ─────────────────             │
│  PlaywrightSource.collect ──┐                  REST endpoints                │
│  Aggregator.evaluate       │                   /api/tickers/live            │
│  Synthesizer ──────────────┤   ┌─────────────► /api/tickers/{ticker}        │
│  TrustGraphStrategy        │   │               /api/signals/recent          │
│  TelegramAlerter           │   │               /api/graph/snapshot          │
│                            │   │                                            │
│                            ▼   │               WebSocket /api/stream        │
│                       ┌─────────┴────┐         ──────────────────           │
│                       │   EventBus   │ ────►   broadcasts events to all     │
│                       │ (asyncio pub │         connected browser tabs       │
│                       │   /sub)      │                                      │
│                       └──────────────┘                                      │
│                            ▲                                                 │
│                            │                  Static files                  │
│                            │                  ──────────────                 │
│                            │                  GET / → web/dist/index.html   │
│                            │                  GET /assets/* → web/dist/...  │
│                            │                                                │
│                            └──── publish(events) from Agent.collect/evaluate │
└──────────────────────────────────────────────────────────────────────────────┘
                                       ▲
                                       │
                                       │ WebSocket + REST over localhost:8000
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            │  Browser SPA (React + Vite + D3 + Recharts)         │
            │                                                     │
            │  ┌─────────────────────────┐ ┌────────────────────┐ │
            │  │ Leaderboard + Gauges    │ │  Trust Graph (D3)  │ │
            │  │  ticker | score | bar   │ │  accounts ↔ tickers │ │
            │  └─────────────────────────┘ └────────────────────┘ │
            │  ┌─────────────────────────┐ ┌────────────────────┐ │
            │  │  Score-over-time chart  │ │  Ticker drilldown  │ │
            │  │  (multi-line + thresh.) │ │  (click ticker)    │ │
            │  └─────────────────────────┘ └────────────────────┘ │
            └─────────────────────────────────────────────────────┘
```

### B2. Event Bus & Event Types

`src/dolev_ai/events.py` — hand-rolled async pub/sub:

```python
class EventBus:
    """Async pub/sub. Multiple WS subscribers, bounded queues, drops on overflow."""
    def subscribe(self) -> asyncio.Queue[dict]: ...
    def unsubscribe(self, q: asyncio.Queue) -> None: ...
    async def publish(self, event: dict) -> None: ...
```

Event types (JSON over WS):

| `type` | Payload | Emitted when |
|---|---|---|
| `tweet.ingested` | `{tweet: RawTweet, tickers: [str], sentiment: str, sentiment_score: float}` | Every newly-saved tweet |
| `ticker.score_updated` | `{ticker, score, voices, tweet_count, threshold, threshold_progress}` | After each `evaluate()` cycle, per ticker |
| `ticker.near_threshold` | `{ticker, score, threshold, percent}` | When threshold_progress ≥ 0.75 (and changed since last tick) |
| `signal.synthesizing` | `{ticker}` | Right before Claude call |
| `signal.fired` | `{signal: Signal}` | After synthesizer + alerter |
| `graph.edge_added` | `{author, ticker, weight, sentiment}` | When a new (author, ticker) edge appears |

The Agent (`main.py`) gets an `EventBus` injected and publishes at each pipeline stage. The aggregator returns ticker scores AND threshold_progress = `abs(score) / threshold` ∈ [0, ∞).

### B3. FastAPI Server

`src/dolev_ai/web/server.py` — mounted into the asyncio daemon, NOT a separate process:

- `lifespan` context: shares the `EventBus`, `session_factory`, and config with the Agent
- Uvicorn started inside `Agent.run()` via `uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning"))` as an asyncio task alongside the APScheduler

`src/dolev_ai/web/api.py` — REST endpoints (all return JSON):

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{ok: true, started_at, threshold}` |
| GET | `/api/tickers/live` | Top N tickers by abs score from last eval cycle |
| GET | `/api/tickers/{ticker}` | Drilldown: score timeline (last 4h), contributing tweets, contributing accounts |
| GET | `/api/signals/recent?limit=20` | Most recent fired signals |
| GET | `/api/graph/snapshot` | Current trust-graph nodes + edges (accounts + tickers, last 60min) |
| GET | `/api/accounts` | All seed accounts with credibility weights |

`src/dolev_ai/web/ws.py` — WebSocket endpoint `/api/stream`:
- On connect: send a `snapshot` event with current top tickers + recent signals (so the UI hydrates immediately, no blank state)
- Then forward every event from the EventBus until the client disconnects
- Handle disconnect cleanly (unsubscribe from bus)

### B4. Frontend SPA

Folder: `web/` (sibling of `src/`, NOT inside it — kept separate so `npm` / `node_modules` don't pollute the Python tree).

```
web/
├── package.json
├── vite.config.ts          # proxy /api → http://localhost:8000 in dev
├── tailwind.config.js
├── tsconfig.json
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx             # layout: 2x2 grid of panels
    ├── types.ts            # mirrors Python dataclasses (Signal, TickerScore, ...)
    ├── api.ts              # fetch wrappers for /api/*
    ├── hooks/
    │   ├── useEventStream.ts   # WebSocket hook, reconnects on drop
    │   └── useLiveTickers.ts   # combines REST snapshot + WS deltas
    └── components/
        ├── Header.tsx              # title, agent status (running/stopped), last-eval timestamp
        ├── Leaderboard.tsx         # see B5
        ├── TrustGraph.tsx          # D3 force layout, see B6
        ├── ScoreChart.tsx          # Recharts multi-line, see B7
        ├── TickerDrilldown.tsx     # modal/side-panel on row click, see B8
        ├── ThresholdProgressBar.tsx # reused inside Leaderboard rows
        └── SignalFiredToast.tsx    # animated overlay when signal.fired arrives
```

`vite.config.ts` proxies `/api/*` → `localhost:8000` so dev mode (`npm run dev` on :5173) talks to FastAPI without CORS pain. Production: `npm run build` outputs to `web/dist/`, FastAPI serves it as static.

`package.json` deps:
- runtime: `react`, `react-dom`, `d3`, `recharts`, `clsx`, `zustand` (tiny store for shared state)
- dev: `vite`, `@vitejs/plugin-react`, `typescript`, `tailwindcss`, `postcss`, `autoprefixer`, `@types/d3`, `@types/react`

### B5. Leaderboard component

Sorted list of top 15 tickers by `|score|`, refreshed live from WS deltas. Each row:

```
┌────────────────────────────────────────────────────────────────────────┐
│  $NVDA   ↑ +7.2   ▰▰▰▰▰▰▰▰▰▱▱▱  144% of threshold     5 voices  ⬛⬛⬛   │
│  $TSLA   ↓ -4.1   ▰▰▰▰▰▰▰▱▱▱▱▱   82% of threshold     4 voices  ⬛⬛     │
│  $MSFT   ↑ +2.8   ▰▰▰▰▱▱▱▱▱▱▱▱   56% of threshold     3 voices  ⬛       │
└────────────────────────────────────────────────────────────────────────┘
```

Visual rules:
- Bar color: gray < 50%, yellow 50-75%, orange 75-100%, green/red ≥ 100% (green buy, red sell)
- When `ticker.near_threshold` fires for a ticker, that row pulses for 2s
- Sparkline at right shows last 12 minutes of score
- Click row → opens TickerDrilldown panel

### B6. TrustGraph component (the headline visualization)

D3 force-directed network. Two node types:
- **Account nodes** (left/perimeter): one per seed account that has tweeted in the last 60 min. Size ∝ credibility. Color by tier.
- **Ticker nodes** (right/center): one per scored ticker. Size ∝ `|score|`. Color: green for buy-leaning, red for sell-leaning, gray for neutral.

Edges: account → ticker mention. Edge width ∝ `credibility × sentiment_confidence × engagement_weight`. Edge color matches sentiment (green/red).

Animation:
- New edges fade in when `graph.edge_added` arrives
- Ticker nodes "grow" when score increases (smooth radius transition over 400ms)
- When `signal.fired` arrives for a ticker, that node flashes and a brief ripple animation propagates along its edges

Controls:
- Slider: window size (15min / 1h / 4h)
- Toggle: show neutral edges
- Hover a node → highlight its connected subgraph, dim everything else
- Click a ticker node → opens drilldown (same as Leaderboard click)

### B7. ScoreChart component

Recharts `<LineChart>` with one line per top-5 ticker, X-axis = time (last 1h, configurable), Y-axis = score. Threshold drawn as a horizontal `<ReferenceLine>` at `+THRESHOLD` and `-THRESHOLD`. Vertical `<ReferenceLine>` markers when signals fired (with ticker emoji label).

Backend feeds this from the `ticker_scores` SQLite table (already persisted by Phase 1).

### B8. TickerDrilldown component

Side panel (slides in from right). Shows for the selected ticker:
- Header: ticker, current score, voices, distance to threshold
- Score timeline (single-line zoomable chart, last 4h)
- Sentiment breakdown (donut chart: positive/negative/neutral tweet counts)
- Contributing accounts table: handle | tier | credibility | tweet count | total contribution (sorted by contribution desc) — click handle to see their tweets in modal
- Contributing tweets feed: chronological, newest first, with sentiment badge and engagement counts

### B9. Wiring it together

`Agent.__init__` accepts an optional `EventBus`. Inside `collect()`, after `save_tweets`, publish one `tweet.ingested` event per new tweet (including the ticker extraction + sentiment so the UI doesn't redo work). Inside `evaluate()`, after `aggregate()`, publish `ticker.score_updated` for every scored ticker AND `ticker.near_threshold` for any whose `threshold_progress` crossed 0.75 upward this cycle. Before synthesizer call: `signal.synthesizing`. After alerter: `signal.fired`.

Web server is started from `Agent.run()`:
```python
config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
server = uvicorn.Server(config)
asyncio.create_task(server.serve())
```
Shutdown handled by the existing SIGINT/SIGTERM path (`server.should_exit = True`).

---

## Critical Files (Phase 1.5)

**New backend files:**
- `src/dolev_ai/events.py` — `EventBus`
- `src/dolev_ai/web/__init__.py`
- `src/dolev_ai/web/server.py` — FastAPI app + static mount + uvicorn integration
- `src/dolev_ai/web/api.py` — REST handlers
- `src/dolev_ai/web/ws.py` — WebSocket handler
- `src/dolev_ai/web/schemas.py` — pydantic models for response shapes (mirrors `models.py`)

**Modified backend files:**
- `src/dolev_ai/main.py` — inject EventBus, publish events at each stage, start uvicorn task
- `src/dolev_ai/analysis/aggregator.py` — return `threshold_progress` on each `TickerScore` (or compute it in the strategy)
- `pyproject.toml` — add `fastapi`, `uvicorn[standard]`, `pydantic`, `websockets`

**New frontend files (`web/`):** as listed in B4.

**New skills:**
- `.claude/skills/setup-dolev-ai/SKILL.md`
- `.claude/skills/run-dolev-ai/SKILL.md`

**Reused from Phase 1:**
- `src/dolev_ai/models.py` — dataclasses, mirrored in `schemas.py` and `web/src/types.ts`
- `src/dolev_ai/db.py` — `load_tweets_since`, `ticker_scores` table read by drilldown endpoint
- `src/dolev_ai/analysis/aggregator.py:aggregate` — same scoring logic, extended with threshold_progress
- `src/dolev_ai/strategies/trust_graph.py` — threshold value is the source of truth for "near threshold" detection

## Verification (Phase 1.5)

**Backend unit:**
- `tests/test_event_bus.py` — publish broadcasts to all subscribers, full queue drops, unsubscribe stops delivery
- `tests/test_api.py` — FastAPI TestClient: `/api/tickers/live` returns expected shape with seeded DB, `/api/tickers/UNKNOWN` returns 404
- `tests/test_ws.py` — WebSocket client connects, receives snapshot event, then receives a published event

**Frontend:** minimal — type-check (`tsc --noEmit`) and a single Vitest smoke test that `App.tsx` renders without crashing given a mocked WebSocket.

**Skill smoke test:**
- On a clean checkout (or fresh git worktree): run `/setup-dolev-ai` from scratch. Confirm `.env`, `universe.csv`, `browser_profile/` all exist after; pytest passes.
- Then `/run-dolev-ai` in dry-run mode. Confirm browser opens at `localhost:8000`, leaderboard populates within 30s of the first eval cycle.

**End-to-end manual:**
1. Start the agent in dry-run.
2. Watch the trust graph fill in during market hours.
3. Wait for a `ticker.near_threshold` event — confirm row pulses and the ticker node grows.
4. Wait for a `signal.fired` event — confirm toast appears, node flashes, drilldown shows the synthesized rationale.
5. Click a top ticker — confirm drilldown loads contributing accounts and tweets.

---

## Out of Scope (Phase 2+)

- Order execution (Alpaca/IBKR adapter consuming `Signal`)
- Historical-accuracy credibility scoring (compare past signals vs. realized returns)
- Cashtag firehose + news-anchored signal strategies (plug into `SignalStrategy` ABC)
- StockTwits / X API alternate `TweetSource` implementations
- Position management, risk limits, portfolio-level exposure caps
- Backtester
- Mobile-responsive UI (Phase 1.5 is desktop-first; mobile comes when alerts need on-the-go context)
- Auth / multi-user / public deployment
