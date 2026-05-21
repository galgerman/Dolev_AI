---
name: run-dolev-ai
description: Run the Dolev AI agent and monitoring UI. Runs pytest as smoke test first, builds the frontend if needed, then starts the agent daemon. Supports dry-run (alerts to stdout) and dev mode (Vite live-reload).
---

# Run: Dolev AI Stock Trend Agent

## Prerequisites

Run `/setup-dolev-ai` first if you haven't already.

## Steps

### 1. Smoke test

```bash
pytest -q -m "not slow"
```

If any tests fail, fix them before starting the agent. The agent should never run on broken code.

### 2. Verify environment

Confirm `.env` exists and contains all three required variables:
```bash
python -c "from dotenv import dotenv_values; v=dotenv_values('.env'); print('OK' if all(k in v for k in ['ANTHROPIC_API_KEY','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID']) else 'MISSING KEYS')"
```

### 3. Build the frontend (if needed)

If `web/dist/` doesn't exist or `web/src/` is newer:
```bash
cd web && npm run build
```

The built frontend is served by the FastAPI backend at `http://localhost:8000`.

### 4. Choose your mode

**Dry-run** (recommended for first run — alerts print to terminal, no Telegram messages):
```bash
python -m dolev_ai.main --dry-run
```

**Live** (sends real Telegram alerts):
```bash
python -m dolev_ai.main
```

**Dev mode** (frontend live-reloads at :5173, backend at :8000 — run in two terminals):
```bash
# Terminal 1:
python -m dolev_ai.main --dry-run

# Terminal 2:
cd web && npm run dev
# Then open http://localhost:5173
```

### 5. Open the dashboard

Once the agent starts, open: **http://localhost:8000**

The dashboard shows:
- **Leaderboard** — top tickers by score, with color-coded threshold progress bars
- **Trust graph** — live D3 network of which accounts are driving which tickers
- **Score chart** — time series of top ticker scores vs. the threshold line
- **Ticker drilldown** — click any ticker for contributing tweets and accounts

### 6. Stop the agent

Press `Ctrl+C` in the terminal. The agent shuts down gracefully, flushing any pending DB writes.

## Tuning tips

After your first few hours of dry-run:

- Check `data/dolev.db` → `ticker_scores` table to see the score distribution. If nothing ever crosses threshold, lower `score_threshold` in `config/settings.yaml`.
- If there are too many false-positive signals, raise `score_threshold` or increase `min_credible_voices`.
- Add more accounts to `config/seeds.yaml` (tier 1-3) to improve signal coverage.
