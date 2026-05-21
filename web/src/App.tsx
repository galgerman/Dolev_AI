import { useState } from 'react'
import { Header } from './components/Header'
import { Leaderboard } from './components/Leaderboard'
import { ScoreChart } from './components/ScoreChart'
import { SignalFiredToast } from './components/SignalFiredToast'
import { TickerDrilldown } from './components/TickerDrilldown'
import { TrustGraph } from './components/TrustGraph'
import { useLiveTickers } from './hooks/useLiveTickers'

export default function App() {
  const { state, wsStatus } = useLiveTickers()
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null)

  const lastEval = state.tickers[0]?.window_end
    ? new Date(state.tickers[0].window_end).toLocaleTimeString()
    : undefined

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <Header wsStatus={wsStatus} threshold={state.threshold} lastEval={lastEval} xAuth={state.xAuth} />

      {/* Main grid: 3 columns × 2 rows */}
      <div className="flex-1 min-h-0 grid grid-cols-3 grid-rows-2 gap-3 p-3">
        {/* Row 1, col 1: Leaderboard */}
        <div className="row-span-2 min-h-0">
          <Leaderboard
            tickers={state.tickers}
            pulsing={state.pulsingTickers}
            synthesising={state.synthesisingTickers}
            onSelect={setSelectedTicker}
            selected={selectedTicker}
          />
        </div>

        {/* Row 1, col 2: Trust Graph */}
        <div className="col-span-1 min-h-0">
          <TrustGraph
            nodes={state.graphNodes}
            edges={state.graphEdges}
            onTickerClick={setSelectedTicker}
          />
        </div>

        {/* Row 1, col 3: Drilldown (or placeholder) */}
        <div className="row-span-2 min-h-0">
          {selectedTicker ? (
            <TickerDrilldown
              ticker={selectedTicker}
              threshold={state.threshold}
              onClose={() => setSelectedTicker(null)}
            />
          ) : (
            <div className="panel flex flex-col items-center justify-center h-full text-gray-700 text-xs gap-2">
              <span className="text-3xl">⬅</span>
              <span>Click a ticker to inspect</span>
            </div>
          )}
        </div>

        {/* Row 2, col 2: Score chart */}
        <div className="min-h-0">
          <ScoreChart
            scoreHistory={state.scoreHistory}
            threshold={state.threshold}
            signals={state.signals}
          />
        </div>
      </div>

      {/* Toast overlay for new signals */}
      {state.lastFiredSignal && (
        <SignalFiredToast signal={state.lastFiredSignal} />
      )}
    </div>
  )
}
