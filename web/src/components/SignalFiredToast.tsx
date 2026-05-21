import { clsx } from 'clsx'
import type { Signal } from '../types'

interface Props {
  signal: Signal
}

export function SignalFiredToast({ signal }: Props) {
  const isBuy = signal.side === 'buy'
  return (
    <div className={clsx(
      'fixed bottom-6 right-6 z-50 max-w-sm rounded-xl p-4 shadow-2xl border',
      'animate-in slide-in-from-bottom duration-300',
      isBuy
        ? 'bg-green-950 border-green-700 text-green-100'
        : 'bg-red-950 border-red-700 text-red-100'
    )}>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-lg">{isBuy ? '🟢' : '🔴'}</span>
        <span className="font-bold text-base">{signal.side.toUpperCase()} ${signal.ticker}</span>
        <span className="ml-auto text-xs opacity-70">conviction {Math.round(signal.conviction * 100)}%</span>
      </div>
      <p className="text-xs opacity-80 leading-relaxed">{signal.rationale}</p>
      <p className="text-xs opacity-50 mt-1">size hint {(signal.suggested_size_pct * 100).toFixed(1)}% of portfolio</p>
    </div>
  )
}
