import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

interface ScorePoint { t: number; score: number }

interface Props {
  scoreHistory: Record<string, ScorePoint[]>
  threshold: number
  signals: { ticker: string; side: string; generated_at: string }[]
}

const COLORS = ['#60a5fa', '#34d399', '#f472b6', '#fb923c', '#a78bfa']

function fmtTime(ts: number) {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function ScoreChart({ scoreHistory, threshold, signals }: Props) {
  // Build unified time series: one row per unique timestamp, columns per ticker
  const tickers = Object.keys(scoreHistory).slice(0, 5)
  const allPoints = new Map<number, Record<string, number>>()

  for (const ticker of tickers) {
    for (const pt of scoreHistory[ticker] ?? []) {
      const bucket = Math.floor(pt.t / 60_000) * 60_000  // 1-min buckets
      if (!allPoints.has(bucket)) allPoints.set(bucket, {})
      allPoints.get(bucket)![ticker] = pt.score
    }
  }

  const data = [...allPoints.entries()]
    .sort((a, b) => a[0] - b[0])
    .slice(-60)
    .map(([t, vals]) => ({ t, ...vals }))

  if (data.length === 0) {
    return (
      <div className="panel flex items-center justify-center h-full text-gray-600 text-xs">
        Accumulating score history…
      </div>
    )
  }

  return (
    <div className="panel flex flex-col h-full">
      <p className="panel-title">Score vs Threshold</p>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="t"
              tickFormatter={fmtTime}
              tick={{ fontSize: 10, fill: '#6b7280' }}
              minTickGap={40}
            />
            <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} />
            <Tooltip
              contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 11 }}
              labelFormatter={v => fmtTime(Number(v))}
              formatter={(val: number, name: string) => [val.toFixed(3), `$${name}`]}
            />
            <Legend formatter={v => `$${v}`} wrapperStyle={{ fontSize: 11 }} />
            <ReferenceLine y={threshold} stroke="#22c55e" strokeDasharray="4 2" label={{ value: `+${threshold}`, fill: '#22c55e', fontSize: 10 }} />
            <ReferenceLine y={-threshold} stroke="#ef4444" strokeDasharray="4 2" label={{ value: `-${threshold}`, fill: '#ef4444', fontSize: 10 }} />
            {tickers.map((ticker, i) => (
              <Line
                key={ticker}
                type="monotone"
                dataKey={ticker}
                stroke={COLORS[i % COLORS.length]}
                dot={false}
                strokeWidth={1.5}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
