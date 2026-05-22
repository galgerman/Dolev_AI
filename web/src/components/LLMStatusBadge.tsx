import { useEffect, useState } from 'react'
import type { LLMStatus } from '../types'

interface Props {
  status: LLMStatus | null
  backlog: { depth: number; capacity: number; dropped_total: number }
  lastCall?: { ts: number; latency_ms: number; is_finance: boolean } | null
}

function formatLatency(ms: number): string {
  if (!ms) return '–'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function LLMStatusBadge({ status, backlog, lastCall }: Props) {
  const [flash, setFlash] = useState(false)

  // Flash on new call
  useEffect(() => {
    if (!lastCall) return
    setFlash(true)
    const t = window.setTimeout(() => setFlash(false), 600)
    return () => window.clearTimeout(t)
  }, [lastCall?.ts])

  if (!status) {
    return (
      <span className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-gray-800 text-gray-500 text-[11px]">
        <span className="w-1.5 h-1.5 rounded-full bg-gray-600" /> LLM unavailable
      </span>
    )
  }
  const dotColor = status.healthy
    ? (flash ? 'bg-yellow-300' : 'bg-green-400')
    : status.running ? 'bg-yellow-400 animate-pulse' : 'bg-red-400'
  const depth = backlog.depth || status.backlog
  const cap = backlog.capacity || status.capacity
  const pct = cap > 0 ? Math.min(100, (depth / cap) * 100) : 0

  const calls = status.calls_total ?? 0
  const finance = status.calls_finance ?? 0
  const errors = status.calls_errors ?? 0
  const avgLat = status.avg_latency_ms ?? 0

  return (
    <div
      className={`flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] text-gray-300 transition-colors ${flash ? 'bg-yellow-900/40' : 'bg-gray-800'}`}
      title={[
        `Model: ${status.model}`,
        `Endpoint: ${status.endpoint || '(none)'}`,
        `Healthy: ${status.healthy}`,
        `Backlog: ${depth}/${cap}`,
        `Calls: ${calls} (${finance} finance, ${errors} errors)`,
        `Avg latency: ${avgLat}ms`,
        lastCall ? `Last call: ${formatLatency(lastCall.latency_ms)} (${lastCall.is_finance ? 'finance' : 'non-finance'})` : '',
      ].filter(Boolean).join('\n')}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
      <span className="font-mono">{status.model}</span>
      <span className="text-gray-500">·</span>
      <span className="tabular-nums">
        <span className="text-gray-200">{calls}</span>
        <span className="text-gray-600"> calls</span>
      </span>
      <span className="text-gray-500">·</span>
      <span className="tabular-nums">
        <span className={avgLat > 5000 ? 'text-yellow-400' : 'text-gray-200'}>{formatLatency(avgLat)}</span>
        <span className="text-gray-600"> avg</span>
      </span>
      {errors > 0 && (
        <>
          <span className="text-gray-500">·</span>
          <span className="text-red-400 tabular-nums">{errors} err</span>
        </>
      )}
      <span className="text-gray-500">·</span>
      <span className="relative inline-block w-10 h-1.5 rounded bg-gray-700 overflow-hidden">
        <span
          className={`absolute inset-y-0 left-0 ${pct > 80 ? 'bg-red-500' : pct > 50 ? 'bg-yellow-500' : 'bg-blue-500'}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-gray-500 tabular-nums">{depth}</span>
    </div>
  )
}
