---
name: setup-dolev-ai
description: One-shot setup for the Dolev AI Twitter trend agent on a fresh clone. Installs Python deps, Playwright Chromium, Node deps for the web UI, installs Ollama and pulls a local LLM, picks the right model for the hardware, downloads NYSE/NASDAQ symbol universe, and scaffolds .env. X login is performed from the dashboard after first run.
---

# Setup: Dolev AI Stock Trend Agent

Run this skill once after cloning the repo. It sets up every dependency the agent needs.

## Steps

### 1. Check Python version

```bash
py -3.12 --version    # Windows
python3.12 --version  # macOS / Linux
```

Must be **3.12+**. If not installed, get it from python.org.

### 2. Install Python dependencies

```bash
py -3.12 -m pip install -e ".[dev]"
```

Installs the agent + dev tools from `pyproject.toml`. The package no longer depends on `torch` / `transformers` (FinBERT was removed in favor of local LLM extraction).

### 3. Install Playwright (only the headless shell is needed)

```bash
playwright install chromium
```

The actual scraping uses the user's **system Chrome or Edge** (selected automatically via `executable_path`). The Playwright bundled Chromium is only used internally.

### 4. Check Node.js version

```bash
node --version
```

Must be **18+**. If missing, install LTS from https://nodejs.org. On Windows you'll typically launch npm via `& "C:\Program Files\nodejs\npm.cmd"`.

### 5. Install frontend dependencies and build

```bash
cd web
npm install
npm run build
cd ..
```

This produces `web/dist/`, which the FastAPI backend serves at `http://localhost:8000`.

### 6. Install Ollama (local LLM runtime)

Download the Windows / macOS / Linux installer from https://ollama.com/download and run it.

After install, Ollama runs as a background service on `http://localhost:11434`. Verify:

```bash
curl http://localhost:11434/api/version
# {"version":"0.24.0"}
```

### 7. Pull the default model

```bash
ollama pull qwen2.5:7b
```

This is ~4.7GB (Q4_K_M quantization). Needs about 6GB VRAM, or runs slowly on 16GB+ RAM.

**Alternatives** if your hardware is different:

| GPU / RAM | Recommended model | Command |
|---|---|---|
| 24GB+ VRAM (4090, A6000) | `qwen2.5:32b` | `ollama pull qwen2.5:32b` |
| 11GB+ VRAM (4080, 3090) | `qwen2.5:14b` | `ollama pull qwen2.5:14b` |
| 6–8GB VRAM (3050, 4060) | `qwen2.5:7b` (default) | already done |
| 3–6GB VRAM or CPU-only | `qwen2.5:3b` or `llama3.2:3b` | `ollama pull qwen2.5:3b` |
| No GPU, low RAM | `llama3.2:1b` | `ollama pull llama3.2:1b` |

You can edit `config/models.yaml` to add or pin a model, then re-run the probe (next step).

### 8. Probe hardware and pick the active model

```bash
py -3.12 scripts/probe_hardware.py
```

This reads `config/models.yaml`, detects VRAM via `nvidia-smi` (falls back to `torch.cuda` then RAM via `psutil`), verifies Ollama is up and the candidate model is pulled, then writes `config/active_model.yaml`.

Re-run any time you change hardware, edit `models.yaml`, or pull a new model.

### 9. Download NYSE + NASDAQ symbol universe

```bash
py -3.12 scripts/download_universe.py
```

Writes `config/universe.csv` (~12,000 ticker symbols from nasdaqtrader.com FTP). Re-run weekly.

### 10. Set up environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Variable | Required for | How to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Signal synthesis (rationale + sizing). **Optional in dry-run** — use a dummy value if you only want to watch the dashboard. | https://console.anthropic.com → API keys |
| `TELEGRAM_BOT_TOKEN` | Telegram alerts. **Optional in dry-run** (alerts go to stdout). | Message `@BotFather` → `/newbot` → copy the token |
| `TELEGRAM_CHAT_ID` | Telegram alerts | After creating the bot, send it a message, then visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and copy `chat.id` from the JSON |

### 11. Verify the install

```bash
py -3.12 -m pytest -q -m "not slow"
```

All **56 tests** should pass. If any fail, check the error — likely a missing dependency or model not pulled.

### 12. X login (done from the dashboard, not a script)

X login is now triggered from the **dashboard header** the first time you run the agent — there's a **"Connect X"** button that opens a Playwright window pointed at `x.com/login`. Log in normally, close the browser, and the session is saved to `browser_profile_chrome/`.

The legacy `scripts/login_x.py` is still present but **not needed** — the in-dashboard flow is preferred.

### Done

Setup complete. Run `/run-dolev-ai` to start the agent and open the monitoring dashboard.
