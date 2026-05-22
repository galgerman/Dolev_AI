import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { DBPage, DBStats } from '../types'

interface Props {
  onClose: () => void
}

type Tab = 'tweets' | 'extractions' | 'ticker_scores' | 'theme_scores' | 'edges' | 'signals'

const TABS: { id: Tab; label: string }[] = [
  { id: 'extractions', label: 'Extractions' },
  { id: 'tweets', label: 'Tweets' },
  { id: 'ticker_scores', label: 'Ticker Scores' },
  { id: 'theme_scores', label: 'Theme Scores' },
  { id: 'edges', label: 'Graph Edges' },
  { id: 'signals', label: 'Signals' },
]

const PAGE = 50

function relativeTime(iso?: string | null): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  const sec = Math.round((Date.now() - t) / 1000)
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`
  return new Date(iso).toLocaleString()
}

export function DBBrowser({ onClose }: Props) {
  const [tab, setTab] = useState<Tab>('extractions')
  const [stats, setStats] = useState<DBStats | null>(null)
  const [page, setPage] = useState<DBPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [financeOnly, setFinanceOnly] = useState(false)
  const [loading, setLoading] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)

  // Reset offset when tab/search changes
  useEffect(() => { setOffset(0); setExpanded(null) }, [tab, search, financeOnly])

  // Stats poller
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const s = await api.dbStats()
        if (alive) setStats(s)
      } catch { /* ignore */ }
    }
    tick()
    const iv = window.setInterval(tick, 4000)
    return () => { alive = false; window.clearInterval(iv) }
  }, [])

  // Page loader
  useEffect(() => {
    let alive = true
    const load = async () => {
      setLoading(true)
      try {
        let p: DBPage
        if (tab === 'tweets') p = await api.dbTweets(PAGE, offset, search)
        else if (tab === 'extractions') p = await api.dbExtractions(PAGE, offset, financeOnly, search)
        else if (tab === 'ticker_scores') p = await api.dbTickerScores(PAGE, offset)
        else if (tab === 'theme_scores') p = await api.dbThemeScores(PAGE, offset)
        else if (tab === 'edges') p = await api.dbEdges(PAGE, offset)
        else p = await api.dbSignals(PAGE, offset)
        if (alive) setPage(p)
      } catch (e) {
        if (alive) setPage({ total: 0, rows: [] })
      } finally {
        if (alive) setLoading(false)
      }
    }
    load()
    if (autoRefresh) {
      const iv = window.setInterval(load, 5000)
      return () => { alive = false; window.clearInterval(iv) }
    }
    return () => { alive = false }
  }, [tab, offset, search, financeOnly, autoRefresh])

  const rows = page?.rows ?? []
  const total = page?.total ?? 0
  const lastPage = Math.floor(Math.max(0, total - 1) / PAGE)
  const curPage = Math.floor(offset / PAGE)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="bg-gray-950 border border-gray-800 rounded-lg w-full max-w-7xl h-[88vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <span className="text-white font-semibold">Database</span>
            {stats && (
              <span className="text-xs text-gray-500">
                tweets {stats.tweets.toLocaleString()} ·
                extractions {stats.extractions.toLocaleString()}
                <span className="text-green-500"> ({stats.extractions_finance} finance)</span> ·
                ticker_scores {stats.tickers.toLocaleString()} ·
                edges {stats.edges.toLocaleString()} ·
                signals {stats.signals}
                {stats.last_extraction_at && (
                  <span className="text-gray-600"> · last extract {relativeTime(stats.last_extraction_at)}</span>
                )}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1 text-xs text-gray-500 cursor-pointer">
              <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} className="accent-blue-500" />
              auto-refresh
            </label>
            <button onClick={onClose} className="px-2 py-0.5 text-gray-400 hover:text-white">✕</button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-2 pt-2 border-b border-gray-800">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={[
                'px-3 py-1 text-xs rounded-t',
                tab === t.id
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-500 hover:text-gray-300',
              ].join(' ')}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Controls */}
        <div className="flex items-center gap-3 px-3 py-2 border-b border-gray-800 text-xs">
          {(tab === 'tweets' || tab === 'extractions') && (
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="search text or author..."
              className="bg-gray-900 border border-gray-800 rounded px-2 py-1 text-gray-200 w-64 focus:outline-none focus:border-blue-500"
            />
          )}
          {tab === 'extractions' && (
            <label className="flex items-center gap-1 text-gray-400 cursor-pointer">
              <input type="checkbox" checked={financeOnly} onChange={e => setFinanceOnly(e.target.checked)} className="accent-green-500" />
              finance only
            </label>
          )}
          <span className="ml-auto text-gray-500">
            {loading ? 'loading...' : `${total.toLocaleString()} rows`}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setOffset(o => Math.max(0, o - PAGE))}
              disabled={offset === 0}
              className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-30"
            >‹ prev</button>
            <span className="text-gray-500 tabular-nums px-2">{curPage + 1} / {lastPage + 1}</span>
            <button
              onClick={() => setOffset(o => o + PAGE)}
              disabled={curPage >= lastPage}
              className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 disabled:opacity-30"
            >next ›</button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 min-h-0 overflow-auto text-xs">
          {tab === 'extractions' && <ExtractionsTable rows={rows} expanded={expanded} setExpanded={setExpanded} />}
          {tab === 'tweets' && <TweetsTable rows={rows} />}
          {tab === 'ticker_scores' && <GenericTable rows={rows} columns={['ticker', 'score', 'voices', 'tweet_count', 'window_end']} />}
          {tab === 'theme_scores' && <GenericTable rows={rows} columns={['theme', 'score', 'voices', 'tweet_count', 'window_end']} />}
          {tab === 'edges' && <GenericTable rows={rows} columns={['from_id', 'to_id', 'edge_type', 'weight', 'sentiment', 'tweet_id', 'created_at']} />}
          {tab === 'signals' && <GenericTable rows={rows} columns={['ticker', 'side', 'conviction', 'suggested_size_pct', 'rationale', 'generated_at']} />}
        </div>
      </div>
    </div>
  )
}

// ── Sub-tables ──────────────────────────────────────────────────────────────

const SENT_COLOR: Record<string, string> = {
  positive: 'text-green-400',
  negative: 'text-red-400',
  neutral: 'text-gray-400',
}

function ExtractionsTable({ rows, expanded, setExpanded }: { rows: any[]; expanded: number | null; setExpanded: (n: number | null) => void }) {
  return (
    <table className="w-full">
      <thead className="sticky top-0 bg-gray-900 text-gray-500 text-[10px] uppercase">
        <tr>
          <th className="text-left px-2 py-1">id</th>
          <th className="text-left px-2 py-1">when</th>
          <th className="text-left px-2 py-1">author</th>
          <th className="text-left px-2 py-1">text</th>
          <th className="text-left px-2 py-1">fin</th>
          <th className="text-left px-2 py-1">tickers</th>
          <th className="text-left px-2 py-1">themes</th>
          <th className="text-right px-2 py-1">latency</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r: any) => (
          <>
            <tr
              key={r.id}
              className={`border-b border-gray-900 cursor-pointer hover:bg-gray-900 ${!r.is_finance ? 'opacity-60' : ''}`}
              onClick={() => setExpanded(expanded === r.id ? null : r.id)}
            >
              <td className="px-2 py-1 text-gray-600 font-mono">{r.id}</td>
              <td className="px-2 py-1 text-gray-500 whitespace-nowrap">{relativeTime(r.created_at)}</td>
              <td className="px-2 py-1 text-blue-300 whitespace-nowrap">@{r.author}</td>
              <td className="px-2 py-1 text-gray-300 max-w-xl truncate">{r.text}</td>
              <td className="px-2 py-1">
                <span className={r.is_finance ? 'text-green-400' : 'text-gray-600'}>{r.is_finance ? '✓' : '–'}</span>
              </td>
              <td className="px-2 py-1">
                <div className="flex flex-wrap gap-1">
                  {r.tickers.map((t: any) => (
                    <span key={t.ticker} className={`px-1 rounded bg-gray-800 ${SENT_COLOR[t.sentiment]}`}>
                      ${t.ticker}
                    </span>
                  ))}
                </div>
              </td>
              <td className="px-2 py-1">
                <div className="flex flex-wrap gap-1">
                  {r.themes.map((t: any) => (
                    <span key={t.theme} className={`px-1 rounded bg-gray-800 ${SENT_COLOR[t.sentiment]}`}>
                      {t.theme}
                    </span>
                  ))}
                </div>
              </td>
              <td className="px-2 py-1 text-gray-500 text-right tabular-nums">{r.latency_ms}ms</td>
            </tr>
            {expanded === r.id && (
              <tr className="bg-gray-900/50">
                <td colSpan={8} className="px-3 py-2">
                  <div className="text-gray-400 italic mb-1">→ {r.summary || '(no summary)'}</div>
                  <div className="text-gray-500 mb-1">Model: <span className="text-gray-300">{r.model}</span> · sentiment: <span className={SENT_COLOR[r.overall_sentiment]}>{r.overall_sentiment}</span></div>
                  <details className="text-gray-500">
                    <summary className="cursor-pointer hover:text-gray-300">raw LLM JSON</summary>
                    <pre className="text-[10px] mt-1 p-2 bg-gray-950 rounded overflow-x-auto whitespace-pre-wrap text-gray-400">{r.raw_json}</pre>
                  </details>
                </td>
              </tr>
            )}
          </>
        ))}
      </tbody>
    </table>
  )
}

function TweetsTable({ rows }: { rows: any[] }) {
  return (
    <table className="w-full">
      <thead className="sticky top-0 bg-gray-900 text-gray-500 text-[10px] uppercase">
        <tr>
          <th className="text-left px-2 py-1">id</th>
          <th className="text-left px-2 py-1">posted</th>
          <th className="text-left px-2 py-1">author</th>
          <th className="text-left px-2 py-1">text</th>
          <th className="text-right px-2 py-1">♥</th>
          <th className="text-right px-2 py-1">⟳</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r: any) => (
          <tr key={r.id} className="border-b border-gray-900 hover:bg-gray-900">
            <td className="px-2 py-1 text-gray-600 font-mono whitespace-nowrap">{r.id}</td>
            <td className="px-2 py-1 text-gray-500 whitespace-nowrap">{relativeTime(r.created_at)}</td>
            <td className="px-2 py-1 text-blue-300 whitespace-nowrap">@{r.author}</td>
            <td className="px-2 py-1 text-gray-300">{r.text}</td>
            <td className="px-2 py-1 text-gray-500 text-right tabular-nums">{r.like_count}</td>
            <td className="px-2 py-1 text-gray-500 text-right tabular-nums">{r.retweet_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function GenericTable({ rows, columns }: { rows: any[]; columns: string[] }) {
  return (
    <table className="w-full">
      <thead className="sticky top-0 bg-gray-900 text-gray-500 text-[10px] uppercase">
        <tr>
          {columns.map(c => (
            <th key={c} className="text-left px-2 py-1">{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r: any) => (
          <tr key={r.id} className="border-b border-gray-900 hover:bg-gray-900">
            {columns.map(c => {
              const v = r[c]
              const isTime = typeof v === 'string' && /\d{4}-\d{2}-\d{2}T/.test(v)
              return (
                <td key={c} className="px-2 py-1 text-gray-300 whitespace-nowrap max-w-md truncate">
                  {isTime ? relativeTime(v) : typeof v === 'number' ? v.toFixed(3) : String(v ?? '')}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
