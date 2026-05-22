---
name: run-dolev-ai
description: Run the Dolev AI agent and monitoring UI. Verifies Ollama + model are reachable, runs pytest as smoke test, builds the frontend if needed, then starts the agent daemon. Supports dry-run (alerts to stdout) and dev mode (Vite live-reload).
---

# Run: Dolev AI Stock Trend Agent

## Prerequisites

Run `/setup-dolev-ai` first if you haven't already.

## Steps

### 1. Verify Ollama is running and the model is pulled

```bash
curl http://localhost:11434/api/tags
# Should list qwen2.5:7b (or whichever model probe_hardware.py picked)
```

If Ollama isn't reachable: launch the Ollama service (system tray icon on Windows, or `ollama serve` from a terminal). If the chosen model isn't listed, re-run `scripts/probe_hardware.py` and pull what it recommends.

### 2. Smoke test

```bash
py -3.12 -m pytest -q -m "not slow"
```

56 tests should pass. If any fail, fix them before starting the agent — the agent should never run on broken code.

### 3. Verify environment

```bash
py -3.12 -c "from dotenv import dotenv_values; v=dotenv_values('.env'); print('OK' if all(k in v for k in ['ANTHROPIC_API_KEY','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID']) else 'MISSING KEYS')"
```

For first-time dashboard preview you can use dummy values — synth + Telegram will fail silently in `--dry-run`.

### 4. Build the frontend (if needed)

If `web/dist/` doesn't exist or `web/src/` is newer:

```bash
cd web && npm run build && cd ..
```

The built frontend is served by the FastAPI backend at `http://localhost:8000`.

### 5. Choose your mode

**Dry-run** (recommended first run — alerts print to terminal, no Telegram):

```bash
py -3.12 -m dolev_ai.main --dry-run
```

**Live** (sends real Telegram alerts):

```bash
py -3.12 -m dolev_ai.main
```

**Dev mode** (frontend live-reloads at :5173, backend at :8000 — two terminals):

```bash
# Terminal 1:
py -3.12 -m dolev_ai.main --dry-run

# Terminal 2:
cd web && npm run dev
# Then open http://localhost:5173
```

**UI-only dev** (no scraping, no LLM — just the dashboard for development):

```bash
py -3.12 scripts/serve_ui.py
# Then click "Start Agent" in the header to begin scraping
```

### 6. Open the dashboard

**http://localhost:8000**

If this is the first run after install, click **"Connect X"** in the header to log in to X.com. The browser opens via Playwright; log in normally and close the window. The session is saved to `browser_profile_chrome/`.

The dashboard layout:

- **Header**
  - **LLM badge** — live call counter, avg latency, error count, backlog meter. Flashes yellow on each completed Ollama call. Hover for full breakdown.
  - **⛁ DB button** — opens a paginated database browser with 6 tabs (Extractions / Tweets / Ticker Scores / Theme Scores / Edges / Signals). Click any extraction row to see the raw LLM JSON.
  - **Start Agent / Stop Agent** — toggle the scraper + extractor without restarting
  - **Connect X / Connected** — X session state
- **Live Ticker Leaderboard** — top tickers by |score|, threshold progress bar, voice count, pulse on near-threshold
- **Trust Graph** (D3) — account nodes (circle), ticker nodes (circle), theme nodes (rounded squares). Edges: solid = direct mention, dashed = account→theme, dotted = theme→ticker cascade. Hover an edge to fetch the source post.
- **Extraction Feed** — every post the LLM processed, newest first, with its extracted tickers + themes + summary. Toggle "all posts" vs "finance only". Non-finance entries dimmed.
- **Score vs Threshold** chart — Recharts time series of the top tickers with the threshold line.
- **Ticker Drilldown** — click any ticker in the leaderboard or graph to see contributing accounts, tweets, and the score timeline.

### 7. Wait for the first scrape

The collect cycle runs every 5 min. The first cycle:

1. Playwright opens the seed accounts (~2-3 min for 29 accounts)
2. Each fetched tweet is queued for LLM extraction
3. Workers fire HTTP calls to Ollama — badge counter climbs

First model load can add 30-45s before the first call returns. After that, latency depends on model size: 7B ≈ 5-28s per tweet, 3B ≈ 3-5s, 14B ≈ 10-40s.

### 8. Stop the agent

Press `Ctrl+C` in the terminal. The agent shuts down gracefully — drains the LLM queue, stops Playwright, and flushes any pending DB writes.

## Tuning tips

After a few hours of dry-run:

- **Score distribution**: click the ⛁ DB button → Ticker Scores tab → see what scores actually appear. If nothing crosses `score_threshold` (default 5.0), lower it in `config/settings.yaml`.
- **Too noisy?** Raise `score_threshold` or `min_credible_voices` (default 3).
- **Slow LLM**: the badge will show a growing backlog. Either switch to a smaller model (`ollama pull qwen2.5:3b`, then re-run `scripts/probe_hardware.py`), or lengthen `poll_cadence_minutes` in `settings.yaml`.
- **Add more accounts**: edit `config/seeds.yaml` (tier 1-3). Re-start the agent.
- **DB size**: auto-prunes hourly. Tune `retention:` in `settings.yaml` if needed.
- **New themes**: add to `config/themes.yaml` and the LLM will start picking from them on the next collect cycle (no restart of Ollama needed).
