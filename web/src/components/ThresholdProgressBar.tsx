import { clsx } from 'clsx'

interface Props {
  progress: number  // 0..∞ (1.0 = threshold)
  side?: 'buy' | 'sell' | null
}

export function ThresholdProgressBar({ progress, side }: Props) {
  const pct = Math.min(progress * 100, 100)
  const crossed = progress >= 1.0

  const barColor = crossed
    ? side === 'buy' ? 'bg-green-500' : 'bg-red-500'
    : progress >= 0.75
    ? 'bg-orange-500'
    : progress >= 0.50
    ? 'bg-yellow-500'
    : 'bg-gray-600'

  return (
    <div className="relative h-1.5 w-full bg-gray-800 rounded-full overflow-hidden">
      <div
        className={clsx('h-full rounded-full transition-all duration-500', barColor)}
        style={{ width: `${pct}%` }}
      />
      {/* threshold marker at 100% */}
      <div className="absolute right-0 top-0 h-full w-px bg-gray-500 opacity-60" />
    </div>
  )
}
