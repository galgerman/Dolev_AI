import { useState } from 'react'
import { api } from '../api'
import type { ConnectionStatus } from '../hooks/useEventStream'
import type { XAuthStatus } from '../types'

interface Props {
  wsStatus: ConnectionStatus
  threshold: number
  lastEval?: string
  xAuth: XAuthStatus
}

const STATUS_COLORS: Record<ConnectionStatus, string> = {
  connected: 'bg-green-500',
  connecting: 'bg-yellow-500 animate-pulse',
  disconnected: 'bg-red-500',
}

const STATE_LABEL: Record<XAuthStatus['state'], string> = {
  idle: 'Connect X',
  opening: 'Opening…',
  waiting: 'Log in & close browser',
  complete: 'Connected',
  error: 'Retry login',
}

export function Header({ wsStatus, threshold, lastEval, xAuth }: Props) {
  const [busy, setBusy] = useState(false)

  async function handleLogin() {
    if (busy || xAuth.state === 'opening' || xAuth.state === 'waiting') return
    setBusy(true)
    try {
      await api.xAuthLogin()
    } catch {
      // state update comes via WS event
    } finally {
      setBusy(false)
    }
  }

  const isActive = xAuth.state === 'opening' || xAuth.state === 'waiting'
  const isConnected = xAuth.logged_in

  return (
    <header className="flex items-center justify-between px-6 py-3 bg-gray-900 border-b border-gray-800 shrink-0">
      <div className="flex items-center gap-3">
        <span className="text-lg font-bold tracking-tight text-white">Dolev AI</span>
        <span className="text-xs text-gray-500">Live Monitor</span>
      </div>

      <div className="flex items-center gap-5 text-xs text-gray-400">
        <span>threshold <span className="text-white font-semibold">{threshold.toFixed(1)}</span></span>
        {lastEval && <span>last eval <span className="text-gray-300">{lastEval}</span></span>}

        {/* X login button */}
        <button
          onClick={handleLogin}
          disabled={isConnected || isActive || busy}
          className={[
            'flex items-center gap-1.5 px-3 py-1 rounded text-xs font-medium transition-colors',
            isConnected
              ? 'bg-green-900 text-green-400 cursor-default'
              : isActive
                ? 'bg-yellow-900 text-yellow-300 cursor-wait animate-pulse'
                : 'bg-gray-700 hover:bg-gray-600 text-gray-200 cursor-pointer',
          ].join(' ')}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-400' : isActive ? 'bg-yellow-400' : 'bg-gray-500'}`} />
          {STATE_LABEL[xAuth.state]}
        </button>

        {/* WebSocket status */}
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[wsStatus]}`} />
          <span>{wsStatus}</span>
        </div>
      </div>
    </header>
  )
}
