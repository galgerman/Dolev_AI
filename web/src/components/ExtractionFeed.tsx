import { useMemo, useState } from 'react'
import type { Extraction } from '../types'

interface Props {
  extractions: Extraction[]
  onTickerClick?: (ticker: string) => void
}

const SENT_COLOR: Record<string, string> = {
  positive: 'text-green-400',
  negative: 'text-red-400',
  neutral: 'text-gray-400',
}

const SENT_GLYPH: Record<string, string> = {
  positive: '+',
  negative: '−',
  neutral: '·',
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function ExtractionFeed({ extractions, onTickerClick }: Props) {
  const [paused, setPaused] = useState(false)
  const [filter, setFilter] = useState<'all' | 'finance'>('all')

  const counts = useMemo(() => ({
    total: extractions.length,
    finance: extractions.filter(e => e.is_finance).length,
  }), [extractions])

  const visible = useMemo(() => {
    const items = filter === 'finance' ? extractions.filter(e => e.is_finance) : extractions
    return items.slice(0, 100)
  }, [extractions, filter])

  return (
    <div
      className="panel flex flex-col h-full"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="flex items-center justify-between mb-1">
        <p className="panel-title">Extraction Feed</p>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <button
            onClick={() => setFilter(f => f === 'all' ? 'finance' : 'all')}
            className="px-1.5 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            title="Toggle between finance-only and all extractions"
          >
            {filter === 'finance' ? 'finance only' : 'all posts'}
          </button>
          <span className="tabular-nums">
            <span className="text-green-400">{counts.finance}</span>
            <span className="text-gray-600"> / {counts.total}</span>
          </span>
          <span className={paused ? 'text-yellow-400' : 'text-gray-600'}>
            {paused ? 'paused' : 'live'}
          </span>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-1 text-xs">
        {visible.length === 0 && (
          <div className="text-gray-600 italic text-center py-4">
            Waiting for LLM extractions…
          </div>
        )}
        {visible.map(ex => (
          <div
            key={`${ex.tweet_id}-${ex.id}`}
            className={[
              'rounded border p-1.5',
              ex.is_finance
                ? 'border-gray-800 bg-gray-900/40'
                : 'border-gray-900 bg-gray-900/10 opacity-60',
            ].join(' ')}
          >
            <div className="flex items-center justify-between text-[10px] text-gray-500 mb-0.5">
              <span>
                <span className="text-gray-400">{formatTime(ex.created_at)}</span>
                <span className="text-blue-300 ml-2">@{ex.author}</span>
                {!ex.is_finance && (
                  <span className="ml-2 px-1 rounded bg-gray-800 text-gray-500 text-[9px]">non-finance</span>
                )}
              </span>
              <span className="text-gray-600">
                {ex.model} · {ex.latency_ms}ms
              </span>
            </div>

            <div className={`leading-tight mb-1 line-clamp-2 ${ex.is_finance ? 'text-gray-300' : 'text-gray-500'}`}>
              {ex.text}
            </div>

            {ex.summary && (
              <div className="text-gray-500 italic text-[11px] mb-1">→ {ex.summary}</div>
            )}

            <div className="flex flex-wrap gap-1">
              {ex.themes.map(th => (
                <span
                  key={th.theme}
                  className={`px-1.5 py-0.5 rounded bg-gray-800 text-[10px] ${SENT_COLOR[th.sentiment]}`}
                  title={`${th.theme} · ${th.sentiment} · ${(th.confidence * 100).toFixed(0)}%`}
                >
                  {SENT_GLYPH[th.sentiment]} {th.theme}
                </span>
              ))}
              {ex.tickers.map(tm => (
                <button
                  key={tm.ticker}
                  onClick={() => onTickerClick?.(tm.ticker)}
                  className={`px-1.5 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-[10px] ${SENT_COLOR[tm.sentiment]} ${tm.explicit ? 'font-bold' : ''}`}
                  title={`${tm.ticker} · ${tm.sentiment} · ${(tm.confidence * 100).toFixed(0)}% · ${tm.explicit ? 'explicit' : 'inferred'}`}
                >
                  ${tm.ticker} {SENT_GLYPH[tm.sentiment]}{(tm.confidence * 100).toFixed(0)}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
