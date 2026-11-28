import type { XYPosition } from '@xyflow/react'
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  ApiError, aiApi, analysisApi, api, explorerApi, type AiExample, type AiStatus, mapApi, newMapId, type Analysis, type Explorer, type DraftOut, type GraphOut, type Me, type Readiness,
  type RiskCandidate, type Role, type ScenarioGraph, type Severity,
} from './api'
import {
  collapsedHidden, isModeled, mainPath, riskTrace, shownTransitions, trace, STATUS_LABEL,
} from './graphUtil'
import AnalysisPanel from './AnalysisPanel'
import Inspector from './Inspector'
import MapCanvas, { type Highlight, type Selection } from './MapCanvas'
import OutcomesPanel from './OutcomesPanel'
import VersionsPanel from './VersionsPanel'
import WorstCases from './WorstCases'

type Props = { projectId: string }
type Loaded = { draft: DraftOut; graph: ScenarioGraph; saved: string; revision: number; readiness: Readiness }

const WRITE: Role[] = ['owner', 'editor']
const REVIEW: Role[] = ['owner', 'editor', 'reviewer']

export default function Workspace({ projectId }: Props) {
  const [data, setData] = useState<Loaded | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const [selection, setSelection] = useState<Selection>(null)
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [showRejected, setShowRejected] = useState(false)

  const fetchAll = useCallback(
    () => Promise.all([api.getProject(projectId), mapApi.get(projectId)]), [projectId])
  const show = useCallback(([draft, graph]: [DraftOut, GraphOut]) => {
    setData({ draft, graph: graph.graph, saved: JSON.stringify(graph.graph), revision: graph.revision, readiness: graph.readiness })
    setCollapsed(new Set(graph.graph.nodes.filter((n) => n.collapsed).map((n) => n.id)))
    setConflict(false)
  }, [])

  useEffect(() => {
    let live = true
    fetchAll().then((r) => { if (live) show(r) }, (e: Error) => { if (live) setLoadError(e.message) })
    mapApi.me().then((m) => { if (live) setMe(m) }, () => undefined)
    return () => { live = false }
  }, [fetchAll, show])

  // Probability analysis of the saved map, recalculated whenever the saved revision changes.
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [explorer, setExplorer] = useState<Explorer | null>(null)
  const [explorerError, setExplorerError] = useState<string | null>(null)
  const [centerView, setCenterView] = useState<'map' | 'worst' | 'versions' | 'outcomes'>('map')
  const [analysisError, setAnalysisError] = useState<string | null>(null)
  const savedRevision = data?.revision
  useEffect(() => {
    if (savedRevision === undefined) return
    let live = true
    analysisApi.get(projectId).then(
      (a) => { if (live) { setAnalysis(a); setAnalysisError(null) } },
      (e: Error) => { if (live) setAnalysisError(e.message) })
    explorerApi.get(projectId).then(
      (x) => { if (live) { setExplorer(x); setExplorerError(null) } },
      (e: Error) => { if (live) setExplorerError(e.message) })
    return () => { live = false }
  }, [projectId, savedRevision])

  // Optional AI: only shows when the server has it switched on. Never called automatically.
  const [ai, setAi] = useState<AiStatus | null>(null)
  const [aiNote, setAiNote] = useState<string[]>([])
  const [aiLearned, setAiLearned] = useState<AiExample[] | null>(null)
  useEffect(() => {
    let live = true
    aiApi.status().then((s) => { if (live) setAi(s) }, () => undefined)
    return () => { live = false }
  }, [])

  const graph = data?.graph
  const dirty = data !== null && JSON.stringify(data.graph) !== data.saved
  const role = data?.draft.role
  const canEdit = role !== undefined && WRITE.includes(role)
  const canReview = role !== undefined && REVIEW.includes(role)

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const update = useCallback((fn: (g: ScenarioGraph) => ScenarioGraph) => {
    setData((d) => (d ? { ...d, graph: fn(d.graph) } : d))
  }, [])

  const applyServer = (out: GraphOut) => {
    setData((d) => d && { ...d, graph: out.graph, saved: JSON.stringify(out.graph), revision: out.revision, readiness: out.readiness })
    setConflict(false)
    setError(null)
  }

  async function run(action: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null)
    try {
      await action()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'revision_conflict') setConflict(true)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const needsClean = () => {
    if (!dirty) return false
    setError('Save or discard your map changes first.')
    return true
  }

  const generate = () => run(async () => {
    if (needsClean() || !data) return
    const out = await mapApi.generate(projectId, data.revision)
    applyServer(out)
    const kept = out.preserved.length ? ` Kept ${out.preserved.length} of your decisions and edits.` : ''
    setNotice(`Map generated: ${out.added.length} added, ${out.refreshed.length} updated, ${out.removed.length} removed.${kept}`)
  })

  const review = (riskId: string, decision: 'accept' | 'reject', modelBranch: boolean) => run(async () => {
    if (needsClean() || !data) return
    applyServer(await mapApi.review(projectId, riskId, decision, modelBranch, data.revision))
    setNotice(decision === 'reject' ? 'Suggestion rejected.' : modelBranch ? 'Accepted, and its branch is now on the map.' : 'Suggestion accepted.')
  })

  const askAi = () => run(async () => {
    if (needsClean() || !data) return
    const focus = selection?.kind === 'node' ? selection.id : null
    const out = await aiApi.suggest(projectId, data.revision, focus)
    applyServer(out)
    setNotice(`AI added ${out.added.length} suggestion(s) to review${out.rejected.length ? `, ${out.rejected.length} rejected` : ''}${out.cached ? ' (reused a stored reply, no new model call)' : ''}. They are hypotheses with no probability until you review them.`)
    setAiNote(out.rejected)
    setAiLearned(out.examples_used)
  })

  const save = () => run(async () => {
    if (!data) return
    const blank = data.graph.nodes.some((n) => !n.label.trim()) || data.graph.transitions.some((t) => !t.trigger.trim())
    if (blank) { setError('Every step needs a name and every link needs a trigger before saving.'); return }
    const withView = { ...data.graph, nodes: data.graph.nodes.map((n) => ({ ...n, collapsed: collapsed.has(n.id) })) }
    applyServer(await mapApi.save(projectId, withView, data.revision))
    setNotice('Map saved.')
  })

  const discard = () => {
    if (!data || !window.confirm('Discard your unsaved map changes?')) return
    setData({ ...data, graph: JSON.parse(data.saved) as ScenarioGraph })
    setSelection(null)
  }

  const reloadLatest = () => run(async () => { show(await fetchAll()); setSelection(null); setNotice('Loaded the latest saved map.') })

  // ---- view state derived from the graph
  const view = useMemo(() => {
    if (!graph) return null
    const { hidden, counts } = collapsedHidden(graph, collapsed)
    const shownNodes = new Set(graph.nodes
      .filter((n) => (showRejected || n.review_status !== 'rejected') && !hidden.has(n.id))
      .map((n) => n.id))
    const edges = shownTransitions(graph, shownNodes, showRejected)
    const branchCounts = new Map<string, number>()  // collapsible: main-path nodes with side branches
    const main = mainPath(graph)
    for (const t of graph.transitions) {
      if (t.outcome !== 'success' && t.review_status !== 'rejected' && main.has(t.source_node_id)) {
        branchCounts.set(t.source_node_id, (branchCounts.get(t.source_node_id) ?? 0) + 1)
      }
    }
    const rejectedHidden = showRejected ? 0 : graph.nodes.filter((n) => n.review_status === 'rejected').length
    return { shownNodes, shownEdges: new Set(edges.map((t) => t.id)), edges, hiddenCounts: counts, branchCounts, hiddenTotal: hidden.size, rejectedHidden }
  }, [graph, collapsed, showRejected])

  const highlight: Highlight | null = useMemo(() => {
    if (!graph || !view || !selection || selection.kind === 'transition') return null
    const risk = selection.kind === 'risk' ? graph.risks.find((r) => r.id === selection.id) : null
    if (selection.kind === 'risk' && !risk) return null
    const focus = risk ? risk.affected_node_ids : [selection.id]
    const result = risk ? riskTrace(risk, view.edges) : trace(view.edges, focus, focus)
    if (!result) return null
    return {
      focus: new Set(focus),
      causes: result.upstream.nodes,
      effects: result.downstream.nodes,
      edges: new Set([...result.upstream.edges, ...result.downstream.edges]),
    }
  }, [graph, view, selection])

  const toggleCollapse = useCallback((id: string) => {
    setCollapsed((c) => { const next = new Set(c); if (next.has(id)) next.delete(id); else next.add(id); return next })
  }, [])

  const move = useCallback((id: string, pos: XYPosition) => {
    update((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === id ? { ...n, layout: { x: Math.round(pos.x), y: Math.round(pos.y) } } : n)) }))
  }, [update])

  const connect = useCallback((source: string, target: string) => {
    const id = newMapId('trn')
    update((g) => {
      const group = g.transitions.find((t) => t.source_node_id === source && t.decision_group_id)?.decision_group_id ?? null
      return {
        ...g,
        transitions: [...g.transitions, {
          id, source_node_id: source, target_node_id: target, outcome: 'success', trigger: 'New link',
          decision_group_id: group, origin: 'user', review_status: 'accepted', probability: { kind: 'unknown' },
        }],
      }
    })
    setSelection({ kind: 'transition', id })
  }, [update])

  const addStep = () => {
    const id = newMapId('nod')
    const xs = graph?.nodes.map((n) => n.layout?.x ?? 0) ?? [0]
    update((g) => ({
      ...g,
      nodes: [...g.nodes, {
        id, kind: 'step', label: 'New step', terminal: false, origin: 'user', review_status: 'accepted',
        collapsed: false, layout: { x: Math.max(...xs) / 2, y: 320 },
      }],
    }))
    setSelection({ kind: 'node', id })
  }

  // ---- render
  if (loadError) {
    return (
      <section className="page">
        <p className="alert error" role="alert">{loadError}</p>
        <a href="#/">Back to projects</a>
      </section>
    )
  }
  if (!data || !graph || !view) return <section className="page"><p className="muted" role="status">Loading the workspace…</p></section>

  const plan = data.draft.plan
  const pick = (ref: string) => {
    if (graph.nodes.some((n) => n.id === ref)) setSelection({ kind: 'node', id: ref })
    else if (graph.transitions.some((t) => t.id === ref)) setSelection({ kind: 'transition', id: ref })
  }
  const empty = graph.nodes.length === 0

  return (
    <div className="workspace">
      <aside className="panel left" aria-label="Project and suggestions">
        <PlanSummary draft={data.draft} projectId={projectId} />
        <Checks readiness={data.readiness} onPick={pick} />
        <AnalysisPanel analysis={analysis} loading={analysis === null} error={analysisError}
          stale={dirty} onPick={pick} />
        <Suggestions graph={graph} selection={selection} canReview={canReview} canEdit={canEdit} busy={busy}
          onSelect={setSelection} onReview={review}
          onAdd={(risk) => { update((g) => ({ ...g, risks: [...g.risks, risk] })); setSelection({ kind: 'risk', id: risk.id }) }}
          selectedNode={selection?.kind === 'node' ? selection.id : null} />
        {ai?.enabled && canEdit && !empty && (
          <section className="panel-section" aria-labelledby="ai-h">
            <h2 id="ai-h">AI suggestions <span className="tag">optional</span></h2>
            <p className="muted small">
              Asks {ai.local ? 'the local' : 'the remote'} model ({ai.model}) for hazards, missing steps and recoveries
              {selection?.kind === 'node' ? ' around the selected step' : ' along the main path'}. It runs only when you click,
              sends only nearby steps and constraints, and never gives numbers. It also learns from risks you accepted
              or rejected on your other projects (text only). Results join the review list above.
            </p>
            <button type="button" disabled={busy || dirty} onClick={askAi}>Suggest with AI</button>
            {aiLearned !== null && (
              aiLearned.length === 0 ? (
                <p className="muted small">No similar reviewed risks from your other projects yet, so it worked from this map alone.</p>
              ) : (
                <details className="small" open>
                  <summary>Learned from {aiLearned.filter((e) => e.decision === 'accepted').length} of your past accepted risks{aiLearned.some((e) => e.decision === 'rejected') ? ` and avoided ${aiLearned.filter((e) => e.decision === 'rejected').length} rejected idea(s)` : ''}</summary>
                  <ul className="plain-list">{aiLearned.map((e) => (
                    <li key={`${e.decision}-${e.project_title}-${e.title}`}>
                      <span className={`tag status-${e.decision}`}>{e.decision === 'accepted' ? 'example' : 'avoid'}</span> {e.title}
                      <span className="muted"> · from “{e.project_title}”</span>
                    </li>
                  ))}</ul>
                  <p className="muted">Only text from your own projects is used, never their numbers. Nothing is trained; the closest matches are picked by word similarity on this computer.</p>
                </details>
              )
            )}
            {aiNote.length > 0 && (
              <details className="small"><summary>Rejected from the model's reply ({aiNote.length})</summary>
                <ul className="plain-list">{aiNote.map((r) => <li key={r}>{r}</li>)}</ul>
              </details>
            )}
          </section>
        )}
      </aside>

      <section className="center" aria-label="Scenario map">
        <div className="toolbar" role="toolbar" aria-label="Map actions">
          <strong className="toolbar-title">{plan.project.title}</strong>
          <div className="segmented" role="group" aria-label="View">
            <button type="button" aria-pressed={centerView === 'map'} onClick={() => setCenterView('map')}>Map</button>
            <button type="button" aria-pressed={centerView === 'worst'} onClick={() => setCenterView('worst')}>Worst cases</button>
            <button type="button" aria-pressed={centerView === 'versions'} onClick={() => setCenterView('versions')}>Versions</button>
            <button type="button" aria-pressed={centerView === 'outcomes'} onClick={() => setCenterView('outcomes')}>Outcomes</button>
          </div>
          <a className="button" href={`#/p/${projectId}/report`}>Report</a>
          {canEdit && (
            <button type="button" className={empty ? 'primary' : ''} disabled={busy} onClick={generate}>
              {empty ? 'Generate map' : 'Regenerate from plan'}
            </button>
          )}
          {canEdit && !empty && <button type="button" disabled={busy} onClick={addStep}>+ Step</button>}
          <label className="check inline">
            <input type="checkbox" checked={showRejected} onChange={(e) => setShowRejected(e.target.checked)} />
            Show rejected
          </label>
          {!empty && (
            <button type="button" onClick={() => setCollapsed(collapsed.size ? new Set() : new Set(view.branchCounts.keys()))}>
              {collapsed.size ? 'Expand all' : 'Collapse all'}
            </button>
          )}
          <span className="toolbar-status muted" role="status">
            {busy ? 'Working…' : dirty ? 'Unsaved map changes' : `Saved · revision ${data.revision}`}
            {view.hiddenTotal > 0 && ` · ${view.hiddenTotal} collapsed`}
            {view.rejectedHidden > 0 && ` · ${view.rejectedHidden} rejected hidden`}
          </span>
          {canEdit && dirty && <button type="button" onClick={discard} disabled={busy}>Discard</button>}
          {canEdit && <button type="button" className="primary" onClick={save} disabled={busy || !dirty}>Save map</button>}
        </div>
        {conflict && (
          <div className="alert warning banner" role="alert">
            <p>Someone saved this project after you opened it. Your change was not saved, and the newer version is kept.</p>
            <button type="button" onClick={reloadLatest}>Load the latest version (discards your unsaved map edits)</button>
          </div>
        )}
        {error && !conflict && <p className="alert error banner" role="alert">{error}</p>}
        {notice && <p className="alert ok banner" role="status">{notice}</p>}
        {!canEdit && <p className="alert info banner">Your role ({role}) can view this map{canReview ? ' and review suggestions' : ''} but not edit it.</p>}
        {centerView === 'map' && <Legend />}
        <div className={centerView === 'map' ? 'canvas' : 'canvas scroll'}>
          {centerView === 'outcomes' ? (
            <OutcomesPanel projectId={projectId} graph={graph} revision={data.revision} dirty={dirty}
              canRecord={canEdit} canApprove={role === 'owner' || role === 'reviewer'}
              onApplied={applyServer} />
          ) : centerView === 'versions' ? (
            <VersionsPanel projectId={projectId} revision={data.revision} dirty={dirty}
              canPublish={canEdit} onSaved={() => undefined} />
          ) : centerView === 'worst' ? (
            <WorstCases explorer={explorer} error={explorerError} stale={dirty}
              onShow={(id) => { setSelection({ kind: 'risk', id }); setCenterView('map') }} />
          ) : empty ? (
            <div className="empty-map">
              <h3>No scenario map yet</h3>
              <p className="muted">Generate one from your plan: start, tasks in dependency order, goal, plus suggested risks to review.</p>
              {canEdit && <button type="button" className="primary" disabled={busy} onClick={generate}>Generate map</button>}
            </div>
          ) : (
            <MapCanvas graph={graph} shownNodes={view.shownNodes} shownEdges={view.shownEdges}
              collapsed={collapsed} hiddenCounts={view.hiddenCounts} branchCounts={view.branchCounts}
              selection={selection} highlight={highlight} canEdit={canEdit}
              onSelect={setSelection} onMove={move} onConnect={connect} onToggleCollapse={toggleCollapse} />
          )}
        </div>
      </section>

      <aside className="panel right" aria-label="Details">
        <Inspector graph={graph} selection={selection} me={me} canEdit={canEdit} canReview={canReview}
          busy={busy} update={update} onSelect={setSelection} onReview={review} />
      </aside>
    </div>
  )
}

function PlanSummary({ draft, projectId }: { draft: DraftOut; projectId: string }) {
  const { project, tasks, prerequisites } = draft.plan
  const ctx = project.context
  const title = new Map(tasks.map((t) => [t.id, t.title]))
  const facts = [
    ctx.domain && `Domain: ${ctx.domain}`,
    ctx.deadline && `Deadline: ${ctx.deadline}`,
    ctx.budget && `Budget: ${ctx.budget}${ctx.currency ? ` ${ctx.currency}` : ''}`,
    ctx.risk_tolerance && `Risk tolerance: ${ctx.risk_tolerance}`,
    ctx.stakeholders.length > 0 && `Stakeholders: ${ctx.stakeholders.join(', ')}`,
  ].filter(Boolean) as string[]
  return (
    <section className="panel-section">
      <div className="panel-head">
        <h2>Plan</h2>
        <a href={`#/p/${projectId}/edit`}>Edit plan</a>
      </div>
      <dl className="facts">
        <dt>Start</dt><dd>{project.start}</dd>
        <dt>Goal</dt><dd>{project.goal}</dd>
        {project.success_criteria && <><dt>Success criteria</dt><dd>{project.success_criteria}</dd></>}
      </dl>
      {facts.length > 0 && <ul className="plain-list small">{facts.map((f) => <li key={f}>{f}</li>)}</ul>}
      <h3 className="sub">Tasks ({tasks.length})</h3>
      {tasks.length === 0 ? <p className="muted small">No tasks. The map runs straight from start to goal.</p> : (
        <ol className="plain-list numbered small">
          {[...tasks].sort((a, b) => a.position - b.position).map((t) => (
            <li key={t.id}>{t.title}{t.owner && <span className="muted"> · {t.owner}</span>}{t.duration_days && <span className="muted"> · {t.duration_days} d</span>}</li>
          ))}
        </ol>
      )}
      {prerequisites.length > 0 && (
        <>
          <h3 className="sub">Dependencies</h3>
          <ul className="plain-list small">
            {prerequisites.map((p) => <li key={p.id}>{title.get(p.before_task_id) ?? '?'} → {title.get(p.after_task_id) ?? '?'}</li>)}
          </ul>
        </>
      )}
    </section>
  )
}

function Checks({ readiness, onPick }: { readiness: Readiness; onPick: (ref: string) => void }) {
  return (
    <section className="panel-section" aria-labelledby="checks-h">
      <h2 id="checks-h">Map checks</h2>
      <p className="small">
        <span className={`tag ${readiness.can_analyze ? 'ok' : 'bad'}`}>{readiness.can_analyze ? 'Ready to analyze' : 'Not ready to analyze'}</span>{' '}
        <span className={`tag ${readiness.can_mark_reviewed ? 'ok' : 'bad'}`}>{readiness.can_mark_reviewed ? 'Can be marked reviewed' : 'Not reviewable yet'}</span>
      </p>
      {readiness.blockers.length > 0 && <ul className="plain-list small">{readiness.blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
      {readiness.issues.length > 0 && (
        <ul className="issues small">
          {readiness.issues.map((i, n) => (
            <li key={`${i.code}-${n}`} className={i.severity}>
              <strong>{i.severity === 'error' ? 'Problem' : 'Warning'}:</strong> {i.message}
              {i.refs[0] && <> <button type="button" className="link" onClick={() => i.refs.forEach(onPick)}>Show</button></>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

type Filter = 'pending' | 'decided' | 'all'

function Suggestions(props: {
  graph: ScenarioGraph; selection: Selection; canReview: boolean; canEdit: boolean; busy: boolean
  selectedNode: string | null
  onSelect: (s: Selection) => void
  onReview: (riskId: string, decision: 'accept' | 'reject', modelBranch: boolean) => void
  onAdd: (risk: RiskCandidate) => void
}) {
  const [filter, setFilter] = useState<Filter>('pending')
  const risks = props.graph.risks.filter((r) =>
    filter === 'all' || (filter === 'pending' ? r.review_status === 'suggested' : r.review_status !== 'suggested'))
  const pending = props.graph.risks.filter((r) => r.review_status === 'suggested').length
  const modeled = (r: RiskCandidate) => props.graph.transitions.some((t) => t.rule_key === r.dedupe_key && isModeled(t.review_status))

  return (
    <section className="panel-section" aria-labelledby="sugg-h">
      <h2 id="sugg-h">Risks and suggestions</h2>
      <div className="segmented" role="group" aria-label="Filter suggestions">
        {(['pending', 'decided', 'all'] as Filter[]).map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f === 'pending' ? `To review (${pending})` : f === 'decided' ? 'Decided' : 'All'}
          </button>
        ))}
      </div>
      {risks.length === 0 && <p className="muted small">{filter === 'pending' ? 'Nothing waiting for review.' : 'No risks here yet.'}</p>}
      <ul className="risk-list">
        {risks.map((r) => {
          const selected = props.selection?.kind === 'risk' && props.selection.id === r.id
          return (
            <li key={r.id} className={selected ? 'selected' : ''}>
              <button type="button" className="risk-title" aria-pressed={selected}
                onClick={() => props.onSelect(selected ? null : { kind: 'risk', id: r.id })}>
                <span>{r.title}</span>
                <span className="small muted">{r.category.replaceAll('_', ' ')}{r.origin === 'rule' ? ' · rule' : r.origin === 'ai' ? ' · AI hypothesis' : ''}</span>
              </button>
              <div className="risk-meta">
                {r.impact && <span className={`sev sev-${r.impact}`}>{r.impact}</span>}
                <span className={`tag status-${r.review_status}`}>{STATUS_LABEL[r.review_status]}</span>
                {modeled(r) && <span className="tag">on map</span>}
              </div>
              {props.canReview && r.review_status === 'suggested' && (
                <div className="row-actions start">
                  <button type="button" disabled={props.busy} onClick={() => props.onReview(r.id, 'accept', false)} aria-label={`Accept ${r.title}`}>Accept</button>
                  {r.affected_node_ids.length > 1 && (
                    <button type="button" disabled={props.busy} onClick={() => props.onReview(r.id, 'accept', true)} aria-label={`Accept ${r.title} and add its branch`}>+ Branch</button>
                  )}
                  <button type="button" className="danger" disabled={props.busy} onClick={() => props.onReview(r.id, 'reject', false)} aria-label={`Reject ${r.title}`}>Reject</button>
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {props.canEdit && <AddRisk selectedNode={props.selectedNode} onAdd={props.onAdd} />}
    </section>
  )
}

function AddRisk({ selectedNode, onAdd }: { selectedNode: string | null; onAdd: (r: RiskCandidate) => void }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [trigger, setTrigger] = useState('')
  const [consequence, setConsequence] = useState('')
  const [impact, setImpact] = useState<Severity | ''>('')
  const ok = title.trim() && trigger.trim()
  if (!open) return <button type="button" onClick={() => setOpen(true)}>+ Add a risk</button>
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ok) return
    onAdd({
      id: newMapId('rsk'), category: 'manual', title: title.trim(), trigger: trigger.trim(),
      affected_node_ids: selectedNode ? [selectedNode] : [], rationale: '', consequence: consequence.trim(),
      impact: impact || null, origin: 'user', review_status: 'accepted', likelihood: { kind: 'unknown' },
    })
    setTitle(''); setTrigger(''); setConsequence(''); setImpact(''); setOpen(false)
  }
  return (
    <form className="stack-form" onSubmit={submit} aria-label="Add a risk">
      <input aria-label="Risk title" placeholder="Risk title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <input aria-label="Risk trigger" placeholder="What triggers it" value={trigger} onChange={(e) => setTrigger(e.target.value)} />
      <input aria-label="Risk consequence" placeholder="What could follow (optional)" value={consequence} onChange={(e) => setConsequence(e.target.value)} />
      <select aria-label="Risk impact" value={impact} onChange={(e) => setImpact(e.target.value as Severity | '')}>
        <option value="">Impact not set</option>
        {['low', 'medium', 'high', 'critical'].map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      <p className="muted small">{selectedNode ? 'Attached to the selected step.' : 'Select a step on the map first to attach it there.'}</p>
      <div className="row-actions start">
        <button type="submit" className="primary" disabled={!ok}>Add risk</button>
        <button type="button" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  )
}

function Legend() {
  return (
    <ul className="legend" aria-label="Legend">
      <li><span className="swatch success" /> Success</li>
      <li><span className="swatch failure" /> Failure</li>
      <li><span className="swatch delay" /> Delay</li>
      <li><span className="swatch recovery" /> Recovery</li>
      <li><span className="swatch pending" /> Suggested</li>
      <li><span className="key key-cause">cause</span></li>
      <li><span className="key key-effect">effect</span></li>
    </ul>
  )
}
