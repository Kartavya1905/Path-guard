import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow,
  useNodesInitialized, useReactFlow,
  type Edge, type Node, type NodeProps, type OnNodesChange, type XYPosition,
} from '@xyflow/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Outcome, ScenarioGraph, ScenarioNode } from './api'
import { KIND_LABEL, OUTCOME_LABEL, STATUS_LABEL } from './graphUtil'
import { estimateLabel } from './prob'

export type Selection = { kind: 'node' | 'transition' | 'risk'; id: string } | null
export type Highlight = { focus: Set<string>; causes: Set<string>; effects: Set<string>; edges: Set<string> }

type NodeData = {
  node: ScenarioNode
  role: 'focus' | 'cause' | 'effect' | 'dim' | null
  collapsed: boolean
  hiddenCount: number
  branchCount: number
  onToggle: (id: string) => void
}
type MapNode = Node<NodeData, 'scenario'>

// Colour is never the only signal: every edge carries its outcome as text, and line style
// differs too (dashed delay, dotted recovery, thick failure).
const OUTCOME_STYLE: Record<Outcome, { stroke: string; dash?: string; width: number }> = {
  success: { stroke: '#1f7a3f', width: 1.6 },
  failure: { stroke: '#b42318', width: 2.4 },
  delay: { stroke: '#b7791f', dash: '8 4', width: 1.8 },
  recovery: { stroke: '#1d4ed8', dash: '2 4', width: 1.8 },
  residual: { stroke: '#6b7280', dash: '4 4', width: 1.4 },
}

function ScenarioNodeView({ data, selected }: NodeProps<MapNode>) {
  const { node, role, collapsed, hiddenCount, branchCount, onToggle } = data
  const endState = node.terminal && node.kind !== 'goal'
  return (
    <div className={`map-node kind-${node.kind} status-${node.review_status}${role ? ` role-${role}` : ''}${selected ? ' selected' : ''}`}
      title={node.description ?? node.label}>
      <Handle type="target" position={Position.Left} />
      <div className="node-top">
        <span className="node-kind">{KIND_LABEL[node.kind]}{endState ? ' · end' : ''}</span>
        {node.review_status !== 'accepted' && (
          <span className="node-status">{STATUS_LABEL[node.review_status]}</span>
        )}
      </div>
      <div className="node-label">{node.label}</div>
      {(node.severity || branchCount > 0) && (
        <div className="node-foot">
          {node.severity && <span className={`sev sev-${node.severity}`}>{node.severity}</span>}
          {branchCount > 0 && (
            <button type="button" className="collapse nodrag"
              aria-label={collapsed ? `Expand ${hiddenCount} hidden branch nodes` : 'Collapse branches'}
              onClick={(e) => { e.stopPropagation(); onToggle(node.id) }}>
              {collapsed ? `+${hiddenCount} hidden` : 'Collapse'}
            </button>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

const nodeTypes = { scenario: ScenarioNodeView }

/** Fit the view once, after the nodes have been measured (fitView on mount runs too early). */
function FitOnce() {
  const ready = useNodesInitialized()
  const { fitView } = useReactFlow()
  const done = useRef(false)
  useEffect(() => {
    if (ready && !done.current) {
      done.current = true
      void fitView({ padding: 0.15 })
    }
  }, [ready, fitView])
  return null
}

type Props = {
  graph: ScenarioGraph
  shownNodes: Set<string>
  shownEdges: Set<string>
  collapsed: Set<string>
  hiddenCounts: Map<string, number>
  branchCounts: Map<string, number>
  selection: Selection
  highlight: Highlight | null
  canEdit: boolean
  onSelect: (s: Selection) => void
  onMove: (id: string, pos: XYPosition) => void
  onConnect: (source: string, target: string) => void
  onToggleCollapse: (id: string) => void
}

export default function MapCanvas(p: Props) {
  const [dims, setDims] = useState<Record<string, { width: number; height: number }>>({})
  const [dragging, setDragging] = useState<Record<string, XYPosition>>({})

  const nodes: MapNode[] = useMemo(() => p.graph.nodes
    .filter((n) => p.shownNodes.has(n.id))
    .map((n, i) => {
      const role = !p.highlight ? null
        : p.highlight.focus.has(n.id) ? 'focus'
          : p.highlight.causes.has(n.id) ? 'cause'
            : p.highlight.effects.has(n.id) ? 'effect' : 'dim'
      return {
        id: n.id,
        type: 'scenario' as const,
        position: dragging[n.id] ?? n.layout ?? { x: 0, y: 140 * i },
        measured: dims[n.id],
        selected: p.selection?.kind === 'node' && p.selection.id === n.id,
        draggable: p.canEdit,
        data: {
          node: n, role, collapsed: p.collapsed.has(n.id),
          hiddenCount: p.hiddenCounts.get(n.id) ?? 0,
          branchCount: p.branchCounts.get(n.id) ?? 0,
          onToggle: p.onToggleCollapse,
        },
      }
    }), [p.graph.nodes, p.shownNodes, p.highlight, p.selection, p.collapsed, p.hiddenCounts,
    p.branchCounts, p.canEdit, p.onToggleCollapse, dims, dragging])

  const edges: Edge[] = useMemo(() => p.graph.transitions
    .filter((t) => p.shownEdges.has(t.id))
    .map((t) => {
      const s = OUTCOME_STYLE[t.outcome]
      const selected = p.selection?.kind === 'transition' && p.selection.id === t.id
      const dim = p.highlight !== null && !p.highlight.edges.has(t.id)
      const pending = t.review_status === 'suggested' || t.review_status === 'rejected'
      const limit = t.max_traversals ? ` · max ${t.max_traversals}×` : ''
      const status = t.review_status === 'accepted' ? '' : ` (${STATUS_LABEL[t.review_status].toLowerCase()})`
      return {
        id: t.id,
        source: t.source_node_id,
        target: t.target_node_id,
        label: `${OUTCOME_LABEL[t.outcome]} · ${estimateLabel(t.probability)}${limit}${status}`,
        ariaLabel: `${OUTCOME_LABEL[t.outcome]} link: ${t.trigger}`,
        selected,
        className: `edge-${t.outcome}${pending ? ' edge-pending' : ''}`,
        style: {
          stroke: s.stroke,
          strokeWidth: selected ? s.width + 1.5 : s.width,
          strokeDasharray: pending ? '3 5' : s.dash,
          opacity: dim ? 0.15 : pending ? 0.6 : 1,
        },
        labelStyle: { fill: s.stroke, fontWeight: 600, fontSize: 11 },
        labelBgStyle: { fill: '#fff', fillOpacity: dim ? 0.3 : 0.9 },
        markerEnd: { type: MarkerType.ArrowClosed, color: s.stroke },
      }
    }), [p.graph.transitions, p.shownEdges, p.selection, p.highlight])

  const onNodesChange: OnNodesChange<MapNode> = (changes) => {
    const sized = changes.getByType('dimensions').filter((c) => c.dimensions)
    if (sized.length) {
      setDims((d) => ({ ...d, ...Object.fromEntries(sized.map((c) => [c.id, c.dimensions!])) }))
    }
    const moved = changes.getByType('position').filter((c) => c.position && c.dragging)
    if (moved.length) {
      setDragging((d) => ({ ...d, ...Object.fromEntries(moved.map((c) => [c.id, c.position!])) }))
    }
  }

  return (
    <ReactFlow<MapNode>
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      forceColorMode="light"
      minZoom={0.2}
      deleteKeyCode={null}
      nodesConnectable={p.canEdit}
      onNodesChange={onNodesChange}
      onNodeDragStop={(_, node) => {
        p.onMove(node.id, node.position)
        setDragging((d) => { const next = { ...d }; delete next[node.id]; return next })
      }}
      onNodeClick={(_, node) => p.onSelect({ kind: 'node', id: node.id })}
      onEdgeClick={(_, edge) => p.onSelect({ kind: 'transition', id: edge.id })}
      onPaneClick={() => p.onSelect(null)}
      onConnect={(c) => { if (c.source && c.target) p.onConnect(c.source, c.target) }}
    >
      <Background />
      <Controls showInteractive={false} />
      <MiniMap pannable zoomable />
      <FitOnce />
    </ReactFlow>
  )
}
