import type { Account, HealthInfo, TickerDrilldown, TickerScore, Signal, GraphSnapshot } from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json() as Promise<T>
}

export const api = {
  health: () => get<HealthInfo>('/health'),
  tickersLive: (limit = 20) => get<TickerScore[]>(`/tickers/live?limit=${limit}`),
  tickerDrilldown: (ticker: string) => get<TickerDrilldown>(`/tickers/${ticker}`),
  signalsRecent: (limit = 20) => get<Signal[]>(`/signals/recent?limit=${limit}`),
  graphSnapshot: () => get<GraphSnapshot>('/graph/snapshot'),
  accounts: () => get<Account[]>('/accounts'),
}
