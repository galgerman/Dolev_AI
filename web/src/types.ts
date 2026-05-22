export interface TickerScore {
  ticker: string
  score: number
  unique_credible_voices: number
  tweet_count: number
  window_start: string
  window_end: string
  top_tweet_urls: string[]
  threshold_progress: number
  direct_score?: number
  cascade_score?: number
}

export interface Signal {
  ticker: string
  side: 'buy' | 'sell'
  conviction: number
  suggested_size_pct: number
  rationale: string
  key_drivers: string[]
  generated_at: string
}

export interface GraphNode {
  id: string
  type: 'account' | 'ticker' | 'theme'
  label: string
  size: number
  sentiment: string
  tier: number
}

export interface GraphEdge {
  id?: number
  source: string
  target: string
  weight: number
  sentiment: string
  edge_type?: 'acct_ticker' | 'acct_theme' | 'theme_ticker'
  tweet_id?: string | null
}

export interface GraphSnapshot {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface Account {
  handle: string
  tier: number
  credibility: number
}

export interface DrilldownTweet {
  id: string
  author: string
  text: string
  created_at: string
  like_count: number
  retweet_count: number
  url: string
}

export interface DrilldownAccountContrib {
  handle: string
  tier: number
  credibility: number
  tweet_count: number
}

export interface TickerDrilldown {
  ticker: string
  current_score: number
  threshold_progress: number
  unique_credible_voices: number
  score_history: [string, number][]
  contributing_accounts: DrilldownAccountContrib[]
  recent_tweets: DrilldownTweet[]
}

export interface HealthInfo {
  ok: boolean
  started_at: string
  threshold: number
  subscriber_count: number
}

export interface AgentStatus {
  running: boolean
  started_at: string | null
  mode: 'dry-run' | 'live'
}

export interface CollectionProgress {
  active: boolean
  completed: number
  total: number
  current_handle: string | null
  tweets_found: number
  tickers_found: number
  last_handle: string | null
  error?: string
}

// ── LLM extraction types ────────────────────────────────────────────────

export interface TickerMention {
  ticker: string
  sentiment: 'positive' | 'negative' | 'neutral'
  confidence: number
  explicit: boolean
}

export interface ThemeMention {
  theme: string
  sentiment: 'positive' | 'negative' | 'neutral'
  confidence: number
}

export interface Extraction {
  id: number
  tweet_id: string
  author: string
  text: string
  url: string
  is_finance: boolean
  overall_sentiment: 'positive' | 'negative' | 'neutral'
  summary: string
  tickers: TickerMention[]
  themes: ThemeMention[]
  model: string
  latency_ms: number
  created_at: string
}

export interface ThemeScore {
  theme: string
  score: number
  voices: number
  tweet_count: number
  threshold_progress: number
  cascade_targets: string[]
}

export interface EdgeDetail {
  id: number
  from_id: string
  to_id: string
  edge_type: string
  weight: number
  sentiment: string
  tweet_id: string | null
  created_at: string
  tweet?: DrilldownTweet | null
}

export interface LLMStatus {
  provider: string
  model: string
  endpoint: string
  healthy: boolean
  backlog: number
  capacity: number
  running: boolean
  calls_total?: number
  calls_finance?: number
  calls_errors?: number
  avg_latency_ms?: number
  last_call_at?: number | null
  dropped_total?: number
}

export interface DBStats {
  tweets: number
  extractions: number
  extractions_finance: number
  tickers: number
  themes: number
  edges: number
  signals: number
  last_tweet_at: string | null
  last_extraction_at: string | null
}

export interface DBPage<T = Record<string, unknown>> {
  total: number
  rows: T[]
}

// ── WebSocket events ──────────────────────────────────────────────────────

export type WsEvent =
  | { type: 'snapshot'; top_tickers: TickerScore[]; recent_signals: Signal[]; graph: GraphSnapshot; top_themes?: ThemeScore[]; recent_extractions?: Extraction[] }
  | { type: 'collection.started'; total: number; completed: number; current_handle: string | null; tweets_found: number; tickers_found: number }
  | { type: 'collection.account_started'; handle: string; index: number; total: number; completed: number; tweets_found: number; tickers_found: number }
  | { type: 'collection.account_completed'; handle: string; index: number; total: number; completed: number; account_tweets: number; tweets_found: number; tickers_found: number }
  | { type: 'collection.finished'; total: number; completed: number; current_handle: string | null; tweets_found: number; tickers_found: number }
  | { type: 'collection.failed'; message: string }
  | { type: 'tweet.ingested'; id: string; author: string; text: string; created_at: string; url: string; tickers: string[]; sentiment: string; sentiment_score: number }
  | { type: 'ticker.discovered'; ticker: string; voices: number; tweet_count: number }
  | { type: 'ticker.score_updated'; ticker: string; score: number; direct_score?: number; cascade_score?: number; voices: number; tweet_count: number; threshold: number; threshold_progress: number }
  | { type: 'ticker.near_threshold'; ticker: string; score: number; threshold: number; percent: number }
  | { type: 'signal.synthesizing'; ticker: string }
  | { type: 'signal.fired'; ticker: string; side: 'buy' | 'sell'; conviction: number; suggested_size_pct: number; rationale: string; key_drivers: string[]; generated_at: string }
  | { type: 'graph.edge_added'; author?: string; ticker?: string; from_id?: string; to_id?: string; edge_type?: string; weight: number; sentiment: string; tweet_id?: string | null; tweet_url?: string }
  | { type: 'auth.x.status_changed'; state: 'idle' | 'opening' | 'waiting' | 'complete' | 'error'; logged_in: boolean }
  | { type: 'post.extracting'; tweet_id: string; author: string }
  | { type: 'post.extracted'; tweet_id: string; author: string; text: string; url: string; is_finance: boolean; overall_sentiment: string; summary: string; tickers: TickerMention[]; themes: ThemeMention[]; model: string; latency_ms: number; created_at: string }
  | { type: 'theme.score_updated'; theme: string; score: number; voices: number; tweet_count: number }
  | { type: 'theme.activated'; theme: string; score: number; voices: number }
  | { type: 'extraction.backlog'; depth: number; capacity: number; dropped_total: number }
  | { type: 'llm.call'; tweet_id: string; model: string; latency_ms: number; is_finance: boolean; tickers_n: number; themes_n: number; ts: number }

export interface XAuthStatus {
  state: 'idle' | 'opening' | 'waiting' | 'complete' | 'error'
  logged_in: boolean
}
