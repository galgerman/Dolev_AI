import { useCallback, useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { api } from '../api'
import type { PaperPosition, SignalApproval } from '../types'

interface Props {
  onClose: () => void
}

function PnlBadge({ pnl }: { pnl: number | null | undefined }) {
  if (pnl == null) return <span className="text-gray-500 text-xs">n/a</span>
  const pos = pnl >= 0
  return (
    <span className={clsx('text-xs font-mono font-semibold', pos ? 'text-green-400' : 'text-red-400')}>
      {pnl >= 0 ? '+' : ''}{(pnl * 100).toFixed(2)}%
    </span>
  )
}

function SideBadge({ side }: { side: string }) {
  return (
    <span className={clsx(
      'text-[10px] font-bold px-1.5 py-0.5 rounded',
      side === 'buy' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
    )}>
      {side.toUpperCase()}
    </span>
  )
}

export function PaperPortfolio({ onClose }: Props) {
  const [pending, setPending] = useState<SignalApproval[]>([])
  const [openPos, setOpenPos] = useState<PaperPosition[]>([])
  const [closedPos, setClosedPos] = useState<PaperPosition[]>([])
  const [deciding, setDeciding] = useState<Set<number>>(new Set())
  const [ackMsg, setAckMsg] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const [p, o, c] = await Promise.all([
        api.approvalsPending(),
        api.positionsOpen(),
        api.positionsClosed(7),
      ])
      setPending(p)
      setOpenPos(o)
      setClosedPos(c)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    reload()
    const iv = window.setInterval(reload, 10_000)
    return () => window.clearInterval(iv)
  }, [reload])

  async function decide(id: number, decision: 'approved' | 'rejected') {
    setDeciding(prev => new Set([...prev, id]))
    try {
      const r = await api.approvalDecide(id, decision)
      setAckMsg(r.ack)
      setTimeout(() => setAckMsg(null), 5000)
      await reload()
    } catch (e) {
      setAckMsg(String(e))
    } finally {
      setDeciding(prev => { const s = new Set(prev); s.delete(id); return s })
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-stretch justify-end pointer-events-none">
      <div className="pointer-events-auto w-[420px] bg-gray-900 border-l border-gray-800 flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <p className="text-sm font-semibold text-gray-100">Paper Portfolio</p>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-lg leading-none">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-4">

          {/* Ack toast */}
          {ackMsg && (
            <div className="text-xs bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-200">
              {ackMsg}
            </div>
          )}

          {/* Pending approvals */}
          <section>
            <p className="text-[10px] uppercase tracking-widest text-gray-500 mb-2">
              Pending approvals ({pending.length})
            </p>
            {pending.length === 0 && (
              <p className="text-xs text-gray-600 italic">No pending approvals</p>
            )}
            {pending.map(a => (
              <div key={a.id} className="bg-gray-800 rounded-lg p-3 mb-2">
                <div className="flex items-center gap-2 mb-2">
                  <SideBadge side={a.side} />
                  <span className="text-sm font-bold text-gray-100">${a.ticker}</span>
                  <span className="text-[10px] text-gray-500 ml-auto">
                    {a.kind === 'open' ? 'Open position?' : 'Close position?'}
                  </span>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => decide(a.id, 'approved')}
                    disabled={deciding.has(a.id)}
                    className="flex-1 text-xs bg-green-800 hover:bg-green-700 text-green-100 py-1.5 rounded disabled:opacity-50"
                  >
                    {deciding.has(a.id) ? '…' : '✅ Approve'}
                  </button>
                  <button
                    onClick={() => decide(a.id, 'rejected')}
                    disabled={deciding.has(a.id)}
                    className="flex-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 py-1.5 rounded disabled:opacity-50"
                  >
                    ❌ Reject
                  </button>
                </div>
              </div>
            ))}
          </section>

          {/* Open positions */}
          <section>
            <p className="text-[10px] uppercase tracking-widest text-gray-500 mb-2">
              Open positions ({openPos.length})
            </p>
            {openPos.length === 0 && (
              <p className="text-xs text-gray-600 italic">No open positions</p>
            )}
            {openPos.map(p => (
              <div key={p.id} className="bg-gray-800 rounded-lg px-3 py-2 mb-1.5 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <SideBadge side={p.side} />
                  <span className="text-sm font-bold text-gray-100">${p.ticker}</span>
                  <span className="text-xs text-gray-500">@ ${p.entry_price.toFixed(2)}</span>
                </div>
                <div className="text-right">
                  <PnlBadge pnl={p.unrealized_pnl_pct} />
                  {p.live_price && (
                    <div className="text-[10px] text-gray-600">${p.live_price.toFixed(2)}</div>
                  )}
                </div>
              </div>
            ))}
          </section>

          {/* Closed positions */}
          <section>
            <p className="text-[10px] uppercase tracking-widest text-gray-500 mb-2">
              Closed (last 7 days)
            </p>
            {closedPos.length === 0 && (
              <p className="text-xs text-gray-600 italic">No closed positions</p>
            )}
            {closedPos.map(p => (
              <div key={p.id} className="bg-gray-800/60 rounded-lg px-3 py-2 mb-1.5">
                <div className="flex items-center justify-between mb-0.5">
                  <div className="flex items-center gap-2">
                    <SideBadge side={p.side} />
                    <span className="text-sm font-semibold text-gray-300">${p.ticker}</span>
                    <span className="text-xs text-gray-500">${p.entry_price.toFixed(2)} → ${p.exit_price?.toFixed(2) ?? '?'}</span>
                  </div>
                  <PnlBadge pnl={p.pnl_pct} />
                </div>
                {p.retrospective && (
                  <p className="text-[10px] text-gray-500 mt-1 italic leading-relaxed">
                    {p.retrospective.slice(0, 200)}
                  </p>
                )}
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  )
}
