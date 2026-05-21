# Dolev AI — Project Status & Handoff

> **Last updated:** 2026-05-21  
> **Purpose:** Full context for any agent (or developer) continuing this work cold.

---

## What Was Built

A Twitter/X-driven stock-trend agent with a live monitoring web UI.

### Phase 1 — Core Pipeline (complete)
Scrapes finance Twitter → extracts tickers → scores sentiment → trust-graph strategy → Claude synthesis → Telegram alerts.

### Phase 1.5 — Monitoring Web UI (complete)
FastAPI + WebSocket backend embedded in the agent process. React/Vite/D3/Recharts frontend served from `web/dist/`. Live dashboard shows trust graph building, ticker scores climbing toward threshold, score chart, drilldown panel.

---

## Repository

**Local:** `C:\CodeProjects\dolev_ai`  
**Remote:** https://github.com/galgerman/Dolev_AI  
**Branch:** main (all work pushed)

---

## Full File Map

```
dolev_ai/
├── pyproject.toml                   # Python package, deps, pytest config
├── .env                             # Filled with dummy keys (MUST be replaced before full run)
├── .env.example
├── config/
│   ├── seeds.yaml                   # ~30 seed accounts, tiers 1-3
│   ├── settings.yaml                # thresholds, cadences, model names
│   └── universe.csv                 # 12,135 NYSE+NASDAQ symbols (downloaded)
├── scripts/
│   ├── download_universe.py         # Pulls nasdaqtrader.com FTP — run once/weekly
│   ├── login_x.py                   # One-time X login, saves session to browser_profile/
│   └── serve_ui.py                  # Dev helper: start ONLY the web server (no scraping)
├── src/dolev_ai/
│   ├── models.py                    # RawTweet, Account, TickerScore, Signal dataclasses
│   ├── db.py                        # SQLAlchemy + SQLite (data/dolev.db)
│   ├── events.py                    # EventBus — async pub/sub, bounded queues
│   ├── main.py                      # Daemon: APScheduler + uvicorn in same event loop
│   ├── sources/
│   │   ├── __init__.py              # TweetSource ABC
│   │   └── playwright_source.py     # Scrapes X.com with persistent browser session
│   ├── strategies/
│   │   ├── __init__.py              # SignalStrategy ABC
│   │   └── trust_graph.py           # Threshold + cooldown + voice-count gating
│   ├── analysis/
│   │   ├── ticker.py                # Regex $[A-Z]{1,5} + universe.csv validation
│   │   ├── sentiment.py             # FinBERT (ProsusAI/finbert) wrapper
│   │   ├── credibility.py           # Per-account weight from seeds.yaml tiers
│   │   └── aggregator.py           # Rolling score with time-decay; returns (scores, edges)
│   ├── synth/
│   │   └── synthesizer.py           # Claude call → Signal JSON (claude-sonnet-4-6)
│   ├── alert/
│   │   └── telegram.py              # httpx POST to Telegram Bot API
│   └── web/
│       ├── server.py                # create_app() factory — FastAPI + CORS + static SPA
│       ├── api.py                   # REST: /api/health, /tickers/live, /tickers/{t},
│       │                            #       /signals/recent, /graph/snapshot, /accounts
│       │                            #       + /api/auth/x/status, /api/auth/x/login
│       ├── ws.py                    # WebSocket /api/stream — snapshot on connect, then live events
│       ├── schemas.py               # Pydantic response models
│       └── auth.py                  # X login state machine — runs Playwright in thread pool
├── web/                             # React SPA (separate from Python tree)
│   ├── package.json
│   ├── vite.config.ts               # proxies /api/* → :8000 in dev mode
│   ├── dist/                        # Built output — served by FastAPI StaticFiles
│   └── src/
│       ├── App.tsx                  # 3-col × 2-row grid layout
│       ├── types.ts                 # Mirrors Python models + WsEvent union type
│       ├── api.ts                   # fetch wrappers incl. xAuthStatus, xAuthLogin
│       ├── hooks/
│       │   ├── useEventStream.ts    # WebSocket hook, auto-reconnects
│       │   └── useLiveTickers.ts    # Combines REST snapshot + WS deltas into LiveState
│       └── components/
│           ├── Header.tsx           # Title + threshold + WS status + X login button
│           ├── Leaderboard.tsx      # Top 20 tickers, ThresholdProgressBar, pulse on near_threshold
│           ├── TrustGraph.tsx       # D3 force-directed graph (account→ticker edges)
│           ├── ScoreChart.tsx       # Recharts multi-line + threshold ReferenceLine
│           ├── TickerDrilldown.tsx  # Score history, contributing accounts, tweets
│           └── SignalFiredToast.tsx # Auto-dismiss toast when signal.fired fires
├── tests/
│   ├── fixtures/tweets.json
│   ├── test_aggregator.py           # 7 tests — all passing
│   ├── test_trust_graph.py          # 9 tests — all passing
│   ├── test_event_bus.py            # 5 tests — all passing
│   ├── test_api.py                  # 7 tests — all passing
│   └── test_ws.py                   # 2 tests — all passing
└── .claude/
    ├── plans/                       # Archived design docs
    └── skills/
        ├── setup-dolev-ai/SKILL.md  # One-shot setup skill
        └── run-dolev-ai/SKILL.md    # Run agent + UI skill
```

---

## Environment

| Item | Detail |
|---|---|
| OS | Windows 11 Home 10.0.26200 |
| Python | 3.12.3 (via `py -3.12`) — installed at `C:\Users\User\AppData\Local\Programs\Python\Python312` |
| Node | 24.15.0 — at `C:\Program Files\nodejs` (NOT on PATH by default, use `& "C:\Program Files\nodejs\npm.cmd"`) |
| Playwright | 1.60.0 — bundled Chromium has VS runtime issue on this machine (headful mode fails) |
| Edge | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` — works for headful Playwright |
| Chrome | `C:\Program Files\Google\Chrome\Application\chrome.exe` — also available |

**Critical:** Always invoke Python as `py -3.12`, not `python` (that's 3.10).  
**Critical:** Add Node to PATH each session: `$env:PATH += ";C:\Program Files\nodejs"`

---

## Running the App

### Dev-only web server (no scraping, no X login needed):
```powershell
cd C:\CodeProjects\dolev_ai
py -3.12 scripts/serve_ui.py
# → http://localhost:8000
```

### Full agent (dry-run, alerts to stdout):
```powershell
py -3.12 -m dolev_ai.main --dry-run
```

### Full agent (live Telegram alerts):
```powershell
py -3.12 -m dolev_ai.main
```

### Run tests:
```powershell
py -3.12 -m pytest -q -m "not slow"
# → 38 passed
```

### Rebuild frontend (needed after any web/src changes):
```powershell
$env:PATH += ";C:\Program Files\nodejs"
cd C:\CodeProjects\dolev_ai\web
& "C:\Program Files\nodejs\npm.cmd" run build
```

---

## API Keys (.env)

File at `C:\CodeProjects\dolev_ai\.env` currently has **dummy values**. Replace before real runs:

```
ANTHROPIC_API_KEY=sk-ant-...     # claude-sonnet-4-6 for signal synthesis
TELEGRAM_BOT_TOKEN=...           # from @BotFather on Telegram
TELEGRAM_CHAT_ID=...             # your chat ID (GET /bot<token>/getUpdates)
```

Signal synthesis (Claude) and Telegram alerts fail silently with dummy keys — scraping, scoring, and the dashboard all work without them.

---

## WebSocket Events (agent → browser)

| `type` | When |
|---|---|
| `snapshot` | On WS connect — hydrates full UI state |
| `tweet.ingested` | Each new tweet saved |
| `ticker.score_updated` | After each evaluate() cycle, per ticker |
| `ticker.near_threshold` | When threshold_progress crosses 0.75 |
| `signal.synthesizing` | Just before Claude API call |
| `signal.fired` | After synthesizer + alerter |
| `graph.edge_added` | New (account, ticker) edge in trust graph |
| `auth.x.status_changed` | X login state machine transition |

---

## Current Blocker — X Login Button

**Status:** Partially working. The login button exists in the Header and the backend state machine is wired up, but the Playwright browser fails to open from within the `run_in_executor` thread with this error:

```
BrowserType.launch_persistent_context: Opening in existing browser session.
This usually means that the profile is already in use by another instance of Chromium.
```

**Root cause:** A leftover Edge session was saved to `browser_profile/` from an earlier manual test. Edge detects the profile is locked / already open and refuses to launch a second instance.

**Fix needed (one of these):**
1. **Delete the lock file before launching** — `browser_profile/SingletonLock` (Chromium lock file). Add this to `_sync_login()` in `src/dolev_ai/web/auth.py` before `launch_persistent_context`:
   ```python
   import os
   lock = pathlib.Path(profile_dir) / "SingletonLock"
   if lock.exists():
       lock.unlink()
   ```
2. **Clear the stale profile** — if `browser_profile/` exists but user isn't actually logged in yet, wipe it first. Check for `browser_profile/Default/Cookies` as the indicator of a real login.
3. **Nuke and re-test** — delete `C:\CodeProjects\dolev_ai\browser_profile\` entirely, restart the server, click "Connect X" — should work cleanly.

**Relevant file:** `src/dolev_ai/web/auth.py` — `_sync_login()` function, lines ~70-91.

**What IS working:**
- `executable_path` approach (using system Edge/Chrome) is correct and confirmed working in isolation
- `run_in_executor` threading approach is correct
- The button state machine (idle → opening → waiting → complete) works end-to-end
- WS event `auth.x.status_changed` broadcasts correctly to the browser

---

## Scoring Formula (reference)

```
score(ticker) = Σ over tweets in window:
    sentiment_sign (+1/-1/0)
  × confidence (0..1, from FinBERT)
  × credibility(author) (0.2..1.0, from seeds.yaml tier)
  × log(1 + likes + 2·retweets)
  × exp(−age_minutes / 30)

threshold_progress = abs(score) / threshold   # 0..∞, shown as progress bar
```

Signal fires when: `abs(score) >= 5.0 AND unique_credible_voices >= 3 AND not in cooldown`.

---

## Phase 2 (not started — deferred)

- Order execution via Alpaca or IBKR (consumes the same `Signal` dataclass)
- Historical accuracy scoring for credibility (compare past signals vs. realized returns)
- `StockTwits` / X API as alternate `TweetSource` implementations
- Backtester
