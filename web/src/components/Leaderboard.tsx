import { clsx } from 'clsx'
import type { ThemeScore, TickerScore } from '../types'
import { ThresholdProgressBar } from './ThresholdProgressBar'

interface Props {
  tickers: TickerScore[]
  pulsing: Set<string>
  synthesising: Set<string>
  onSelect: (ticker: string) => void
  selected: string | null
  themes?: ThemeScore[]
}

function drivingThemes(ticker: string, themes: ThemeScore[]): string[] {
  return themes
    .filter(t => t.cascade_targets.includes(ticker) && Math.abs(t.score) > 0)
    .sort((a, b) => Math.abs(b.score) - Math.abs(a.score))
    .slice(0, 2)
    .map(t => t.theme)
}

export function Leaderboard({ tickers, pulsing, synthesising, onSelect, selected, themes = [] }: Props) {
  const directTickers = tickers.filter(t => t.unique_credible_voices > 0)
  const cascadeTickers = tickers.filter(t => t.unique_credible_voices === 0 && Math.abs(t.score) > 0)

  const renderRow = (t: TickerScore, driving: string[] = [], dimmed = false) => {
    const side = t.score >= 0 ? 'buy' : 'sell'
    const pct = Math.round(t.threshold_progress * 100)
    const isProvisional = t.score === 0 && t.threshold_progress === 0
    const isPulsing = pulsing.has(t.ticker)
    const isSynthesising = synthesising.has(t.ticker)
    const isSelected = selected === t.ticker
    return (
      <button
        onClick={() => onSelect(t.ticker)}
        className={clsx(
          'w-full text-left rounded-lg px-3 py-2 transition-colors',
          dimmed && 'opacity-55',
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
            {driving.length > 0 && (
              <span className="text-[10px] text-purple-400 bg-purple-900/30 px-1.5 py-0.5 rounded">
                via {driving[0]}
              </span>
            )}
            {isSynthesising && (
              <span className="text-xs text-purple-400 animate-pulse">⚙ synthesising…</span>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-500">
            {t.unique_credible_voices > 0 && <span>{t.unique_credible_voices} voices</span>}
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
  }

  return (
    <div className="panel flex flex-col h-full overflow-hidden">
      <p className="panel-title">Live Ticker Leaderboard</p>
      <div className="overflow-y-auto flex-1 space-y-1">
        {tickers.length === 0 && (
          <p className="text-gray-600 text-xs py-4 text-center">Waiting for first eval cycle…</p>
        )}
        {directTickers.map(t => (
          <div key={t.ticker}>{renderRow(t)}</div>
        ))}
        {cascadeTickers.length > 0 && (
          <>
            <p className="text-[10px] text-gray-600 px-1 pt-2 pb-0.5 border-t border-gray-800 mt-1">
              cascade only (no direct voices)
            </p>
            {cascadeTickers.map(t => (
              <div key={t.ticker}>{renderRow(t, drivingThemes(t.ticker, themes), true)}</div>
            ))}
          </>
        )}
      </div>
    </div>
  )
}
