import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Mover, MoversSnapshot } from '../types'
import { clsx } from 'clsx'

interface Props {
  onSelect?: (ticker: string) => void
  liveEvent?: { gainers: Mover[]; losers: Mover[]; captured_at: string | null } | null
}

function MoversList({ items, side, onSelect }: { items: Mover[]; side: 'gainer' | 'loser'; onSelect?: (t: string) => void }) {
  if (items.length === 0) {
    return <div className="text-xs text-gray-600 px-2 py-3">No data</div>
  }
  return (
    <div className="space-y-0.5">
      {items.map(m => (
        <button
          key={m.ticker}
          onClick={() => onSelect?.(m.ticker)}
          className="w-full flex items-center gap-2 text-xs px-2 py-1 hover:bg-gray-800 rounded text-left"
        >
          <span className="text-blue-400 font-semibold w-14 truncate">${m.ticker}</span>
          <span className={clsx('w-14 text-right font-mono', side === 'gainer' ? 'text-green-400' : 'text-red-400')}>
            {m.pct_change >= 0 ? '+' : ''}{m.pct_change.toFixed(2)}%
          </span>
          <span className="text-gray-500 w-12 text-right font-mono">${m.last_price.toFixed(2)}</span>
          <span className={clsx('text-[10px] ml-auto', m.rel_volume >= 2 ? 'text-orange-400' : 'text-gray-600')}>
            ×{m.rel_volume.toFixed(1)} vol
          </span>
        </button>
      ))}
    </div>
  )
}

export function MarketMovers({ onSelect, liveEvent }: Props) {
  const [data, setData] = useState<MoversSnapshot | null>(null)

  useEffect(() => {
    api.movers(15).then(setData).catch(() => setData(null))
  }, [])

  // Refresh on movers.updated WS event
  useEffect(() => {
    if (liveEvent) {
      setData({ gainers: liveEvent.gainers, losers: liveEvent.losers, captured_at: liveEvent.captured_at })
    }
  }, [liveEvent])

  return (
    <div className="panel flex flex-col min-h-0 overflow-hidden">
      <div className="flex items-center justify-between mb-2">
        <p className="panel-title mb-0">Market Movers</p>
        {data?.captured_at && (
          <span className="text-[10px] text-gray-600">
            {new Date(data.captured_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 flex-1 min-h-0 overflow-hidden">
        <div className="flex flex-col min-h-0">
          <p className="text-[10px] text-green-400 uppercase tracking-wide mb-1 px-1">Gainers</p>
          <div className="overflow-y-auto flex-1">
            <MoversList items={data?.gainers ?? []} side="gainer" onSelect={onSelect} />
          </div>
        </div>
        <div className="flex flex-col min-h-0">
          <p className="text-[10px] text-red-400 uppercase tracking-wide mb-1 px-1">Losers</p>
          <div className="overflow-y-auto flex-1">
            <MoversList items={data?.losers ?? []} side="loser" onSelect={onSelect} />
          </div>
        </div>
      </div>
    </div>
  )
}
