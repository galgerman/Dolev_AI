import type {
  Account,
  AgentStatus,
  DBPage,
  DBStats,
  EdgeDetail,
  Extraction,
  GraphSnapshot,
  HealthInfo,
  LLMStatus,
  PaperPosition,
  Signal,
  SignalApproval,
  ThemeScore,
  TickerDrilldown,
  TickerScore,
  XAuthStatus,
} from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'POST' })
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  health: () => get<HealthInfo>('/health'),
  tickersLive: (limit = 20) => get<TickerScore[]>(`/tickers/live?limit=${limit}`),
  tickerDrilldown: (ticker: string) => get<TickerDrilldown>(`/tickers/${ticker}`),
  signalsRecent: (limit = 20) => get<Signal[]>(`/signals/recent?limit=${limit}`),
  graphSnapshot: () => get<GraphSnapshot>('/graph/snapshot'),
  accounts: () => get<Account[]>('/accounts'),
  agentStatus: () => get<AgentStatus>('/agent/status'),
  agentStart: () => post<AgentStatus & { started: boolean }>('/agent/start'),
  agentStop: () => post<AgentStatus & { stopped: boolean }>('/agent/stop'),
  xAuthStatus: () => get<XAuthStatus>('/auth/x/status'),
  xAuthLogin: () => post<{ started: boolean; state: string }>('/auth/x/login'),
  extractionsRecent: (limit = 50) => get<Extraction[]>(`/extractions/recent?limit=${limit}`),
  themesActive: (limit = 20) => get<ThemeScore[]>(`/themes/active?limit=${limit}`),
  themeDrilldown: (theme: string) => get<unknown>(`/themes/${theme}`),
  edgeDetail: (id: number) => get<EdgeDetail>(`/edges/${id}`),
  llmStatus: () => get<LLMStatus>('/llm/status'),
  dbStats: () => get<DBStats>('/db/stats'),
  dbTweets: (limit = 50, offset = 0, q = '') =>
    get<DBPage>(`/db/tweets?limit=${limit}&offset=${offset}&q=${encodeURIComponent(q)}`),
  dbExtractions: (limit = 50, offset = 0, financeOnly = false, q = '') =>
    get<DBPage>(`/db/extractions?limit=${limit}&offset=${offset}&finance_only=${financeOnly}&q=${encodeURIComponent(q)}`),
  dbTickerScores: (limit = 50, offset = 0) =>
    get<DBPage>(`/db/ticker_scores?limit=${limit}&offset=${offset}`),
  dbThemeScores: (limit = 50, offset = 0) =>
    get<DBPage>(`/db/theme_scores?limit=${limit}&offset=${offset}`),
  dbEdges: (limit = 50, offset = 0) =>
    get<DBPage>(`/db/graph_edges?limit=${limit}&offset=${offset}`),
  dbSignals: (limit = 50, offset = 0) =>
    get<DBPage>(`/db/signals?limit=${limit}&offset=${offset}`),
  positionsOpen: () => get<PaperPosition[]>('/positions/open'),
  positionsClosed: (days = 7) => get<PaperPosition[]>(`/positions/closed?days=${days}`),
  approvalsPending: () => get<SignalApproval[]>('/approvals/pending'),
  approvalDecide: (id: number, decision: 'approved' | 'rejected') => {
    return fetch(`${BASE}/approvals/${id}/decide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    }).then(r => { if (!r.ok) throw new Error(`POST ${r.url} → ${r.status}`); return r.json() as Promise<{ ack: string }> })
  },
}
