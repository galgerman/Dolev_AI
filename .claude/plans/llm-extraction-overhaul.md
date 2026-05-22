# Phase 1.6 — LLM-driven extraction overhaul

## Context

Current pipeline relies on `$TICKER` regex + FinBERT sentiment. Real finance Twitter rarely uses cashtags and constantly alludes to companies via theme, name, or sector context. The result: the trust graph barely populates because explicit ticker matches are sparse.

**Goal:** Replace the extraction stage with a per-post LLM that returns both explicit ticker mentions and inferred themes/tickers. Themes become first-class graph nodes that cascade scores to their associated tickers. Local-first with a clean abstraction so cloud models can plug in later.

**Locked decisions:**
- Provider: local-first (Ollama or LM Studio), Anthropic Haiku as future cloud fallback
- Theme system: **fixed taxonomy** (~80 themes in `config/themes.yaml`)
- FinBERT: **removed** (LLM produces sentiment now)
- Hardware: **scriptable** — `config/models.yaml` defines candidates, `scripts/probe_hardware.py` picks one
- Cascade factor: **0.5** (theme-mediated score = direct score × 0.5)

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ Tweet ingested                                                     │
│   │                                                                │
│   ▼                                                                │
│ analysis/ticker.py  (regex hint, fast path)                        │
│   │                                                                │
│   ▼                                                                │
│ analysis/extractor.py — worker pool                                │
│   │ uses LLMProvider                                               │
│   │                                                                │
│   ├──▶ OpenAICompatProvider  (Ollama :11434/v1, LM Studio :1234/v1)│
│   └──▶ AnthropicProvider     (Claude Haiku, later)                 │
│   │                                                                │
│   ▼ Extraction { tickers, themes, sentiment, summary }             │
│ db.extractions                                                     │
│   │                                                                │
│   ▼                                                                │
│ analysis/aggregator.py (rewritten)                                 │
│   direct_score(ticker)  +  cascade(ticker via themes) × 0.5        │
│   │                                                                │
│   ▼                                                                │
│ strategies/trust_graph.py  (unchanged threshold logic)             │
│   │                                                                │
│   ▼                                                                │
│ synth/synthesizer.py  (Claude, now with theme + post context)      │
│   │                                                                │
│   ▼                                                                │
│ alert/telegram.py                                                  │
└────────────────────────────────────────────────────────────────────┘
```

---

## File-by-file plan

### A. Hardware probe + model picker

**`config/models.yaml`** (new) — user-editable model catalogue:
```yaml
candidates:
  - name: qwen2.5:32b
    provider: openai_compat
    endpoint: http://localhost:11434/v1
    min_vram_gb: 22
  - name: qwen2.5:14b
    provider: openai_compat
    endpoint: http://localhost:11434/v1
    min_vram_gb: 11
  - name: qwen2.5:7b
    provider: openai_compat
    endpoint: http://localhost:11434/v1
    min_vram_gb: 6
  - name: gemma2:9b
    provider: openai_compat
    endpoint: http://localhost:11434/v1
    min_vram_gb: 7
  - name: qwen2.5:3b
    provider: openai_compat
    endpoint: http://localhost:11434/v1
    min_vram_gb: 3
    cpu_ok: true
  - name: llama3.2:1b
    provider: openai_compat
    endpoint: http://localhost:11434/v1
    min_vram_gb: 0
    cpu_ok: true
defaults:
  batch_size: 8
  max_concurrent: 2
  timeout_s: 30
```

**`scripts/probe_hardware.py`** (new):
- Detect NVIDIA VRAM via `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits`; fall back to `torch.cuda.mem_get_info()` if available; otherwise CPU-only
- Detect free RAM via `psutil.virtual_memory()`
- Read `config/models.yaml`, walk candidates top→bottom, pick first match
- Probe Ollama / LM Studio endpoints by hitting `/v1/models`; verify the chosen model is actually pulled
- Write `config/active_model.yaml`: `{ provider, name, endpoint, batch_size, max_concurrent }`
- Print recommendation + manual override instructions

### B. LLM provider layer

**`src/dolev_ai/llm/__init__.py`** — `LLMProvider` ABC + dataclasses
```python
@dataclass
class TickerMention:
    ticker: str
    sentiment: Literal["positive", "negative", "neutral"]
    confidence: float
    explicit: bool

@dataclass
class ThemeMention:
    theme: str
    sentiment: Literal["positive", "negative", "neutral"]
    confidence: float

@dataclass
class Extraction:
    post_id: str
    is_finance: bool
    tickers: list[TickerMention]
    themes: list[ThemeMention]
    overall_sentiment: Literal["positive", "negative", "neutral"]
    summary: str
    model: str
    latency_ms: int

class LLMProvider(ABC):
    @abstractmethod
    async def healthcheck(self) -> bool: ...
    @abstractmethod
    async def extract_batch(self, tweets: list[RawTweet]) -> list[Extraction]: ...
```

**`src/dolev_ai/llm/prompt.py`** — system prompt builder:
- Reads `config/themes.yaml`, injects taxonomy into prompt as a strict whitelist
- System prompt explains: "Return only themes from this list. Set is_finance=false for non-finance posts. Return top-5 most relevant tickers."
- Returns the JSON Schema for `response_format` constraint

**`src/dolev_ai/llm/openai_compat.py`** — covers both Ollama and LM Studio:
```python
class OpenAICompatProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, batch_size: int = 8, ...):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        ...

    async def healthcheck(self) -> bool:
        resp = await self._client.get("/models")
        return resp.status_code == 200 and self._model in [m["id"] for m in resp.json()["data"]]

    async def extract_batch(self, tweets):
        # Build batch prompt: numbered tweets, request JSON array of Extraction
        # Use response_format={"type": "json_object"} or json_schema if supported
        # Validate each result against taxonomy; retry once with explicit error feedback
        ...
```

**`src/dolev_ai/llm/anthropic_provider.py`** — Claude Haiku implementation of same ABC. Skeleton + stub for Phase 2 cloud fallback. Tests cover wire format but not real API calls.

**`src/dolev_ai/llm/factory.py`** — `build_provider() -> LLMProvider`:
- Reads `config/active_model.yaml`
- Returns the right provider instance based on `provider` field
- Validates with `healthcheck()` at startup; raises clear error if Ollama isn't running

### C. Theme taxonomy

**`config/themes.yaml`** (new) — seed with ~80 themes across categories:

```yaml
# AI / Semiconductors
ai_infrastructure:
  description: "AI chips, data centers, model training compute"
  tickers: { NVDA: 1.0, AMD: 0.8, TSM: 0.7, AVGO: 0.6, ASML: 0.6, ARM: 0.5, MU: 0.5 }
ai_software:
  description: "Enterprise AI applications, LLM products"
  tickers: { MSFT: 0.9, GOOGL: 0.9, META: 0.7, ORCL: 0.5, PLTR: 0.6, CRM: 0.5 }
semiconductor_cycle:
  description: "Chip demand cycle, inventory normalization"
  tickers: { NVDA: 0.7, AMD: 0.8, INTC: 0.8, TSM: 0.9, MU: 0.9, AMAT: 0.7, LRCX: 0.7 }

# EV / Auto
ev_demand:
  description: "EV sales, charging, EV supply chain"
  tickers: { TSLA: 1.0, RIVN: 0.7, F: 0.6, GM: 0.6, LCID: 0.5, NIO: 0.5, BYD: 0.5 }
auto_traditional:
  description: "Legacy auto OEMs, ICE sales"
  tickers: { F: 0.9, GM: 0.9, STLA: 0.7, TM: 0.6 }

# Banking / Finance
regional_banks_stress: { ... }
big_banks: { tickers: { JPM, BAC, WFC, C, GS, MS } }
insurance: { ... }
fintech_disruption: { tickers: { SQ, PYPL, COIN, HOOD, SOFI } }

# Macro
rate_cuts: { tickers: { TLT: 0.9, SPY: 0.5, QQQ: 0.5, XLU: 0.7, IWM: 0.6 } }
rate_hikes: { tickers: { TLT: -0.9, KRE: -0.6, ... } }   # negative weight = inverse exposure
inflation_hot:    { ... }
inflation_cool:   { ... }
recession_fear:   { ... }
soft_landing:     { ... }
dollar_strength:  { ... }
risk_on:          { tickers: { SPY: 0.7, QQQ: 0.8, IWM: 0.7, ARKK: 0.6 } }
risk_off:         { tickers: { TLT: 0.7, GLD: 0.6, VIX: 0.8 } }

# Sectors / Themes
energy_supply: { tickers: { XOM, CVX, OXY, COP, USO } }
oil_demand: { ... }
gold_safe_haven: { tickers: { GLD, GDX, NEM } }
crypto_bullish: { tickers: { COIN, MSTR, MARA, RIOT, IBIT } }
biotech_breakthrough: { tickers: { XBI, REGN, VRTX } }
weight_loss_drugs: { tickers: { LLY: 1.0, NVO: 1.0, PFE: 0.4 } }
pharma_pricing: { ... }
defense_spending: { tickers: { LMT, RTX, NOC, GD, BA } }
china_consumer: { tickers: { BABA, PDD, JD, NIO } }
china_tension: { tickers: { TSM: -0.5, NVDA: -0.4, AAPL: -0.4 } }
travel_demand: { tickers: { DAL, UAL, MAR, HLT, ABNB, BKNG } }
housing_demand: { tickers: { LEN, DHI, PHM, HD, LOW, Z } }
retail_consumer_strong: { tickers: { WMT, COST, AMZN, TGT } }
retail_consumer_weak: { tickers: { TGT: -0.7, KSS: -0.7, M: -0.7 } }
streaming_wars: { tickers: { NFLX, DIS, WBD, PARA } }
cloud_growth: { tickers: { AMZN, MSFT, GOOGL, ORCL, SNOW } }
cybersecurity: { tickers: { CRWD, PANW, ZS, S, FTNT } }
ev_charging: { tickers: { CHPT, BLNK, EVGO } }
solar_renewable: { tickers: { ENPH, FSLR, SEDG, RUN } }
nuclear_revival: { tickers: { CCJ, NXE, SMR, UEC } }

# Event-driven
earnings_beat: {}                  # ticker-agnostic flag; aggregator handles
earnings_miss: {}
fda_approval: {}
fda_rejection: {}
merger_announcement: {}
spinoff: {}
buyback_announcement: {}
dividend_cut: {}

# Macro events
fed_meeting: {}
cpi_release: {}
jobs_report: {}
gdp_release: {}
```

(Empty `tickers: {}` themes are event-flags that affect any ticker also mentioned in the same post; not their own graph nodes.)

Full file: ~80 themes. I'll seed it during implementation and you can edit freely.

### D. Extraction pipeline

**`src/dolev_ai/analysis/extractor.py`** (new) — worker pool:
```python
class ExtractionWorker:
    def __init__(self, provider: LLMProvider, event_bus: EventBus,
                 session_factory, max_queue: int = 1000, workers: int = 2):
        self._queue: asyncio.Queue[RawTweet] = asyncio.Queue(maxsize=max_queue)
        ...

    async def submit(self, tweet: RawTweet) -> None:
        try:
            self._queue.put_nowait(tweet)
        except asyncio.QueueFull:
            # Drop oldest, publish backlog event
            ...

    async def _worker_loop(self) -> None:
        while True:
            batch = await self._collect_batch()  # up to batch_size, max wait 2s
            await self._event_bus.publish({"type": "post.extracting", ...})
            extractions = await self._provider.extract_batch(batch)
            self._persist(extractions)
            for ex in extractions:
                await self._event_bus.publish({"type": "post.extracted", ...})

    def backlog(self) -> int:
        return self._queue.qsize()
```

**Backpressure:**
- `extraction.backlog` event published every 5s with `{depth, capacity}`
- Drop-oldest policy when queue full
- Header in UI shows backlog meter

### E. Scoring (rewritten)

**`src/dolev_ai/analysis/aggregator.py`** — replace existing logic:

```python
CASCADE_FACTOR = 0.5

def aggregate(extractions: list[Extraction], window_minutes: int,
              threshold: float, themes_cfg: dict) -> tuple[
                dict[str, TickerScore],
                dict[str, ThemeScore],
                list[GraphEdge]]:
    # 1. Direct ticker scores
    direct: dict[str, float] = {}
    for ex in extractions:
        for tm in ex.tickers:
            contrib = (
                _sign(tm.sentiment) *
                tm.confidence *
                credibility(ex.author) *
                _engagement_weight(tweet) *
                _time_decay(tweet.created_at, now)
            )
            direct[tm.ticker] = direct.get(tm.ticker, 0) + contrib

    # 2. Theme scores
    theme_scores: dict[str, float] = {}
    for ex in extractions:
        for th in ex.themes:
            contrib = ...same formula...
            theme_scores[th.theme] = theme_scores.get(th.theme, 0) + contrib

    # 3. Cascade theme → ticker
    cascaded: dict[str, float] = defaultdict(float)
    for theme, tscore in theme_scores.items():
        weights = themes_cfg.get(theme, {}).get("tickers", {})
        for ticker, weight in weights.items():
            cascaded[ticker] += tscore * weight * CASCADE_FACTOR

    # 4. Combine
    final = {t: TickerScore(
        ticker=t,
        score=direct.get(t, 0) + cascaded.get(t, 0),
        direct_score=direct.get(t, 0),
        cascade_score=cascaded.get(t, 0),
        ...
    ) for t in (direct.keys() | cascaded.keys())}

    return final, theme_scores_struct, graph_edges
```

`TickerScore` dataclass gets two new fields: `direct_score`, `cascade_score` — surfaced in UI so user can see why a ticker ranked.

### F. Database additions

In `src/dolev_ai/db.py`, add models:

```python
class ExtractionRow(Base):
    __tablename__ = "extractions"
    id: int                # primary
    tweet_id: str          # FK → tweets.id
    model: str
    is_finance: bool
    overall_sentiment: str
    summary: str
    raw_json: str
    latency_ms: int
    created_at: datetime

class ExtractedTickerRow(Base):
    __tablename__ = "extracted_tickers"
    id: int
    extraction_id: int     # FK
    ticker: str
    sentiment: str
    confidence: float
    explicit: bool

class ExtractedThemeRow(Base):
    __tablename__ = "extracted_themes"
    id: int
    extraction_id: int
    theme: str
    sentiment: str
    confidence: float

class GraphEdgeRow(Base):
    __tablename__ = "graph_edges"
    id: int
    from_id: str           # "acct:cnbcnow" or "theme:ai_infrastructure"
    to_id: str
    edge_type: str         # "acct_ticker" | "acct_theme" | "theme_ticker"
    weight: float
    sentiment: str
    tweet_id: str | None   # source post — null for theme_ticker edges
    created_at: datetime

class ThemeScoreRow(Base):
    __tablename__ = "theme_scores"
    id: int
    theme: str
    score: float
    voices: int
    tweet_count: int
    window_start: datetime
    window_end: datetime
```

Indexes:
- `extractions(tweet_id)`, `extractions(created_at)`
- `extracted_tickers(ticker, extraction_id)`
- `extracted_themes(theme, extraction_id)`
- `graph_edges(created_at)`, `graph_edges(tweet_id)`

### G. Remove FinBERT

- Delete `src/dolev_ai/analysis/sentiment.py`
- Remove `torch`, `transformers` from `pyproject.toml`
- Drop all imports / patches in tests
- Update `tests/test_aggregator.py` to inject `Extraction` fixtures instead of `RawTweet` + sentiment

### H. Web API additions

**`src/dolev_ai/web/api.py`** — new endpoints:
- `GET /api/extractions/recent?limit=50` → recent extractions for the live feed
- `GET /api/themes/active` → top themes by score (last 60min)
- `GET /api/themes/{theme}` → drilldown: contributing tweets, related tickers, score history
- `GET /api/edges/{edge_id}` → edge detail with source post text (for hover popover)
- `GET /api/llm/status` → `{provider, model, healthy, backlog, batch_size}`
- `POST /api/llm/switch` → switch active provider/model at runtime (validates with healthcheck before swapping)

**`src/dolev_ai/web/schemas.py`** — add `ExtractionOut`, `ThemeScoreOut`, `LLMStatusOut`, `EdgeDetailOut`.

### I. WebSocket events (additions)

| `type` | Payload |
|---|---|
| `post.extracting` | `{ tweet_id, author }` |
| `post.extracted` | `{ extraction: ExtractionOut }` |
| `theme.score_updated` | `{ theme, score, voices, cascade_targets: [...] }` |
| `theme.activated` | `{ theme, score, voices }` |
| `extraction.backlog` | `{ depth, capacity }` |
| `extraction.model_changed` | `{ provider, model }` |

### J. Frontend

**`web/src/types.ts`** — mirror new dataclasses.

**`web/src/api.ts`** — add `extractionsRecent`, `themesActive`, `themeDrilldown`, `edgeDetail`, `llmStatus`, `llmSwitch`.

**New components:**

1. **`web/src/components/ExtractionFeed.tsx`** — live streaming list of extractions, newest first:
   ```
   14:23  @CNBCnow            "Powell sounds dovish, risk on..."
          themes  rate_cuts(+)  risk_on(+)
          tickers SPY(+0.8)  QQQ(+0.7)  TLT(+0.6)
   ```
   Auto-scrolls (with pause-on-hover). Click row → opens drilldown of that post's edges.

2. **`web/src/components/ThemeNode`** — D3 visual: rounded rectangle vs. circle for tickers.

3. **`web/src/components/EdgeHover.tsx`** — popover showing source post text + extraction summary.

4. **`web/src/components/LLMStatusBadge.tsx`** — header pill showing `qwen2.5:7b · ollama · backlog 4/1000`. Click opens model selector dropdown.

**Modified:**

- **`TrustGraph.tsx`**:
  - Two node types: tickers (circles) + themes (rounded squares)
  - Three edge types styled distinctly: `acct→ticker` (solid green/red), `acct→theme` (dashed), `theme→ticker` (dotted faint)
  - Edge hover → fetch `/api/edges/{id}` → show post
  - Click ticker → dim non-1-hop neighborhood (subgraph focus)
  - Toggle row: "show themes", "show neutral", "subgraph mode"
- **`useLiveTickers.ts`** — handle new events; add `extractions`, `themes`, `llmStatus`, `extractionBacklog` to `LiveState`
- **`Header.tsx`** — add `<LLMStatusBadge />` next to existing buttons
- **`App.tsx`** — grid layout adjusts:
  ```
  ┌──────────────┬─────────────────┬──────────────┐
  │ Leaderboard  │ TrustGraph      │ Drilldown /  │
  │              │ (2 cols wide)   │ ExtractionFd │
  ├──────────────┴─────────────────┤              │
  │ ScoreChart                     │              │
  └────────────────────────────────┴──────────────┘
  ```

### K. Synthesizer enhancement

**`src/dolev_ai/synth/synthesizer.py`** — extend input:
- Now receives: ticker, score breakdown (direct + cascade), top contributing themes, top 10 contributing posts (with summaries from extraction), recent price (if `yfinance` available)
- Prompt updated to explicitly reference theme drivers in rationale

---

## Verification

**Unit tests:**
- `tests/test_provider_openai_compat.py` — mock httpx, assert request shape, JSON validation, retry on schema violation
- `tests/test_taxonomy_enforcement.py` — off-taxonomy theme → retry → final result has only valid themes
- `tests/test_extraction_pipeline.py` — fixture extractions seeded into DB → aggregate → assert direct + cascade math matches expected, edges created with correct types
- `tests/test_probe_hardware.py` — mock VRAM/RAM/endpoint responses → assert correct model picked from `models.yaml`
- `tests/test_aggregator_v2.py` — replace existing aggregator tests with extraction-based inputs

**Integration tests:**
- `tests/test_api_extractions.py` — `/api/extractions/recent`, `/api/themes/active`, `/api/edges/{id}` return correct shapes
- `tests/test_ws_v2.py` — `post.extracted`, `theme.score_updated`, `extraction.backlog` events delivered

**Manual smoke test:**
1. Install Ollama, pull a model (`ollama pull qwen2.5:7b`)
2. Run `python scripts/probe_hardware.py` → verify `config/active_model.yaml` written
3. Run agent → confirm `/api/llm/status` shows model healthy
4. Watch ExtractionFeed in UI populate; click a row, confirm edges highlight in TrustGraph
5. Hover an edge → popover shows source post text
6. Wait for a theme to activate; confirm theme node appears in graph and cascades visible boost to related tickers
7. Switch provider via header dropdown → verify hot-swap without restart

---

## Migration order (suggested implementation order)

1. **`config/themes.yaml`** seed + parser
2. **`scripts/probe_hardware.py`** + `config/models.yaml`
3. **`src/dolev_ai/llm/`** module: ABC, openai_compat, anthropic stub, prompt, factory
4. **DB migrations** — new tables, no destructive changes yet
5. **`analysis/extractor.py`** worker pool
6. **`analysis/aggregator.py`** rewrite with cascade
7. **Remove FinBERT** + clean up `pyproject.toml` deps
8. **`main.py`** — wire extractor into pipeline before aggregator
9. **REST + WS endpoints** in web layer
10. **Frontend**: types + api + ExtractionFeed component first (highest user value)
11. **TrustGraph enhancements** (theme nodes, edge hover, subgraph focus)
12. **LLMStatusBadge** + model switcher
13. **Synthesizer** context enhancement
14. **Full test pass** + manual smoke

---

## Out of scope

- Theme discovery / free-form theme consolidation (Phase 2 if you outgrow the fixed taxonomy)
- Embedding-based fallback for posts the LLM marks as low-confidence
- Multi-language support
- Anthropic provider beyond stub
- Order execution (still Phase 2)
- Historical accuracy scoring for accounts

---

## Acceptance criteria

- ✅ Agent runs end-to-end with Ollama + a local model and no FinBERT
- ✅ Graph populates from semantic content, not just `$TICKER`
- ✅ Theme nodes visible in UI with cascade visualizations
- ✅ Live ExtractionFeed shows post → tickers + themes within ~2s of ingestion (local-model dependent)
- ✅ Hover any edge → see the source post
- ✅ Hot-swap model via UI without restart
- ✅ All tests pass; total test count comparable to current 38
- ✅ Cold-start setup unchanged (no new manual steps beyond `ollama pull <model>`)
