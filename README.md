# Dolev AI — Twitter Stock Trend Agent

Watches finance Twitter via Playwright, asks a **local LLM** what each post implies for markets, builds a trust graph (accounts → tickers + themes → tickers), and fires structured trade-signal alerts to Telegram when conviction crosses a threshold.

A live monitoring dashboard at `http://localhost:8000` shows extractions, the trust graph, the leaderboard, and a built-in DB browser.

Phase 2 (autonomous order execution via Alpaca/IBKR) will consume the same `Signal` objects this phase emits — no rewrite needed.

---

## Quick start

```bash
# 1. Python deps (3.12+)
py -3.12 -m pip install -e ".[dev]"

# 2. Playwright (only needed for the headless shell; scraping uses your system Chrome/Edge)
playwright install chromium

# 3. Local LLM — install Ollama, then pull a model
#    https://ollama.com/download
ollama pull qwen2.5:7b          # default; ~4.7GB, needs ~6GB VRAM

# 4. Frontend
cd web && npm install && npm run build && cd ..

# 5. Pick the LLM model for your hardware
py -3.12 scripts/probe_hardware.py
#   → detects VRAM / RAM, picks from config/models.yaml,
#     writes config/active_model.yaml

# 6. NYSE + NASDAQ symbol universe
py -3.12 scripts/download_universe.py

# 7. Environment variables
cp .env.example .env
# Fill in ANTHROPIC_API_KEY (signal synthesis), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# Dummy values are fine for dry-run preview — synth + alerts will fail silently

# 8. Run
py -3.12 -m dolev_ai.main --dry-run

# 9. Open dashboard
# http://localhost:8000  → click "Connect X" to log in to X.com (one-time)
```

Once X is connected, the agent will start the 5-minute collect cycle automatically. The first extraction may take 30–45s while Ollama loads the model into VRAM — subsequent calls are ~5–28s each depending on model size.

---

## Architecture

```
APScheduler
  ├── collect()  every 5 min  →  Playwright scrape → save_tweets → submit to extractor
  ├── evaluate() every 2 min  →  load extractions → score → strategy → synth → telegram
  └── prune()    every 60 min →  trim old time-series rows (24h) + tweets (30 days)

ExtractionWorker (2 parallel workers, always running)
  bounded queue (1000, drop-oldest)
    → POST http://localhost:11434/v1/chat/completions  (one call per tweet)
    → persist Extraction → publish llm.call + post.extracted WS events

Scoring
  direct_score(ticker) + 0.5 × Σ themes [theme_score × theme_weights[ticker]]
  → TrustGraphStrategy fires Signal when |score| ≥ 5.0 AND voices ≥ 3 AND not in cooldown
  → Claude synthesizer (claude-sonnet-4-6) writes rationale + sizing
  → Telegram alert

Web UI (FastAPI in same event loop)
  /api/* REST + /api/stream WebSocket → React + D3 + Recharts SPA
```

---

## Required infrastructure

| Component | Default | How to get |
|---|---|---|
| Python | 3.12+ | python.org |
| Node.js | 18+ | nodejs.org |
| Ollama | 0.24+ on `:11434` | https://ollama.com/download |
| LLM model | `qwen2.5:7b` (~4.7GB, needs 6GB VRAM) | `ollama pull qwen2.5:7b` |
| Browser | system Chrome **or** Edge | already installed on Windows 11 |
| Anthropic API key | for signal synthesis only (optional in dry-run) | console.anthropic.com |
| Telegram bot | for alerts (optional in dry-run) | @BotFather on Telegram |

**Hardware sizing**: edit `config/models.yaml` and re-run `scripts/probe_hardware.py`. Candidates range from `llama3.2:1b` (any CPU) to `qwen2.5:32b` (24GB+ VRAM). For an RTX 3050 8GB, `qwen2.5:7b` is the sweet spot.

---

## Configuration

| File | Purpose |
|---|---|
| `config/settings.yaml` | poll/eval cadences, threshold, retention |
| `config/models.yaml` | LLM candidate catalogue (edit when hardware changes) |
| `config/active_model.yaml` | written by `probe_hardware.py` — do not edit by hand |
| `config/themes.yaml` | ~80 themes the LLM picks from; cascade weights theme→tickers |
| `config/seeds.yaml` | ~30 finance accounts with tier 1-3 credibility |
| `config/universe.csv` | NYSE + NASDAQ symbols (regenerate weekly) |

---

## Project structure

```
src/dolev_ai/
  main.py              asyncio daemon — collect / evaluate / prune scheduler
  models.py            RawTweet, TickerScore, ThemeScore, Signal, ...
  db.py                SQLAlchemy schema + prune_old_data
  events.py            in-process pub/sub for WebSocket
  sources/             TweetSource ABC + PlaywrightSource (Chrome/Edge via executable_path)
  strategies/          SignalStrategy ABC + TrustGraphStrategy
  analysis/
    extractor.py       async worker pool that drives the LLM
    aggregator.py      direct + cascade scoring math
    ticker.py          regex $TICKER fast path (legacy hint)
    credibility.py     per-account weighting from seeds.yaml
  llm/
    __init__.py        LLMProvider ABC + dataclasses
    openai_compat.py   covers Ollama + LM Studio
    anthropic_provider.py  future cloud fallback
    prompt.py          single-tweet JSON prompt + taxonomy injection
    factory.py         build_provider() from active_model.yaml
  synth/               Claude synthesizer (rationale + sizing)
  alert/               Telegram alerter
  web/                 FastAPI app + REST + WS + static SPA serving
scripts/
  probe_hardware.py    pick the right model for this machine
  download_universe.py NYSE + NASDAQ symbol lists
  login_x.py           DEPRECATED — X login now happens from the dashboard
  serve_ui.py          dev helper — runs only the web UI (no scraping)
web/                   React + Vite + TypeScript + Tailwind + D3 + Recharts
tests/                 56 tests passing
```

---

## Tests

```bash
py -3.12 -m pytest -q -m "not slow"
```

---

## Operating notes

- **First model load** is slow (30–45s); subsequent calls are fast(er). Keep the agent running.
- **Throughput**: 7B on RTX 3050 ≈ 28s/tweet, ~50 tweets/cycle = 25 min of LLM work. For faster, run `ollama pull qwen2.5:3b` then re-run `scripts/probe_hardware.py`.
- **Storage**: auto-pruned hourly. Tunable in `config/settings.yaml` → `retention:`.
- **Telegram dry-run**: pass `--dry-run` to print alerts to stdout instead of sending.
- **DB browser**: click the **⛁ DB** button in the dashboard header — paginated views of every table.
- **LLM monitor**: the badge in the header shows live call count, avg latency, errors, and queue backlog. Hover for full breakdown.

For full handoff details (file map, REST endpoints, WS events, scoring formula), see `PROJECT_STATUS.md`.

---

## Phase 2 (deferred)

- Order execution via Alpaca / IBKR (broker adapter consuming `Signal`)
- Historical-accuracy credibility scoring
- Backtester
- Free-form theme discovery + consolidation
