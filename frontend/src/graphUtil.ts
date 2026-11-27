// Pure helpers for the scenario map view: visibility, collapse, tracing and labels.

import type {
  NodeKind, Outcome, ReviewStatus, RiskCandidate, ScenarioGraph, TargetKind, Transition,
} from './api'

export const OUTCOME_LABEL: Record<Outcome, string> = {
  success: 'Success', failure: 'Failure', delay: 'Delay', recovery: 'Recovery', residual: 'Remainder',
}

export const KIND_LABEL: Record<NodeKind, string> = {
  start: 'Start', step: 'Step', success: 'Success', failure: 'Failure', delay: 'Delay',
  recovery: 'Recovery', goal: 'Goal', residual: 'Remainder',
}

export const STATUS_LABEL: Record<ReviewStatus, string> = {
  suggested: 'Suggested', accepted: 'Accepted', edited: 'Edited', rejected: 'Rejected',
}

export const isModeled = (s: ReviewStatus) => s === 'accepted' || s === 'edited'

/** Transitions that are shown: everything except rejected (unless asked) with both ends shown. */
export function shownTransitions(g: ScenarioGraph, shownNodes: Set<string>, showRejected: boolean) {
  return g.transitions.filter((t) =>
    (showRejected || t.review_status !== 'rejected')
    && shownNodes.has(t.source_node_id) && shownNodes.has(t.target_node_id))
}

function outgoing(transitions: Transition[]) {
  const out = new Map<string, Transition[]>()
  for (const t of transitions) out.set(t.source_node_id, [...(out.get(t.source_node_id) ?? []), t])
  return out
}

/** Nodes on the main path: reachable from the start through success links only. */
export function mainPath(g: ScenarioGraph): Set<string> {
  const out = outgoing(g.transitions.filter((t) => t.outcome === 'success' && t.review_status !== 'rejected'))
  const starts = g.nodes.filter((n) => n.kind === 'start').map((n) => n.id)
  const seen = new Set(starts)
  const stack = [...starts]
  while (stack.length) {
    for (const t of out.get(stack.pop() as string) ?? []) {
      if (!seen.has(t.target_node_id)) { seen.add(t.target_node_id); stack.push(t.target_node_id) }
    }
  }
  return seen
}

/**
 * Collapsing a node hides the side branches that hang off it: nodes reached through its
 * non-success links, up to where they rejoin the main path. Returns hidden IDs and a count
 * per collapsed node.
 */
export function collapsedHidden(g: ScenarioGraph, collapsed: Set<string>) {
  const main = mainPath(g)
  const out = outgoing(g.transitions.filter((t) => t.review_status !== 'rejected'))
  const hidden = new Set<string>()
  const counts = new Map<string, number>()
  for (const c of collapsed) {
    const seen = new Set<string>()
    const stack = (out.get(c) ?? []).filter((t) => t.outcome !== 'success').map((t) => t.target_node_id)
    while (stack.length) {
      const id = stack.pop() as string
      if (id === c || main.has(id) || seen.has(id)) continue
      seen.add(id)
      for (const t of out.get(id) ?? []) stack.push(t.target_node_id)
    }
    seen.forEach((id) => hidden.add(id))
    counts.set(c, seen.size)
  }
  return { hidden, counts }
}

/** Upstream causes and downstream effects of a set of nodes. */
export function trace(transitions: Transition[], causesOf: string[], effectsOf: string[]) {
  const fwd = outgoing(transitions)
  const back = new Map<string, Transition[]>()
  for (const t of transitions) back.set(t.target_node_id, [...(back.get(t.target_node_id) ?? []), t])
  const walk = (from: string[], next: Map<string, Transition[]>, end: 'target_node_id' | 'source_node_id') => {
    const nodes = new Set<string>()
    const edges = new Set<string>()
    const stack = [...from]
    while (stack.length) {
      for (const t of next.get(stack.pop() as string) ?? []) {
        edges.add(t.id)
        if (!nodes.has(t[end])) { nodes.add(t[end]); stack.push(t[end]) }
      }
    }
    return { nodes, edges }
  }
  return {
    upstream: walk(causesOf, back, 'source_node_id'),
    downstream: walk(effectsOf, fwd, 'target_node_id'),
  }
}

/** For a risk: causes lead into its affected step; effects follow its branch (or the step). */
export function riskTrace(risk: RiskCandidate, transitions: Transition[]) {
  const [step, ...branch] = risk.affected_node_ids
  if (!step) return null
  return trace(transitions, [step], branch.length ? branch : [step])
}

export function targetLabel(g: ScenarioGraph, kind: TargetKind, id: string): string {
  if (kind === 'node') return g.nodes.find((n) => n.id === id)?.label ?? 'Missing node'
  if (kind === 'transition') return g.transitions.find((t) => t.id === id)?.trigger ?? 'Missing link'
  if (kind === 'risk') return g.risks.find((r) => r.id === id)?.title ?? 'Missing risk'
  return id
}
