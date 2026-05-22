import { useState } from 'react'
import { DBBrowser } from './components/DBBrowser'
import { ExtractionFeed } from './components/ExtractionFeed'
import { Header } from './components/Header'
import { Leaderboard } from './components/Leaderboard'
import { PaperPortfolio } from './components/PaperPortfolio'
import { ScoreChart } from './components/ScoreChart'
import { SignalFiredToast } from './components/SignalFiredToast'
import { TickerDrilldown } from './components/TickerDrilldown'
import { TrustGraph } from './components/TrustGraph'
import { useLiveTickers } from './hooks/useLiveTickers'

export default function App() {
  const { state, wsStatus } = useLiveTickers()
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null)
  const [showDB, setShowDB] = useState(false)
  const [showPortfolio, setShowPortfolio] = useState(false)

  const lastEval = state.tickers[0]?.window_end
    ? new Date(state.tickers[0].window_end).toLocaleTimeString()
    : undefined

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <Header
        wsStatus={wsStatus}
        threshold={state.threshold}
        lastEval={lastEval}
        xAuth={state.xAuth}
        collection={state.collection}
        llmStatus={state.llmStatus}
        extractionBacklog={state.extractionBacklog}
        lastLlmCall={state.lastLlmCall}
        onOpenDB={() => setShowDB(true)}
        onOpenPortfolio={() => setShowPortfolio(true)}
      />

      {/* Main grid: 3 columns × 2 rows */}
      <div className="flex-1 min-h-0 grid grid-cols-3 grid-rows-2 gap-3 p-3">
        {/* Col 1: Leaderboard (full height) */}
        <div className="row-span-2 min-h-0">
          <Leaderboard
            tickers={state.tickers}
            pulsing={state.pulsingTickers}
            synthesising={state.synthesisingTickers}
            onSelect={setSelectedTicker}
            selected={selectedTicker}
            themes={state.themes}
          />
        </div>

        {/* Col 2 row 1: Trust Graph */}
        <div className="min-h-0">
          <TrustGraph
            nodes={state.graphNodes}
            edges={state.graphEdges}
            onTickerClick={setSelectedTicker}
          />
        </div>

        {/* Col 3 row 1 + 2: Extraction Feed (default) or Drilldown when a ticker is selected */}
        <div className="row-span-2 min-h-0">
          {selectedTicker ? (
            <TickerDrilldown
              ticker={selectedTicker}
              threshold={state.threshold}
              onClose={() => setSelectedTicker(null)}
            />
          ) : (
            <ExtractionFeed
              extractions={state.extractions}
              onTickerClick={setSelectedTicker}
            />
          )}
        </div>

        {/* Col 2 row 2: Score chart */}
        <div className="min-h-0">
          <ScoreChart
            scoreHistory={state.scoreHistory}
            threshold={state.threshold}
            signals={state.signals}
          />
        </div>
      </div>

      {state.lastFiredSignal && (
        <SignalFiredToast signal={state.lastFiredSignal} />
      )}
      {showDB && <DBBrowser onClose={() => setShowDB(false)} />}
      {showPortfolio && <PaperPortfolio onClose={() => setShowPortfolio(false)} />}
    </div>
  )
}
