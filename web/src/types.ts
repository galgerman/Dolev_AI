export interface TickerScore {
  ticker: string
  score: number
  unique_credible_voices: number
  tweet_count: number
  window_start: string
  window_end: string
  top_tweet_urls: string[]
  threshold_progress: number
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
  type: 'account' | 'ticker'
  label: string
  size: number
  sentiment: string
  tier: number
}

export interface GraphEdge {
  source: string
  target: string
  weight: number
  sentiment: string
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

// ── WebSocket events ──────────────────────────────────────────────────────

export type WsEvent =
  | { type: 'snapshot'; top_tickers: TickerScore[]; recent_signals: Signal[]; graph: GraphSnapshot }
  | { type: 'tweet.ingested'; id: string; author: string; text: string; created_at: string; url: string; tickers: string[]; sentiment: string; sentiment_score: number }
  | { type: 'ticker.score_updated'; ticker: string; score: number; voices: number; tweet_count: number; threshold: number; threshold_progress: number }
  | { type: 'ticker.near_threshold'; ticker: string; score: number; threshold: number; percent: number }
  | { type: 'signal.synthesizing'; ticker: string }
  | { type: 'signal.fired'; ticker: string; side: 'buy' | 'sell'; conviction: number; suggested_size_pct: number; rationale: string; key_drivers: string[]; generated_at: string }
  | { type: 'graph.edge_added'; author: string; ticker: string; weight: number; sentiment: string }
  | { type: 'auth.x.status_changed'; state: 'idle' | 'opening' | 'waiting' | 'complete' | 'error'; logged_in: boolean }

export interface XAuthStatus {
  state: 'idle' | 'opening' | 'waiting' | 'complete' | 'error'
  logged_in: boolean
}
