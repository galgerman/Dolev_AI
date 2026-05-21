import type { ConnectionStatus } from '../hooks/useEventStream'

interface Props {
  wsStatus: ConnectionStatus
  threshold: number
  lastEval?: string
}

const STATUS_COLORS: Record<ConnectionStatus, string> = {
  connected: 'bg-green-500',
  connecting: 'bg-yellow-500 animate-pulse',
  disconnected: 'bg-red-500',
}

export function Header({ wsStatus, threshold, lastEval }: Props) {
  return (
    <header className="flex items-center justify-between px-6 py-3 bg-gray-900 border-b border-gray-800 shrink-0">
      <div className="flex items-center gap-3">
        <span className="text-lg font-bold tracking-tight text-white">Dolev AI</span>
        <span className="text-xs text-gray-500">Live Monitor</span>
      </div>
      <div className="flex items-center gap-5 text-xs text-gray-400">
        <span>threshold <span className="text-white font-semibold">{threshold.toFixed(1)}</span></span>
        {lastEval && <span>last eval <span className="text-gray-300">{lastEval}</span></span>}
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[wsStatus]}`} />
          <span>{wsStatus}</span>
        </div>
      </div>
    </header>
  )
}
