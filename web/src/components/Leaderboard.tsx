import { clsx } from 'clsx'
import type { TickerScore } from '../types'
import { ThresholdProgressBar } from './ThresholdProgressBar'

interface Props {
  tickers: TickerScore[]
  pulsing: Set<string>
  synthesising: Set<string>
  onSelect: (ticker: string) => void
  selected: string | null
}

function Sparkline({ points }: { points: number[] }) {
  if (points.length < 2) return null
  const min = Math.min(...points)
  const max = Math.max(...points)
  const range = max - min || 1
  const W = 48, H = 16
  const coords = points.map((v, i) => {
    const x = (i / (points.length - 1)) * W
    const y = H - ((v - min) / range) * H
    return `${x},${y}`
  }).join(' ')
  return (
    <svg width={W} height={H} className="shrink-0">
      <polyline points={coords} fill="none" stroke="#6b7280" strokeWidth="1.5" />
    </svg>
  )
}

export function Leaderboard({ tickers, pulsing, synthesising, onSelect, selected }: Props) {
  return (
    <div className="panel flex flex-col h-full overflow-hidden">
      <p className="panel-title">Live Ticker Leaderboard</p>
      <div className="overflow-y-auto flex-1 space-y-1">
        {tickers.length === 0 && (
          <p className="text-gray-600 text-xs py-4 text-center">Waiting for first eval cycle…</p>
        )}
        {tickers.map(t => {
          const side = t.score >= 0 ? 'buy' : 'sell'
          const pct = Math.round(t.threshold_progress * 100)
          const isProvisional = t.score === 0 && t.threshold_progress === 0
          const isPulsing = pulsing.has(t.ticker)
          const isSynthesising = synthesising.has(t.ticker)
          const isSelected = selected === t.ticker
          return (
            <button
              key={t.ticker}
              onClick={() => onSelect(t.ticker)}
              className={clsx(
                'w-full text-left rounded-lg px-3 py-2 transition-colors',
                isSelected ? 'bg-gray-700' : 'hover:bg-gray-800',
                isPulsing && 'animate-pulse_fast ring-1 ring-orange-500'
              )}
            >
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-2">
                  <span className={clsx(
                    'font-bold text-sm',
                    isProvisional ? 'text-blue-300' : t.score > 0 ? 'text-green-400' : 'text-red-400'
                  )}>
                    ${t.ticker}
                  </span>
                  <span className={clsx(
                    'text-xs',
                    isProvisional ? 'text-gray-500' : t.score > 0 ? 'text-green-500' : 'text-red-500'
                  )}>
                    {isProvisional ? 'queued' : `${t.score > 0 ? '↑' : '↓'} ${Math.abs(t.score).toFixed(2)}`}
                  </span>
                  {isSynthesising && (
                    <span className="text-xs text-purple-400 animate-pulse">⚙ synthesising…</span>
                  )}
                </div>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <span>{t.unique_credible_voices} voices</span>
                  <span className={clsx(
                    pct >= 100 ? (side === 'buy' ? 'text-green-400' : 'text-red-400') :
                    pct >= 75 ? 'text-orange-400' :
                    pct >= 50 ? 'text-yellow-400' : 'text-gray-500'
                  )}>
                    {pct}%
                  </span>
                </div>
              </div>
              <ThresholdProgressBar progress={t.threshold_progress} side={side as 'buy' | 'sell'} />
            </button>
          )
        })}
      </div>
    </div>
  )
}
