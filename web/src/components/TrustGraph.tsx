import * as d3 from 'd3'
import { useEffect, useRef } from 'react'
import type { GraphEdge, GraphNode } from '../types'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  onTickerClick: (ticker: string) => void
}

const TIER_COLORS = ['', '#60a5fa', '#34d399', '#9ca3af']  // tier 1,2,3
const BUY_COLOR = '#22c55e'
const SELL_COLOR = '#ef4444'
const NEUTRAL_COLOR = '#6b7280'

function sentimentColor(s: string) {
  return s === 'positive' ? BUY_COLOR : s === 'negative' ? SELL_COLOR : NEUTRAL_COLOR
}

export function TrustGraph({ nodes, edges, onTickerClick }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    if (!svgRef.current) return
    const svg = d3.select(svgRef.current)
    const { width, height } = svgRef.current.getBoundingClientRect()
    svg.selectAll('*').remove()

    if (nodes.length === 0) {
      svg.append('text')
        .attr('x', width / 2).attr('y', height / 2)
        .attr('text-anchor', 'middle').attr('fill', '#374151')
        .attr('font-size', 12)
        .text('Waiting for trust graph data…')
      return
    }

    const g = svg.append('g')

    // Zoom + pan
    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.3, 4])
        .on('zoom', e => g.attr('transform', e.transform))
    )

    const nodeMap = new Map(nodes.map(n => [n.id, n]))

    // Filter edges to only those with valid source+target
    const validEdges = edges.filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))

    const sim = d3.forceSimulation(nodes as any)
      .force('link', d3.forceLink(validEdges.map(e => ({ ...e, source: e.source, target: e.target })))
        .id((d: any) => d.id)
        .distance(80)
        .strength(0.4))
      .force('charge', d3.forceManyBody().strength(-120))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d: any) => nodeRadius(d) + 6))

    // Edges
    const link = g.append('g').selectAll('line')
      .data(validEdges)
      .join('line')
      .attr('stroke', d => sentimentColor(d.sentiment))
      .attr('stroke-opacity', 0.35)
      .attr('stroke-width', d => Math.max(0.5, d.weight * 3))

    const dragBehavior = d3.drag<SVGGElement, GraphNode>()
      .on('start', (e, d: any) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (e, d: any) => { d.fx = e.x; d.fy = e.y })
      .on('end', (e, d: any) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null })

    // Nodes
    const node = g.append('g').selectAll('g')
      .data(nodes)
      .join('g')
      .attr('cursor', d => d.type === 'ticker' ? 'pointer' : 'default')
      .on('click', (_, d) => {
        if (d.type === 'ticker') onTickerClick(d.label.replace('$', ''))
      })
      .call(dragBehavior as any)

    node.append('circle')
      .attr('r', nodeRadius)
      .attr('fill', d => {
        if (d.type === 'account') return TIER_COLORS[d.tier] ?? '#9ca3af'
        return d.sentiment === 'positive' ? BUY_COLOR : d.sentiment === 'negative' ? SELL_COLOR : NEUTRAL_COLOR
      })
      .attr('fill-opacity', 0.85)
      .attr('stroke', '#111827')
      .attr('stroke-width', 1.5)

    node.append('text')
      .text(d => d.label)
      .attr('text-anchor', 'middle')
      .attr('dy', d => nodeRadius(d) + 11)
      .attr('font-size', d => d.type === 'ticker' ? 11 : 9)
      .attr('fill', d => d.type === 'ticker' ? '#e5e7eb' : '#9ca3af')

    // Tooltip on hover
    const tooltip = d3.select('body').append('div')
      .attr('class', 'fixed z-50 pointer-events-none bg-gray-800 text-xs text-gray-100 px-2 py-1 rounded shadow-lg opacity-0 transition-opacity')

    node
      .on('mouseover', (e, d) => {
        tooltip.style('opacity', '1')
          .html(`${d.label}<br/>type: ${d.type}${d.type === 'account' ? ` | tier ${d.tier}` : ''}<br/>size: ${d.size.toFixed(3)}`)
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
  }, [nodes, edges])

  return (
    <div className="panel flex flex-col h-full">
      <p className="panel-title">Trust Graph</p>
      <div className="flex-1 min-h-0 relative">
        <svg ref={svgRef} className="w-full h-full" />
        <div className="absolute bottom-2 right-2 flex gap-3 text-xs text-gray-500">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-400 inline-block" />T1 account</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-400 inline-block" />T2</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-green-500 inline-block" />bullish ticker</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500 inline-block" />bearish ticker</span>
        </div>
      </div>
    </div>
  )
}

function nodeRadius(d: GraphNode) {
  if (d.type === 'account') return 4 + d.size * 10
  return 6 + d.size * 18
}
