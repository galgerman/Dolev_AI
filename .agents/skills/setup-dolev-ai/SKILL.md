---
name: setup-dolev-ai
description: One-shot setup for the Dolev AI Twitter trend agent on a fresh clone. Installs Python deps, Playwright Chromium, Node deps for the web UI, downloads NYSE/NASDAQ symbol universe, scaffolds .env, and walks the user through one-time X login.
---

# Setup: Dolev AI Stock Trend Agent

Run this skill once after cloning the repo. It sets up every dependency and credential needed to start the agent.

## Steps

### 1. Check Python version

```bash
python --version
```

Must be 3.11 or higher. If not, install Python 3.11+ from python.org and re-run this skill.

### 2. Install Python dependencies

```bash
pip install -e ".[dev]"
```

This installs the agent and all dev tools (pytest, etc.) from `pyproject.toml`.

### 3. Install Playwright browser

```bash
playwright install chromium
```

### 4. Check Node.js version

```bash
node --version
```

Must be 18 or higher. If missing or older, install from https://nodejs.org (LTS version) and re-run this skill.

### 5. Install frontend dependencies

```bash
cd web && npm install
```

### 6. Set up environment variables

If `.env` doesn't exist yet:
```bash
cp .env.example .env
```

Then open `.env` and fill in:

| Variable | How to get it |
|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com → API keys |
| `TELEGRAM_BOT_TOKEN` | Message @BotFather on Telegram → `/newbot` → follow prompts → copy the token |
| `TELEGRAM_CHAT_ID` | After creating the bot, send it a message, then visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and copy the `chat.id` from the JSON |

### 7. Download NYSE + NASDAQ symbol universe

```bash
python scripts/download_universe.py
```

This writes `config/universe.csv` (~10,000 ticker symbols). Re-run weekly or when the agent misses well-known tickers.

### 8. Log in to X (one time)

```bash
python scripts/login_x.py
```

A browser window will open. Log into your X account normally, then close the browser. Your session is saved to `browser_profile/` (gitignored). You only need to do this once — sessions persist across restarts.

### 9. Verify the install

```bash
pytest -q -m "not slow"
```

All tests should pass. If any fail, check the error output — likely a missing dependency.

### Done

Setup complete. Run `/run-dolev-ai` to start the agent and open the monitoring dashboard.
