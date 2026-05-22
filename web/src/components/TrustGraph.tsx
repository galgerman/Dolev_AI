import * as d3 from 'd3'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { GraphEdge, GraphNode } from '../types'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  onTickerClick: (ticker: string) => void
}

const TIER_COLORS = ['', '#60a5fa', '#34d399', '#9ca3af']
const BUY_COLOR = '#22c55e'
const SELL_COLOR = '#ef4444'
const NEUTRAL_COLOR = '#6b7280'
const THEME_COLOR = '#a78bfa'  // purple for theme nodes

function sentimentColor(s: string) {
  return s === 'positive' ? BUY_COLOR : s === 'negative' ? SELL_COLOR : NEUTRAL_COLOR
}

function nodeRadius(d: GraphNode) {
  if (d.type === 'account') return 4 + d.size * 10
  if (d.type === 'theme') return 6 + d.size * 16
  return 6 + d.size * 18
}

function edgeDashArray(t?: string): string {
  if (t === 'acct_theme') return '4 3'
  if (t === 'theme_ticker') return '2 4'
  return ''
}

export function TrustGraph({ nodes, edges, onTickerClick }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [showThemes, setShowThemes] = useState(true)
  const [showNeutral, setShowNeutral] = useState(true)

  useEffect(() => {
    if (!svgRef.current) return
    const svg = d3.select(svgRef.current)
    const { width, height } = svgRef.current.getBoundingClientRect()
    svg.selectAll('*').remove()

    const filteredNodes = nodes.filter(n => showThemes || n.type !== 'theme')
    const filteredEdges = edges.filter(e => {
      if (!showThemes && (e.edge_type === 'acct_theme' || e.edge_type === 'theme_ticker')) return false
      if (!showNeutral && e.sentiment === 'neutral') return false
      return true
    })

    if (filteredNodes.length === 0) {
      svg.append('text')
        .attr('x', width / 2).attr('y', height / 2)
        .attr('text-anchor', 'middle').attr('fill', '#374151')
        .attr('font-size', 12)
        .text('Waiting for trust graph data…')
      return
    }

    const g = svg.append('g')

    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.3, 4])
        .on('zoom', e => g.attr('transform', e.transform))
    )

    const nodeMap = new Map(filteredNodes.map(n => [n.id, n]))
    const validEdges = filteredEdges.filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))

    const sim = d3.forceSimulation(filteredNodes as any)
      .force('link', d3.forceLink(validEdges.map(e => ({ ...e, source: e.source, target: e.target })))
        .id((d: any) => d.id)
        .distance((e: any) => e.edge_type === 'theme_ticker' ? 90 : 80)
        .strength(0.4))
      .force('charge', d3.forceManyBody().strength(-140))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d: any) => nodeRadius(d) + 20))

    // Edges
    const link = g.append('g').selectAll('line')
      .data(validEdges)
      .join('line')
      .attr('stroke', d => sentimentColor(d.sentiment))
      .attr('stroke-opacity', d => d.edge_type === 'theme_ticker' ? 0.25 : 0.4)
      .attr('stroke-width', d => Math.max(0.5, d.weight * 3))
      .attr('stroke-dasharray', d => edgeDashArray(d.edge_type))
      .attr('cursor', d => d.tweet_id || d.id ? 'pointer' : 'default')

    const dragBehavior = d3.drag<SVGGElement, GraphNode>()
      .on('start', (e, d: any) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (e, d: any) => { d.fx = e.x; d.fy = e.y })
      .on('end', (e, d: any) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null })

    const node = g.append('g').selectAll('g')
      .data(filteredNodes)
      .join('g')
      .attr('cursor', d => d.type === 'ticker' ? 'pointer' : 'default')
      .on('click', (_, d) => {
        if (d.type === 'ticker') onTickerClick(d.label.replace('$', ''))
      })
      .call(dragBehavior as any)

    // Theme nodes are rounded rectangles, tickers + accounts are circles
    node.each(function (d) {
      const sel = d3.select(this)
      if (d.type === 'theme') {
        const r = nodeRadius(d)
        sel.append('rect')
          .attr('x', -r).attr('y', -r * 0.6)
          .attr('width', r * 2).attr('height', r * 1.2)
          .attr('rx', 4).attr('ry', 4)
          .attr('fill', d.sentiment === 'positive' ? BUY_COLOR : d.sentiment === 'negative' ? SELL_COLOR : THEME_COLOR)
          .attr('fill-opacity', 0.7)
          .attr('stroke', '#1f2937').attr('stroke-width', 1.5)
      } else {
        sel.append('circle')
          .attr('r', nodeRadius(d))
          .attr('fill', () => {
            if (d.type === 'account') return TIER_COLORS[d.tier] ?? '#9ca3af'
            return d.sentiment === 'positive' ? BUY_COLOR : d.sentiment === 'negative' ? SELL_COLOR : NEUTRAL_COLOR
          })
          .attr('fill-opacity', 0.85)
          .attr('stroke', '#111827').attr('stroke-width', 1.5)
      }
    })

    node.append('text')
      .text(d => d.label)
      .attr('text-anchor', 'middle')
      .attr('dy', d => (d.type === 'theme' ? nodeRadius(d) * 0.8 : nodeRadius(d) + 11))
      .attr('font-size', d => d.type === 'ticker' ? 11 : 9)
      .attr('font-weight', d => d.type === 'ticker' || d.type === 'theme' ? '600' : '400')
      .attr('fill', d => d.type === 'ticker' ? '#e5e7eb' : d.type === 'theme' ? '#ddd6fe' : '#9ca3af')
      .attr('stroke', '#111827')
      .attr('stroke-width', 3)
      .attr('paint-order', 'stroke')
      .attr('stroke-linejoin', 'round')

    const tooltip = d3.select('body').append('div')
      .attr('class', 'fixed z-50 pointer-events-none bg-gray-800 text-xs text-gray-100 px-2 py-1 rounded shadow-lg opacity-0 transition-opacity max-w-md')

    node
      .on('mouseover', (e, d) => {
        let desc = ''
        if (d.type === 'account') desc = `Tier ${d.tier} source — credibility ${d.size.toFixed(2)}`
        else if (d.type === 'ticker') desc = `${d.sentiment} momentum — activity ${(d.size * 100).toFixed(0)}%`
        else desc = `theme — ${d.sentiment}, activity ${(d.size * 100).toFixed(0)}%`
        tooltip.style('opacity', '1')
          .html(`<strong>${d.label}</strong><br/>${desc}`)
      })
      .on('mousemove', e => {
        tooltip.style('left', (e.clientX + 12) + 'px').style('top', (e.clientY - 8) + 'px')
      })
      .on('mouseout', () => tooltip.style('opacity', '0'))

    // Edge hover: contextual description per edge type
    link
      .on('mouseover', async (e, d: any) => {
        const src = d.source.id ?? d.source
        const tgt = d.target.id ?? d.target
        let hint = ''
        if (d.edge_type === 'theme_ticker') hint = '<br/><span class="text-gray-400">inherited via themes.yaml mapping</span>'
        else if (d.edge_type === 'acct_theme') hint = '<br/><span class="text-gray-400">theme mention in tweet</span>'
        else if (d.id) hint = '<br/><span class="text-gray-400">loading post…</span>'
        tooltip.style('opacity', '1')
          .html(`<em>${src} → ${tgt}</em><br/>weight: ${d.weight.toFixed(3)} · ${d.sentiment}${hint}`)
        if (d.id && d.edge_type !== 'theme_ticker') {
          try {
            const detail = await api.edgeDetail(d.id)
            if (detail.tweet) {
              tooltip.html(
                `<em>${src} → ${tgt}</em><br/>` +
                `weight: ${d.weight.toFixed(3)} · ${d.sentiment}<br/>` +
                `<span class="text-blue-300">@${detail.tweet.author}</span>: ${detail.tweet.text.slice(0, 180)}`
              )
            }
          } catch { /* ignore */ }
        }
      })
      .on('mousemove', e => {
        tooltip.style('left', (e.clientX + 12) + 'px').style('top', (e.clientY - 8) + 'px')
      })
      .on('mouseout', () => tooltip.style('opacity', '0'))

    sim.on('tick', () => {
      link
        .attr('x1', (d: any) => d.source.x)
        .attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x)
        .attr('y2', (d: any) => d.target.y)
      node.attr('transform', (d: any) => `translate(${d.x},${d.y})`)
    })

    return () => {
      sim.stop()
      tooltip.remove()
    }
  }, [nodes, edges, showThemes, showNeutral, onTickerClick])

  return (
    <div className="panel flex flex-col h-full">
      <div className="flex items-center justify-between mb-0.5">
        <p className="panel-title">Trust Graph</p>
        <div className="flex items-center gap-2 text-[10px] text-gray-500">
          <label className="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" checked={showThemes} onChange={e => setShowThemes(e.target.checked)} className="accent-purple-500" />
            themes
          </label>
          <label className="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" checked={!showNeutral} onChange={e => setShowNeutral(!e.target.checked)} className="accent-gray-500" />
            hide neutral
          </label>
        </div>
      </div>
      <p className="text-[10px] text-gray-600 mb-1">
        Accounts → themes → tickers. Stronger lines = higher conviction. Solid = direct mention, dashed = via theme.
      </p>
      <div className="flex-1 min-h-0 relative">
        <svg ref={svgRef} className="w-full h-full" />
        <div className="absolute bottom-2 right-2 flex gap-3 text-xs text-gray-500">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-400 inline-block" />T1</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-400 inline-block" />T2</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500 inline-block" />bull</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500 inline-block" />bear</span>
          <span className="flex items-center gap-1"><span className="w-2 h-1.5 rounded-sm bg-purple-400 inline-block" />theme</span>
        </div>
      </div>
    </div>
  )
}
