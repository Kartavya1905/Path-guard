import { useState, type FormEvent, type ReactNode } from 'react'
import {
  newMapId, type Evidence, type Me, type NodeKind, type Outcome, type RiskCandidate,
  type ScenarioGraph, type ScenarioNode, type Severity, type TargetKind, type Transition,
} from './api'
import EstimateEditor from './EstimateEditor'
import { KIND_LABEL, OUTCOME_LABEL, STATUS_LABEL, isModeled } from './graphUtil'
import { pctExact, sumFractions } from './prob'
import type { Selection } from './MapCanvas'

type Update = (fn: (g: ScenarioGraph) => ScenarioGraph) => void

type Props = {
  graph: ScenarioGraph
  selection: Selection
  me: Me | null
  canEdit: boolean
  canReview: boolean
  busy: boolean
  update: Update
  onSelect: (s: Selection) => void
  onReview: (riskId: string, decision: 'accept' | 'reject', modelBranch: boolean) => void
}

const SEVERITIES: Severity[] = ['low', 'medium', 'high', 'critical']
const KINDS: NodeKind[] = ['step', 'success', 'failure', 'delay', 'recovery', 'goal', 'residual', 'start']
const OUTCOMES: Outcome[] = ['success', 'failure', 'delay', 'recovery', 'residual']

export default function Inspector(p: Props) {
  const { graph, selection } = p
  if (!selection) {
    return (
      <div className="inspector-empty muted">
        <p>Select a step, a link or a risk to see its details.</p>
        <p className="small">Drag from a node's right edge to another node to add a link.</p>
      </div>
    )
  }
  if (selection.kind === 'node') {
    const node = graph.nodes.find((n) => n.id === selection.id)
    return node ? <NodePanel {...p} node={node} /> : <Gone />
  }
  if (selection.kind === 'transition') {
    const t = graph.transitions.find((x) => x.id === selection.id)
    return t ? <LinkPanel {...p} t={t} /> : <Gone />
  }
  const risk = graph.risks.find((r) => r.id === selection.id)
  return risk ? <RiskPanel {...p} risk={risk} /> : <Gone />
}

const Gone = () => <p className="muted">This item no longer exists.</p>

function patchNode(update: Update, id: string, patch: Partial<ScenarioNode>) {
  update((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)) }))
}
function patchLink(update: Update, id: string, patch: Partial<Transition>) {
  update((g) => ({ ...g, transitions: g.transitions.map((t) => (t.id === id ? { ...t, ...patch } : t)) }))
}

function ReviewState(props: {
  status: ScenarioNode['review_status']; origin: string; canDecide: boolean
  onDecide: (s: 'accepted' | 'rejected') => void
}) {
  return (
    <div className="review-state">
      <span className={`tag status-${props.status}`}>{STATUS_LABEL[props.status]}</span>
      <span className="muted small">{props.origin === 'rule' ? 'Suggested by a rule' : props.origin === 'ai' ? 'AI suggestion' : 'Added by a person'}</span>
      {props.canDecide && props.status !== 'accepted' && props.status !== 'edited' && (
        <button type="button" onClick={() => props.onDecide('accepted')}>Accept</button>
      )}
      {props.canDecide && props.status !== 'rejected' && (
        <button type="button" className="danger" onClick={() => props.onDecide('rejected')}>Reject</button>
      )}
    </div>
  )
}

function NodePanel(p: Props & { node: ScenarioNode }) {
  const { node, graph, update, canEdit } = p
  const incoming = graph.transitions.filter((t) => t.target_node_id === node.id)
  const outgoingLinks = graph.transitions.filter((t) => t.source_node_id === node.id)
  const risks = graph.risks.filter((r) => r.affected_node_ids.includes(node.id))
  const nodeLabel = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? 'Missing node'

  const remove = () => {
    if (!window.confirm(`Delete “${node.label}” and its links?`)) return
    update((g) => ({
      ...g,
      nodes: g.nodes.filter((n) => n.id !== node.id),
      transitions: g.transitions.filter((t) => t.source_node_id !== node.id && t.target_node_id !== node.id),
      decision_groups: g.decision_groups.filter((d) => d.node_id !== node.id),
    }))
    p.onSelect(null)
  }

  return (
    <div className="inspector">
      <p className="eyebrow">{KIND_LABEL[node.kind]}{node.terminal ? ' · end state' : ''}</p>
      <fieldset disabled={!canEdit} className="plain">
        <Labeled id="node-label" label="Name">
          <input id="node-label" value={node.label} onChange={(e) => patchNode(update, node.id, { label: e.target.value })} />
        </Labeled>
        <div className="grid2">
          <Labeled id="node-kind" label="Type">
            <select id="node-kind" value={node.kind} onChange={(e) => {
              const kind = e.target.value as NodeKind
              patchNode(update, node.id, {
                kind, terminal: kind === 'goal' || kind === 'residual' ? true : kind === 'start' ? false : node.terminal,
              })
            }}>
              {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </select>
          </Labeled>
          <Labeled id="node-severity" label="Severity">
            <select id="node-severity" value={node.severity ?? ''}
              onChange={(e) => patchNode(update, node.id, { severity: (e.target.value || null) as Severity | null })}>
              <option value="">Not set</option>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Labeled>
        </div>
        <label className="check">
          <input type="checkbox" checked={node.terminal}
            disabled={node.kind === 'goal' || node.kind === 'residual' || node.kind === 'start'}
            onChange={(e) => patchNode(update, node.id, { terminal: e.target.checked })} />
          End state (no way forward)
        </label>
        <Labeled id="node-desc" label="Description">
          <textarea id="node-desc" rows={2} value={node.description ?? ''}
            onChange={(e) => patchNode(update, node.id, { description: e.target.value || null })} />
        </Labeled>
      </fieldset>
      <ReviewState status={node.review_status} origin={node.origin} canDecide={canEdit}
        onDecide={(s) => patchNode(update, node.id, { review_status: s })} />

      <Section title="Links">
        <ul className="plain-list">
          {incoming.map((t) => <li key={t.id}><button type="button" className="link" onClick={() => p.onSelect({ kind: 'transition', id: t.id })}>
            ← {OUTCOME_LABEL[t.outcome]} from {nodeLabel(t.source_node_id)}</button></li>)}
          {outgoingLinks.map((t) => <li key={t.id}><button type="button" className="link" onClick={() => p.onSelect({ kind: 'transition', id: t.id })}>
            → {OUTCOME_LABEL[t.outcome]} to {nodeLabel(t.target_node_id)}</button></li>)}
          {incoming.length + outgoingLinks.length === 0 && <li className="muted">No links.</li>}
        </ul>
      </Section>
      {risks.length > 0 && (
        <Section title="Risks affecting this step">
          <ul className="plain-list">
            {risks.map((r) => <li key={r.id}><button type="button" className="link" onClick={() => p.onSelect({ kind: 'risk', id: r.id })}>{r.title}</button></li>)}
          </ul>
        </Section>
      )}
      <Related {...p} kind="node" id={node.id} />
      {canEdit && node.origin === 'user' && (
        <button type="button" className="danger" onClick={remove}>Delete step</button>
      )}
    </div>
  )
}

function LinkPanel(p: Props & { t: Transition }) {
  const { t, graph, update, canEdit } = p
  const groups = graph.decision_groups.filter((d) => d.node_id === t.source_node_id)
  const nodeLabel = (id: string) => graph.nodes.find((n) => n.id === id)?.label ?? 'Missing node'
  const group = graph.decision_groups.find((d) => d.id === t.decision_group_id)
  const siblings = group ? graph.transitions.filter((x) => x.decision_group_id === group.id && isModeled(x.review_status)) : []
  const unknownSiblings = siblings.filter((x) => !x.probability || x.probability.kind === 'unknown').length
  const rangeSiblings = siblings.filter((x) => x.probability?.kind === 'range').length
  const points = siblings.filter((x) => x.probability?.kind === 'point').map((x) => String(x.probability?.value))
  const pointTotal = points.length ? sumFractions(points) : null

  const addGroup = () => {
    const id = newMapId('dgp')
    update((g) => ({
      ...g,
      decision_groups: [...g.decision_groups, {
        id, node_id: t.source_node_id, label: `Outcome of: ${nodeLabel(t.source_node_id)}`,
        exclusive: true, complete: false, origin: 'user', review_status: 'accepted',
      }],
      transitions: g.transitions.map((x) => x.source_node_id === t.source_node_id ? { ...x, decision_group_id: id } : x),
    }))
  }
  const remove = () => {
    update((g) => ({ ...g, transitions: g.transitions.filter((x) => x.id !== t.id) }))
    p.onSelect(null)
  }

  return (
    <div className="inspector">
      <p className="eyebrow">{OUTCOME_LABEL[t.outcome]} link</p>
      <p className="small">
        <button type="button" className="link" onClick={() => p.onSelect({ kind: 'node', id: t.source_node_id })}>{nodeLabel(t.source_node_id)}</button>
        {' → '}
        <button type="button" className="link" onClick={() => p.onSelect({ kind: 'node', id: t.target_node_id })}>{nodeLabel(t.target_node_id)}</button>
      </p>
      <fieldset disabled={!canEdit} className="plain">
        <Labeled id="link-trigger" label="Trigger">
          <input id="link-trigger" value={t.trigger} onChange={(e) => patchLink(update, t.id, { trigger: e.target.value })} />
        </Labeled>
        <div className="grid2">
          <Labeled id="link-outcome" label="Outcome">
            <select id="link-outcome" value={t.outcome} onChange={(e) => patchLink(update, t.id, { outcome: e.target.value as Outcome })}>
              {OUTCOMES.map((o) => <option key={o} value={o}>{OUTCOME_LABEL[o]}</option>)}
            </select>
          </Labeled>
          <Labeled id="link-max" label="Max attempts" hint="Limits retries and rework loops.">
            <input id="link-max" type="number" min={1} value={t.max_traversals ?? ''}
              onChange={(e) => patchLink(update, t.id, { max_traversals: e.target.value ? Math.max(1, Math.floor(Number(e.target.value))) : null })} />
          </Labeled>
        </div>
        <Labeled id="link-condition" label="Condition">
          <textarea id="link-condition" rows={2} value={t.condition ?? ''}
            onChange={(e) => patchLink(update, t.id, { condition: e.target.value || null })} />
        </Labeled>
        <Labeled id="link-group" label="Decision group" hint="Branches leaving the same step must share one group.">
          <select id="link-group" value={t.decision_group_id ?? ''}
            onChange={(e) => patchLink(update, t.id, { decision_group_id: e.target.value || null })}>
            <option value="">None</option>
            {groups.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
          </select>
        </Labeled>
        {groups.length === 0 && (
          <button type="button" onClick={addGroup}>Group all links leaving this step</button>
        )}
      </fieldset>
      <Section title="Probability">
        <p className="muted small">Chance of this outcome once the run reaches “{nodeLabel(t.source_node_id)}”.</p>
        <EstimateEditor key={`${t.id}:${JSON.stringify(t.probability ?? null)}`} estimate={t.probability}
          disabled={!canEdit} onApply={(probability) => patchLink(update, t.id, { probability })} />
        {group && (
          <div className="group-box">
            <p className="small"><strong>{group.label}</strong>: {siblings.length} branch(es){
              unknownSiblings > 0 ? `, ${unknownSiblings} without an estimate` : ''}{
              pointTotal !== null ? `; exact estimates total ${pctExact(pointTotal)}` : ''}.</p>
            <label className="check">
              <input type="checkbox" checked={group.complete} disabled={!canEdit}
                onChange={(e) => update((g) => ({ ...g, decision_groups: g.decision_groups.map((d) => d.id === group.id ? { ...d, complete: e.target.checked } : d) }))} />
              Every possible outcome here is modeled (complete)
            </label>
            {group.complete && pointTotal !== null && unknownSiblings === 0 && rangeSiblings === 0 && pointTotal !== '1' && (
              <p className="field-error small">A complete decision must total exactly 100%.</p>
            )}
          </div>
        )}
      </Section>
      <ReviewState status={t.review_status} origin={t.origin} canDecide={canEdit}
        onDecide={(s) => patchLink(update, t.id, { review_status: s })} />
      <Related {...p} kind="transition" id={t.id} />
      {canEdit && t.origin === 'user' && <button type="button" className="danger" onClick={remove}>Delete link</button>}
    </div>
  )
}

function RiskPanel(p: Props & { risk: RiskCandidate }) {
  const { risk, graph, canReview, busy } = p
  const hasBranch = risk.affected_node_ids.length > 1
  const branchModeled = graph.transitions.some((t) => t.rule_key && t.rule_key === risk.dedupe_key && isModeled(t.review_status))
  return (
    <div className="inspector">
      <p className="eyebrow">Risk · {risk.category.replaceAll('_', ' ')}</p>
      <h3 className="inspector-title">{risk.title}</h3>
      <dl className="facts">
        <dt>Trigger</dt><dd>{risk.trigger}</dd>
        <dt>Why</dt><dd>{risk.rationale || '—'}</dd>
        <dt>Consequence</dt><dd>{risk.consequence || '—'}</dd>
        {risk.mitigation_hint && <><dt>Suggested mitigation</dt><dd>{risk.mitigation_hint}</dd></>}
        <dt>Impact</dt><dd>{risk.impact ? <span className={`sev sev-${risk.impact}`}>{risk.impact}</span> : 'Not set'}</dd>
        <dt>Likelihood</dt><dd>{risk.likelihood?.kind === 'unknown' || !risk.likelihood ? 'Unknown (no estimate)' : risk.likelihood.kind}</dd>
        <dt>Affected</dt>
        <dd>
          {risk.affected_node_ids.map((id) => (
            <button key={id} type="button" className="link" onClick={() => p.onSelect({ kind: 'node', id })}>
              {graph.nodes.find((n) => n.id === id)?.label ?? 'Missing node'}
            </button>
          ))}
        </dd>
      </dl>
      <p className="small muted">Highlighted on the map: <span className="key key-cause">causes</span> lead into the affected step, <span className="key key-effect">effects</span> follow from it.</p>
      <div className="review-state">
        <span className={`tag status-${risk.review_status}`}>{STATUS_LABEL[risk.review_status]}</span>
        {hasBranch && <span className="muted small">{branchModeled ? 'Branch is on the map' : 'Branch not modeled'}</span>}
      </div>
      {canReview && (
        <div className="row-actions start">
          {(risk.review_status === 'suggested' || risk.review_status === 'rejected') && (
            <button type="button" disabled={busy} onClick={() => p.onReview(risk.id, 'accept', false)}>Accept</button>
          )}
          {hasBranch && !branchModeled && (
            <button type="button" className="primary" disabled={busy} onClick={() => p.onReview(risk.id, 'accept', true)}>Accept and add branch</button>
          )}
          {risk.review_status !== 'rejected' && (
            <button type="button" className="danger" disabled={busy} onClick={() => p.onReview(risk.id, 'reject', false)}>Reject</button>
          )}
        </div>
      )}
      <Related {...p} kind="risk" id={risk.id} />
    </div>
  )
}

// --------------------------------------------------------------------------- related items

function Related(p: Props & { kind: TargetKind; id: string }) {
  const { graph, kind, id, update, canEdit, canReview, me } = p
  const assumptions = graph.assumptions.filter((a) => a.target_kind === kind && a.target_id === id)
  const links = graph.evidence_links.filter((l) => l.target_kind === kind && l.target_id === id)
  const notes = graph.notes.filter((n) => n.target_kind === kind && n.target_id === id)
  const mitigations = graph.mitigations.filter((m) => m.target_kind === kind && m.target_id === id)

  return (
    <>
      <Section title={`Assumptions (${assumptions.length})`}>
        <ul className="plain-list">
          {assumptions.map((a) => <li key={a.id}>{a.text} <span className="muted small">· {a.status}</span></li>)}
        </ul>
        {canEdit && <OneLineForm label="New assumption" onAdd={(text) => update((g) => ({
          ...g, assumptions: [...g.assumptions, { id: newMapId('asm'), text, target_kind: kind, target_id: id, status: 'open' }],
        }))} />}
      </Section>

      <Section title={`Evidence (${links.length})`}>
        <ul className="plain-list">
          {links.map((l) => {
            const e = graph.evidence.find((x) => x.id === l.evidence_id)
            return <li key={l.id}>{e ? <>{e.title} <span className="muted small">· {e.kind.replace('_', ' ')} · {e.quality}</span></> : 'Missing evidence'}</li>
          })}
        </ul>
        {canEdit && <EvidenceForm onAdd={(e) => update((g) => ({
          ...g,
          evidence: [...g.evidence, e],
          evidence_links: [...g.evidence_links, { id: newMapId('evl'), evidence_id: e.id, target_kind: kind, target_id: id }],
        }))} />}
      </Section>

      <Section title={`Mitigations (${mitigations.length})`}>
        <ul className="plain-list">
          {mitigations.map((m) => (
            <li key={m.id}><strong>{m.title}</strong>{m.owner && <span className="muted small"> · owner {m.owner}</span>}
              <div className="small">{m.expected_effect}</div></li>
          ))}
        </ul>
        {canEdit && <MitigationForm onAdd={(m) => update((g) => ({
          ...g, mitigations: [...g.mitigations, {
            id: newMapId('mit'), target_kind: kind, target_id: id, assumptions: [], estimate_changes: [], ...m,
          }],
        }))} />}
      </Section>

      <Section title={`Review notes (${notes.length})`}>
        <ul className="plain-list">
          {notes.map((n) => (
            <li key={n.id}>{n.body}
              <div className="muted small">{n.author_id === me?.id ? 'You' : n.author_id} · {new Date(n.created_at).toLocaleString()}</div></li>
          ))}
        </ul>
        {(canEdit || canReview) && me && <OneLineForm label="New review note" onAdd={(body) => update((g) => ({
          ...g, notes: [...g.notes, {
            id: newMapId('not'), target_kind: kind, target_id: id, author_id: me.id, body,
            created_at: new Date().toISOString(),
          }],
        }))} />}
      </Section>
    </>
  )
}

function Section(props: { title: string; children: ReactNode }) {
  return <section className="insp-section"><h4>{props.title}</h4>{props.children}</section>
}

function Labeled(props: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={props.id}>{props.label}</label>
      {props.children}
      {props.hint && <span className="muted small">{props.hint}</span>}
    </div>
  )
}

function OneLineForm(props: { label: string; onAdd: (text: string) => void }) {
  const [text, setText] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (text.trim()) { props.onAdd(text.trim()); setText('') }
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <input aria-label={props.label} placeholder={props.label} value={text} onChange={(e) => setText(e.target.value)} />
      <button type="submit" disabled={!text.trim()}>Add</button>
    </form>
  )
}

function EvidenceForm(props: { onAdd: (e: Evidence) => void }) {
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState<Evidence['kind']>('document')
  const [quality, setQuality] = useState<Evidence['quality']>('moderate')
  const [source, setSource] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    props.onAdd({ id: newMapId('evd'), title: title.trim(), kind, quality, source_ref: source.trim() || null })
    setTitle(''); setSource('')
  }
  return (
    <form className="stack-form" onSubmit={submit}>
      <input aria-label="Evidence title" placeholder="Evidence title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <div className="grid2">
        <select aria-label="Evidence type" value={kind} onChange={(e) => setKind(e.target.value as Evidence['kind'])}>
          {['dataset', 'incident', 'document', 'interview', 'test_result', 'other'].map((k) => <option key={k} value={k}>{k.replace('_', ' ')}</option>)}
        </select>
        <select aria-label="Evidence quality" value={quality} onChange={(e) => setQuality(e.target.value as Evidence['quality'])}>
          {['strong', 'moderate', 'weak', 'anecdotal'].map((q) => <option key={q} value={q}>{q}</option>)}
        </select>
      </div>
      <input aria-label="Evidence source" placeholder="Source or link (optional)" value={source} onChange={(e) => setSource(e.target.value)} />
      <button type="submit" disabled={!title.trim()}>Add evidence</button>
    </form>
  )
}

function MitigationForm(props: { onAdd: (m: { title: string; rationale: string; expected_effect: string; owner: string | null }) => void }) {
  const [title, setTitle] = useState('')
  const [effect, setEffect] = useState('')
  const [rationale, setRationale] = useState('')
  const [owner, setOwner] = useState('')
  const ok = title.trim() && effect.trim()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ok) return
    props.onAdd({ title: title.trim(), expected_effect: effect.trim(), rationale: rationale.trim(), owner: owner.trim() || null })
    setTitle(''); setEffect(''); setRationale(''); setOwner('')
  }
  return (
    <form className="stack-form" onSubmit={submit}>
      <input aria-label="Mitigation title" placeholder="Mitigation title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <input aria-label="Expected effect" placeholder="Expected effect" value={effect} onChange={(e) => setEffect(e.target.value)} />
      <input aria-label="Mitigation rationale" placeholder="Why it helps (optional)" value={rationale} onChange={(e) => setRationale(e.target.value)} />
      <input aria-label="Mitigation owner" placeholder="Owner (optional)" value={owner} onChange={(e) => setOwner(e.target.value)} />
      <button type="submit" disabled={!ok}>Add mitigation</button>
    </form>
  )
}
