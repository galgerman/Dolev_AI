import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ConnectionStatus } from '../hooks/useEventStream'
import type { AgentStatus, CollectionProgress, LLMStatus, XAuthStatus } from '../types'
import { LLMStatusBadge } from './LLMStatusBadge'

interface Props {
  wsStatus: ConnectionStatus
  threshold: number
  lastEval?: string
  xAuth: XAuthStatus
  collection: CollectionProgress
  llmStatus: LLMStatus | null
  extractionBacklog: { depth: number; capacity: number; dropped_total: number }
  lastLlmCall?: { ts: number; latency_ms: number; is_finance: boolean } | null
  onOpenDB: () => void
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

export function Header({ wsStatus, threshold, lastEval, xAuth, collection, llmStatus, extractionBacklog, lastLlmCall, onOpenDB }: Props) {
  const [busy, setBusy] = useState(false)
  const [agentBusy, setAgentBusy] = useState(false)
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    async function refresh() {
      try {
        const status = await api.agentStatus()
        if (!cancelled) setAgentStatus(status)
      } catch {
        if (!cancelled) setAgentStatus(null)
      }
    }
    refresh()
    const interval = window.setInterval(refresh, 5000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [])

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

  async function handleAgentToggle() {
    if (agentBusy || !agentStatus) return
    setAgentBusy(true)
    try {
      const next = agentStatus.running ? await api.agentStop() : await api.agentStart()
      setAgentStatus(next)
    } catch {
      setAgentStatus(null)
    } finally {
      setAgentBusy(false)
    }
  }

  const isActive = xAuth.state === 'opening' || xAuth.state === 'waiting'
  const isConnected = xAuth.logged_in
  const agentRunning = agentStatus?.running ?? false
  const agentLabel = agentStatus
    ? agentBusy
      ? 'Working...'
      : agentRunning
        ? 'Stop Agent'
        : 'Start Agent'
    : 'Agent unavailable'

  return (
    <header className="flex items-center justify-between gap-4 px-6 py-3 bg-gray-900 border-b border-gray-800 shrink-0">
      <div className="flex items-center gap-3 shrink-0">
        <span className="text-lg font-bold tracking-tight text-white whitespace-nowrap">Dolev AI</span>
        <span className="text-xs text-gray-500">Live Monitor</span>
      </div>

      <div className="flex items-center justify-end gap-3 text-xs text-gray-400 min-w-0">
        <LLMStatusBadge status={llmStatus} backlog={extractionBacklog} lastCall={lastLlmCall} />
        <button
          onClick={onOpenDB}
          className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 text-[11px]"
          title="Browse the SQLite database"
        >
          ⛁ DB
        </button>
        <span>threshold <span className="text-white font-semibold">{threshold.toFixed(1)}</span></span>
        {lastEval && <span>last eval <span className="text-gray-300">{lastEval}</span></span>}
        {collection.total > 0 && (
          <span className="min-w-[220px] text-gray-500">
            scrape{' '}
            <span className="text-gray-300">{collection.completed}/{collection.total}</span>
            {' '}acct
            {collection.current_handle && (
              <span> · <span className="text-blue-300">@{collection.current_handle}</span></span>
            )}
            <span> · <span className="text-gray-300">{collection.tweets_found}</span> tweets</span>
            <span> · <span className="text-gray-300">{collection.tickers_found}</span> tickers</span>
          </span>
        )}

        <button
          onClick={handleAgentToggle}
          disabled={!agentStatus || agentBusy}
          className={[
            'flex min-w-[96px] items-center justify-center gap-1.5 px-3 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors',
            !agentStatus
              ? 'bg-gray-800 text-gray-500 cursor-not-allowed'
              : agentRunning
                ? 'bg-red-950 text-red-300 hover:bg-red-900 cursor-pointer'
                : 'bg-blue-950 text-blue-300 hover:bg-blue-900 cursor-pointer',
          ].join(' ')}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${agentRunning ? 'bg-red-400' : 'bg-blue-400'}`} />
          {agentLabel}
        </button>

        <button
          onClick={handleLogin}
          disabled={isConnected || isActive || busy}
          className={[
            'flex min-w-[96px] items-center justify-center gap-1.5 px-3 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors',
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
