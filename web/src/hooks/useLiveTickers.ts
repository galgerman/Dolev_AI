import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type {
  CollectionProgress,
  Extraction,
  GraphEdge,
  GraphNode,
  GraphSnapshot,
  LLMStatus,
  Signal,
  ThemeScore,
  TickerScore,
  WsEvent,
  XAuthStatus,
} from '../types'
import { useEventStream } from './useEventStream'

const SCORE_HISTORY_MAX = 120  // keep ~2 hours of 1-min ticks per ticker

interface ScorePoint { t: number; score: number }

export interface LLMCall {
  ts: number
  tweet_id: string
  latency_ms: number
  is_finance: boolean
  tickers_n: number
  themes_n: number
}

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
  xAuth: XAuthStatus
  collection: CollectionProgress
  themes: ThemeScore[]
  extractions: Extraction[]
  llmStatus: LLMStatus | null
  extractionBacklog: { depth: number; capacity: number; dropped_total: number }
  lastLlmCall: LLMCall | null
  llmCallHistory: LLMCall[]
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
  xAuth: { state: 'idle', logged_in: false },
  collection: {
    active: false,
    completed: 0,
    total: 0,
    current_handle: null,
    tweets_found: 0,
    tickers_found: 0,
    last_handle: null,
  },
  themes: [],
  extractions: [],
  llmStatus: null,
  extractionBacklog: { depth: 0, capacity: 0, dropped_total: 0 },
  lastLlmCall: null,
  llmCallHistory: [],
}

function upsertNode(nodes: GraphNode[], node: GraphNode) {
  const exists = nodes.find(n => n.id === node.id)
  if (exists) return nodes
  return [...nodes, node]
}

function upsertTicker(tickers: TickerScore[], ticker: string, voices: number, tweetCount: number) {
  const now = new Date().toISOString()
  const updated = tickers.map(t =>
    t.ticker === ticker
      ? {
          ...t,
          unique_credible_voices: Math.max(t.unique_credible_voices, voices),
          tweet_count: Math.max(t.tweet_count, tweetCount),
        }
      : t
  )
  if (updated.find(t => t.ticker === ticker)) return updated
  return [...updated, {
    ticker,
    score: 0,
    unique_credible_voices: voices,
    tweet_count: tweetCount,
    window_start: now,
    window_end: now,
    top_tweet_urls: [],
    threshold_progress: 0,
  }]
}

export function useLiveTickers() {
  const [state, setState] = useState<LiveState>(INITIAL)
  const stateRef = useRef(state)
  stateRef.current = state

  // Fetch initial threshold, X auth status, and LLM status
  useEffect(() => {
    api.health().then(h => {
      setState(s => ({ ...s, threshold: h.threshold }))
    }).catch(() => {})
    api.xAuthStatus().then(status => {
      setState(s => ({ ...s, xAuth: status }))
    }).catch(() => {})

    let cancelled = false
    async function refreshLLM() {
      try {
        const s = await api.llmStatus()
        if (!cancelled) setState(prev => ({ ...prev, llmStatus: s }))
      } catch {
        if (!cancelled) setState(prev => ({ ...prev, llmStatus: null }))
      }
    }
    refreshLLM()
    const iv = window.setInterval(refreshLLM, 7000)
    return () => { cancelled = true; window.clearInterval(iv) }
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
            themes: event.top_themes ?? [],
            extractions: event.recent_extractions ?? [],
          }
        }

        case 'post.extracted': {
          const newExtraction: Extraction = {
            id: 0,
            tweet_id: event.tweet_id,
            author: event.author,
            text: event.text,
            url: event.url,
            is_finance: event.is_finance,
            overall_sentiment: event.overall_sentiment as 'positive' | 'negative' | 'neutral',
            summary: event.summary,
            tickers: event.tickers,
            themes: event.themes,
            model: event.model,
            latency_ms: event.latency_ms,
            created_at: event.created_at,
          }
          // Dedup by tweet_id, keep most recent first
          const filtered = prev.extractions.filter(e => e.tweet_id !== event.tweet_id)
          return { ...prev, extractions: [newExtraction, ...filtered].slice(0, 200) }
        }

        case 'theme.score_updated': {
          const updated = prev.themes.map(t =>
            t.theme === event.theme
              ? { ...t, score: event.score, voices: event.voices, tweet_count: event.tweet_count,
                  threshold_progress: prev.threshold ? Math.abs(event.score) / prev.threshold : 0 }
              : t
          )
          if (!updated.find(t => t.theme === event.theme)) {
            updated.push({
              theme: event.theme, score: event.score, voices: event.voices,
              tweet_count: event.tweet_count,
              threshold_progress: prev.threshold ? Math.abs(event.score) / prev.threshold : 0,
              cascade_targets: [],
            })
          }
          const sorted = [...updated].sort((a, b) => Math.abs(b.score) - Math.abs(a.score)).slice(0, 30)
          return { ...prev, themes: sorted }
        }

        case 'extraction.backlog':
          return {
            ...prev,
            extractionBacklog: {
              depth: event.depth, capacity: event.capacity,
              dropped_total: event.dropped_total,
            },
          }

        case 'llm.call': {
          const call: LLMCall = {
            ts: event.ts,
            tweet_id: event.tweet_id,
            latency_ms: event.latency_ms,
            is_finance: event.is_finance,
            tickers_n: event.tickers_n,
            themes_n: event.themes_n,
          }
          return {
            ...prev,
            lastLlmCall: call,
            llmCallHistory: [call, ...prev.llmCallHistory].slice(0, 100),
          }
        }

        case 'collection.started':
          return {
            ...prev,
            collection: {
              active: true,
              completed: event.completed,
              total: event.total,
              current_handle: event.current_handle,
              tweets_found: event.tweets_found,
              tickers_found: event.tickers_found,
              last_handle: null,
            },
          }

        case 'collection.account_started':
          return {
            ...prev,
            collection: {
              ...prev.collection,
              active: true,
              completed: event.completed,
              total: event.total,
              current_handle: event.handle,
              tweets_found: event.tweets_found,
              tickers_found: event.tickers_found,
              error: undefined,
            },
          }

        case 'collection.account_completed':
          return {
            ...prev,
            collection: {
              ...prev.collection,
              active: true,
              completed: event.completed,
              total: event.total,
              current_handle: event.completed === event.total ? null : prev.collection.current_handle,
              tweets_found: event.tweets_found,
              tickers_found: event.tickers_found,
              last_handle: event.handle,
            },
          }

        case 'collection.finished':
          return {
            ...prev,
            collection: {
              ...prev.collection,
              active: false,
              completed: event.completed,
              total: event.total,
              current_handle: null,
              tweets_found: event.tweets_found,
              tickers_found: event.tickers_found,
            },
          }

        case 'collection.failed':
          return {
            ...prev,
            collection: {
              ...prev.collection,
              active: false,
              current_handle: null,
              error: event.message,
            },
          }

        case 'ticker.discovered': {
          const tickers = upsertTicker(prev.tickers, event.ticker, event.voices, event.tweet_count)
            .sort((a, b) => Math.abs(b.score) - Math.abs(a.score) || b.tweet_count - a.tweet_count)
          return { ...prev, tickers: tickers.slice(0, 20) }
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
          // Prefer new from_id/to_id format; fall back to legacy author/ticker
          const source = event.from_id ?? (event.author ? `acct:${event.author}` : null)
          const target = event.to_id ?? (event.ticker ? `ticker:${event.ticker}` : null)
          if (!source || !target) return prev

          const exists = prev.graphEdges.find(e => e.source === source && e.target === target)
          let graphNodes = prev.graphNodes
          if (source.startsWith('acct:')) {
            graphNodes = upsertNode(graphNodes, {
              id: source, type: 'account', label: '@' + source.slice(5),
              size: 0.5, sentiment: 'neutral', tier: 3,
            })
          } else if (source.startsWith('theme:')) {
            graphNodes = upsertNode(graphNodes, {
              id: source, type: 'theme', label: source.slice(6),
              size: 0.3, sentiment: event.sentiment, tier: 0,
            })
          }
          if (target.startsWith('ticker:')) {
            graphNodes = upsertNode(graphNodes, {
              id: target, type: 'ticker', label: '$' + target.slice(7),
              size: 0.25, sentiment: event.sentiment, tier: 0,
            })
          } else if (target.startsWith('theme:')) {
            graphNodes = upsertNode(graphNodes, {
              id: target, type: 'theme', label: target.slice(6),
              size: 0.3, sentiment: event.sentiment, tier: 0,
            })
          }

          const edgeBase: GraphEdge = {
            source, target,
            weight: event.weight,
            sentiment: event.sentiment,
            edge_type: (event.edge_type as GraphEdge['edge_type']) ?? 'acct_ticker',
            tweet_id: event.tweet_id ?? null,
          }
          if (exists) {
            return {
              ...prev,
              graphNodes,
              graphEdges: prev.graphEdges.map(e =>
                e.source === source && e.target === target
                  ? { ...e, weight: e.weight + event.weight, sentiment: event.sentiment }
                  : e
              ),
            }
          }
          return { ...prev, graphNodes, graphEdges: [...prev.graphEdges, edgeBase] }
        }

        case 'auth.x.status_changed':
          return { ...prev, xAuth: { state: event.state, logged_in: event.logged_in } }

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
