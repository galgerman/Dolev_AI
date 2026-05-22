import { useEffect, useState } from 'react'
import { api } from '../api'
import type { TickerDrilldown as DrilldownData } from '../types'
import { ThresholdProgressBar } from './ThresholdProgressBar'
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis, ReferenceLine,
} from 'recharts'
import { clsx } from 'clsx'

interface Props {
  ticker: string
  threshold: number
  onClose: () => void
}

function fmtTime(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

const SENTIMENT_LABELS: Record<string, string> = {
  positive: '🟢 bullish',
  negative: '🔴 bearish',
  neutral: '⚪ neutral',
}

export function TickerDrilldown({ ticker, threshold, onClose }: Props) {
  const [data, setData] = useState<DrilldownData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    setError(null)
    api.tickerDrilldown(ticker)
      .then(setData)
      .catch(e => setError(e.message))
  }, [ticker])

  const side = data && data.current_score > 0 ? 'buy' : 'sell'

  return (
    <div className="panel flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="panel-title mb-0">${ticker} drilldown</p>
          {data && (
            <div className="flex items-center gap-3 mt-1 text-xs">
              <span className={clsx(side === 'buy' ? 'text-green-400' : 'text-red-400', 'font-semibold')}>
                {side === 'buy' ? '↑ BULLISH' : '↓ BEARISH'} {Math.abs(data.current_score).toFixed(3)}
              </span>
              <span className="text-gray-500">{data.unique_credible_voices} voices</span>
              <span className={data.threshold_progress >= 1 ? 'text-green-400' : data.threshold_progress >= 0.75 ? 'text-orange-400' : 'text-gray-500'}>
                {Math.round(data.threshold_progress * 100)}% to threshold
              </span>
            </div>
          )}
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-white text-lg leading-none">×</button>
      </div>

      {!data && !error && (
        <div className="flex-1 flex items-center justify-center text-gray-600 text-xs animate-pulse">Loading…</div>
      )}
      {error && (
        <div className="flex-1 flex items-center justify-center text-red-500 text-xs">{error}</div>
      )}

      {data && (
        <div className="flex-1 min-h-0 overflow-y-auto space-y-4">
          {/* Progress bar */}
          <div>
            <ThresholdProgressBar progress={data.threshold_progress} side={side as 'buy' | 'sell'} />
          </div>

          {/* Score timeline */}
          {data.score_history.length > 1 && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Score timeline (last 4h)</p>
              <div className="h-24">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={data.score_history.map(([t, s]) => ({ t, s }))}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis dataKey="t" tickFormatter={v => fmtTime(v as string)} tick={{ fontSize: 9, fill: '#6b7280' }} minTickGap={40} />
                    <YAxis tick={{ fontSize: 9, fill: '#6b7280' }} width={32} />
                    <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 10 }} labelFormatter={v => fmtTime(v as string)} />
                    <ReferenceLine y={threshold} stroke="#22c55e" strokeDasharray="4 2" />
                    <ReferenceLine y={-threshold} stroke="#ef4444" strokeDasharray="4 2" />
                    <Line type="monotone" dataKey="s" stroke="#60a5fa" dot={false} strokeWidth={1.5} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Contributing accounts */}
          {data.contributing_accounts.length > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Contributing accounts</p>
              <div className="space-y-1">
                {data.contributing_accounts.map(a => (
                  <div key={a.handle} className="flex items-center gap-3 text-xs">
                    <span className="text-blue-400 w-28 truncate">@{a.handle}</span>
                    <span className="text-gray-600">T{a.tier}</span>
                    <span className="text-gray-500">{a.tweet_count} tweets</span>
                    <div className="flex-1 bg-gray-800 rounded h-1">
                      <div className="bg-blue-500 h-1 rounded" style={{ width: `${a.credibility * 100}%` }} />
                    </div>
                    <span className="text-gray-600 w-10 text-right">{(a.credibility * 100).toFixed(0)}%</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Voices — the tweets the LLM actually extracted this ticker from */}
          {data.recent_tweets.length > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Voices (last hour)</p>
              <div className="space-y-2">
                {data.recent_tweets.map(t => (
                  <div key={t.id} className="bg-gray-800 rounded-lg p-2 text-xs">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-blue-400">@{t.author}</span>
                      <span className="text-gray-600">{new Date(t.created_at).toLocaleTimeString()}</span>
                    </div>
                    {t.sentiment && (
                      <div className="flex items-center gap-2 text-[10px] mb-1">
                        <span>{SENTIMENT_LABELS[t.sentiment] ?? t.sentiment}</span>
                        {t.confidence != null && (
                          <span className="text-gray-500">conf {Math.round(t.confidence * 100)}%</span>
                        )}
                        {t.explicit === false && (
                          <span className="text-gray-600 italic">inferred</span>
                        )}
                      </div>
                    )}
                    <p className="text-gray-300 leading-relaxed">{t.text}</p>
                    <div className="flex gap-3 mt-1 text-gray-600">
                      <span>♥ {t.like_count}</span>
                      <span>↺ {t.retweet_count}</span>
                      <a href={t.url} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:underline ml-auto">open ↗</a>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
