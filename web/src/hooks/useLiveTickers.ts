import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { GraphEdge, GraphNode, GraphSnapshot, Signal, TickerScore, WsEvent } from '../types'
import { useEventStream } from './useEventStream'

const SCORE_HISTORY_MAX = 120  // keep ~2 hours of 1-min ticks per ticker

interface ScorePoint { t: number; score: number }

export interface LiveState {
  tickers: TickerScore[]
  scoreHistory: Record<string, ScorePoint[]>
  signals: Signal[]
  graphNodes: GraphNode[]
  graphEdges: GraphEdge[]
  pulsingTickers: Set<string>
  synthesisingTickers: Set<string>
  lastFiredSignal: Signal | null
  threshold: number
}

const INITIAL: LiveState = {
  tickers: [],
  scoreHistory: {},
  signals: [],
  graphNodes: [],
  graphEdges: [],
  pulsingTickers: new Set(),
  synthesisingTickers: new Set(),
  lastFiredSignal: null,
  threshold: 5.0,
}

export function useLiveTickers() {
  const [state, setState] = useState<LiveState>(INITIAL)
  const stateRef = useRef(state)
  stateRef.current = state

  // Fetch initial threshold from health endpoint
  useEffect(() => {
    api.health().then(h => {
      setState(s => ({ ...s, threshold: h.threshold }))
    }).catch(() => {})
  }, [])

  const handleEvent = useCallback((event: WsEvent) => {
    setState(prev => {
      switch (event.type) {
        case 'snapshot': {
          const history: Record<string, ScorePoint[]> = { ...prev.scoreHistory }
          for (const ts of event.top_tickers) {
            const pts = history[ts.ticker] ?? []
            history[ts.ticker] = [...pts, { t: Date.now(), score: ts.score }]
          }
          return {
            ...prev,
            tickers: event.top_tickers,
            signals: event.recent_signals,
            graphNodes: event.graph.nodes,
            graphEdges: event.graph.edges,
            scoreHistory: history,
          }
        }

        case 'ticker.score_updated': {
          const updated = prev.tickers.map(t =>
            t.ticker === event.ticker
              ? { ...t, score: event.score, unique_credible_voices: event.voices, tweet_count: event.tweet_count, threshold_progress: event.threshold_progress }
              : t
          )
          // Insert if not present
          if (!updated.find(t => t.ticker === event.ticker)) {
            updated.push({
              ticker: event.ticker,
              score: event.score,
              unique_credible_voices: event.voices,
              tweet_count: event.tweet_count,
              window_start: new Date().toISOString(),
              window_end: new Date().toISOString(),
              top_tweet_urls: [],
              threshold_progress: event.threshold_progress,
            })
          }
          const sorted = [...updated].sort((a, b) => Math.abs(b.score) - Math.abs(a.score))
          const pts = prev.scoreHistory[event.ticker] ?? []
          const newPts = [...pts, { t: Date.now(), score: event.score }].slice(-SCORE_HISTORY_MAX)
          return {
            ...prev,
            tickers: sorted.slice(0, 20),
            scoreHistory: { ...prev.scoreHistory, [event.ticker]: newPts },
          }
        }

        case 'ticker.near_threshold': {
          const pulsing = new Set(prev.pulsingTickers)
          pulsing.add(event.ticker)
          // Clear pulse after 2.5s via a timeout — but since we can't do async here,
          // we use a separate effect; just add the ticker
          return { ...prev, pulsingTickers: pulsing }
        }

        case 'signal.synthesizing': {
          const s = new Set(prev.synthesisingTickers)
          s.add(event.ticker)
          return { ...prev, synthesisingTickers: s }
        }

        case 'signal.fired': {
          const newSig: Signal = {
            ticker: event.ticker,
            side: event.side,
            conviction: event.conviction,
            suggested_size_pct: event.suggested_size_pct,
            rationale: event.rationale,
            key_drivers: event.key_drivers,
            generated_at: event.generated_at,
          }
          const s = new Set(prev.synthesisingTickers)
          s.delete(event.ticker)
          return {
            ...prev,
            signals: [newSig, ...prev.signals].slice(0, 20),
            synthesisingTickers: s,
            lastFiredSignal: newSig,
          }
        }

        case 'graph.edge_added': {
          const edgeId = `acct:${event.author}→ticker:${event.ticker}`
          const exists = prev.graphEdges.find(
            e => e.source === `acct:${event.author}` && e.target === `ticker:${event.ticker}`
          )
          if (exists) {
            return {
              ...prev,
              graphEdges: prev.graphEdges.map(e =>
                e.source === `acct:${event.author}` && e.target === `ticker:${event.ticker}`
                  ? { ...e, weight: e.weight + event.weight }
                  : e
              ),
            }
          }
          return {
            ...prev,
            graphEdges: [...prev.graphEdges, {
              source: `acct:${event.author}`,
              target: `ticker:${event.ticker}`,
              weight: event.weight,
              sentiment: event.sentiment,
            }],
          }
        }

        default:
          return prev
      }
    })
  }, [])

  const wsStatus = useEventStream(handleEvent)

  // Clear pulsing tickers after 2.5s
  useEffect(() => {
    if (state.pulsingTickers.size === 0) return
    const timer = setTimeout(() => {
      setState(s => ({ ...s, pulsingTickers: new Set() }))
    }, 2500)
    return () => clearTimeout(timer)
  }, [state.pulsingTickers])

  // Clear lastFiredSignal toast after 8s
  useEffect(() => {
    if (!state.lastFiredSignal) return
    const timer = setTimeout(() => {
      setState(s => ({ ...s, lastFiredSignal: null }))
    }, 8000)
    return () => clearTimeout(timer)
  }, [state.lastFiredSignal])

  return { state, wsStatus }
}
