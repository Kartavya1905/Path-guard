import { useEffect, useState } from 'react'
import {
  api, reportApi, versionsApi, type Report, type Role, type ScenarioGraph, type VersionSummary,
} from './api'
import { KIND_LABEL, OUTCOME_LABEL, isModeled } from './graphUtil'
import { estimateLabel, pct1, pctExact } from './prob'

// Print-friendly report. Everything is generated locally by the API and permission-checked.
// The browser's Print dialog saves it as a PDF. A report is "Reviewed" only for a saved
// version a reviewer has signed off; anything else is clearly labeled as a draft.

const REVIEW: Role[] = ['owner', 'reviewer']

export default function ReportPage({ projectId }: { projectId: string }) {
  const [versions, setVersions] = useState<VersionSummary[]>([])
  const [role, setRole] = useState<Role | null>(null)
  const [version, setVersion] = useState('')
  const [compareWith, setCompareWith] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [reload, setReload] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    versionsApi.list(projectId).then((v) => { if (live) setVersions(v) }, () => undefined)
    api.getProject(projectId).then((d) => { if (live) setRole(d.role) }, () => undefined)
    return () => { live = false }
  }, [projectId])

  useEffect(() => {
    let live = true
    reportApi.get(projectId, version, version ? compareWith : '').then(
      (r) => { if (live) { setReport(r); setError(null) } },
      (e: Error) => { if (live) setError(e.message) })
    return () => { live = false }
  }, [projectId, version, compareWith, reload])

  const signOff = async () => {
    try {
      await reportApi.signOff(projectId, version, note)
      setNotice('Signed off. This version\'s report is now marked reviewed.')
      setNote('')
      setReload((n) => n + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="report-page">
      <div className="report-controls no-print">
        <a href={`#/p/${projectId}`}>← Back to workspace</a>
        <label>Report on{' '}
          <select aria-label="Report on" value={version} onChange={(e) => { setVersion(e.target.value); setCompareWith('') }}>
            <option value="">Working map (draft)</option>
            {versions.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.label}</option>)}
          </select>
        </label>
        {version && (
          <label>Compare with{' '}
            <select aria-label="Compare with" value={compareWith} onChange={(e) => setCompareWith(e.target.value)}>
              <option value="">No comparison</option>
              {versions.filter((v) => v.id !== version).map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.label}</option>)}
            </select>
          </label>
        )}
        <button type="button" className="primary" onClick={() => window.print()}>Print / save as PDF</button>
        <a className="button" href={reportApi.csvUrl(projectId, version)} download>Download risk register (CSV)</a>
      </div>
      {notice && <p className="alert ok no-print" role="status">{notice}</p>}
      {error && <p className="alert error no-print" role="alert">{error}</p>}
      {version && role && REVIEW.includes(role) && report?.mode === 'draft' && (
        <div className="card no-print signoff">
          <h3>Reviewer sign-off</h3>
          {report.readiness.can_mark_reviewed ? (
            <>
              <p className="small muted">Signing off marks this saved version's report as reviewed. The version can't change afterwards.</p>
              <div className="inline-form">
                <input aria-label="Sign-off note" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
                <button type="button" className="primary" onClick={() => void signOff()}>Sign off as reviewed</button>
              </div>
            </>
          ) : (
            <ul className="plain-list small">{report.readiness.blockers.map((b) => <li key={b}>{b}</li>)}</ul>
          )}
        </div>
      )}
      {!report ? <p className="muted">Building the report…</p> : <ReportBody r={report} />}
    </div>
  )
}

function ReportBody({ r }: { r: Report }) {
  const a = r.analysis
  const nodes = new Map(r.graph.nodes.map((n) => [n.id, n.label]))
  const assumptions = [
    ...r.graph.assumptions.map((x) => `${x.text} (${x.status}${x.owner ? `, owner ${x.owner}` : ''})`),
    ...r.graph.mitigations.flatMap((m) => m.assumptions.map((x) => `${x} (mitigation: ${m.title})`)),
  ]
  return (
    <article className={`report ${r.mode}`}>
      <header className="report-head">
        <p className={`report-status ${r.mode}`} role="note">{r.mode === 'draft' ? 'DRAFT' : 'REVIEWED'} · {r.status_label}</p>
        <h1>{r.title}</h1>
        <p><strong>Start:</strong> {r.plan.project.start} · <strong>Goal:</strong> {r.plan.project.goal}</p>
        <p className="small muted">
          Generated {new Date(r.generated_at).toLocaleString()}
          {r.version && <> · version v{r.version.number} “{r.version.label}” · content hash {r.version.content_hash.slice(0, 12)}…</>}
        </p>
      </header>

      <section>
        <h2>Scenario map</h2>
        <GraphSnapshot graph={r.graph} />
        <p className="small muted">Modeled steps and links only. Line style shows the outcome (solid success, thick failure, dashed delay, dotted recovery), and each link is labeled with its outcome and estimate.</p>
      </section>

      <section>
        <h2>Results</h2>
        {a.status === 'blocked' ? <ul>{a.blockers.map((b) => <li key={b}>{b}</li>)}</ul> : (
          <table className="cmp-table">
            <tbody>
              <tr><th scope="row">Clean first-pass success</th><td>{show(a.first_pass)}</td></tr>
              <tr><th scope="row">Eventual success</th><td>{show(a.eventual_success)}</td></tr>
              {Object.entries(a.outcomes).map(([k, v]) => <tr key={k}><th scope="row">Ends: {k.replaceAll('_', ' ')}</th><td>{show(v)}</td></tr>)}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Risk register</h2>
        <table className="cmp-table small">
          <thead><tr><th scope="col">#</th><th scope="col">Risk</th><th scope="col">Impact</th><th scope="col">Likelihood</th><th scope="col">Evidence</th><th scope="col">Status</th><th scope="col">Trigger and consequence</th></tr></thead>
          <tbody>{r.explorer.risks.map((x) => (
            <tr key={x.id}><td>{x.rank}</td><td>{x.title}</td><td>{x.impact ?? 'not set'}</td>
              <td>{show(x.likelihood.value)} ({x.likelihood.basis})</td><td>{x.evidence.strength}</td>
              <td>{x.review_status}</td><td>{x.trigger}. {x.consequence}</td></tr>
          ))}</tbody>
        </table>
        {r.explorer.excluded_rejected > 0 && <p className="small muted">{r.explorer.excluded_rejected} rejected risk(s) not listed.</p>}
      </section>

      <section>
        <h2>Mitigations and owners</h2>
        {r.graph.mitigations.length === 0 ? <p className="muted">None recorded.</p> : (
          <table className="cmp-table small">
            <thead><tr><th scope="col">Mitigation</th><th scope="col">Owner</th><th scope="col">Expected effect</th><th scope="col">Cost / delay</th></tr></thead>
            <tbody>{r.graph.mitigations.map((m) => <tr key={m.id}><td>{m.title}</td><td>{m.owner ?? 'no owner'}</td><td>{m.expected_effect}</td><td>{m.cost ?? '—'} / {m.delay_days ?? '—'} d</td></tr>)}</tbody>
          </table>
        )}
      </section>

      {r.comparison && (
        <section>
          <h2>Comparison with v{r.compare_version?.number} “{r.compare_version?.label}”</h2>
          <p>Clean first-pass: {r.comparison.first_pass_delta.note} Eventual: {r.comparison.eventual_delta.note}</p>
          {r.comparison.flags.length > 0 && <ul className="small">{r.comparison.flags.map((f) => <li key={f}>{f}</li>)}</ul>}
          <ul className="small">{r.comparison.changed_branches.map((b) => <li key={b.transition_id}>{b.label}: {b.change}, {b.before ?? '—'} → {b.after ?? '—'}</li>)}</ul>
        </section>
      )}

      <section>
        <h2>Assumptions</h2>
        {assumptions.length === 0 ? <p className="muted">None recorded.</p> : <ul className="small">{assumptions.map((x) => <li key={x}>{x}</li>)}</ul>}
      </section>

      <section>
        <h2>Estimate sources</h2>
        <table className="cmp-table small">
          <thead><tr><th scope="col">Link</th><th scope="col">Estimate</th><th scope="col">Source</th><th scope="col">Verification</th></tr></thead>
          <tbody>{r.estimate_sources.map((s) => <tr key={s.transition_id}><td>{s.link}</td><td>{s.estimate}</td><td>{s.detail}</td><td>{s.verification}</td></tr>)}</tbody>
        </table>
      </section>

      <section>
        <h2>Open evidence gaps ({r.evidence_gaps.length})</h2>
        {r.evidence_gaps.length === 0 ? <p className="muted">None.</p> : <ul className="small">{r.evidence_gaps.map((g) => <li key={`${g.kind}-${g.target_id}`}>{g.message}</li>)}</ul>}
      </section>

      <section>
        <h2>Review status</h2>
        <ul className="small">{Object.entries(r.review_summary).map(([k, counts]) => (
          <li key={k}>{k}: {Object.entries(counts).map(([s, c]) => `${c} ${s}`).join(', ') || 'none'}</li>
        ))}</ul>
        {r.signoffs.length > 0 ? (
          <ul className="small">{r.signoffs.map((s) => <li key={s.id}>Signed off by {s.reviewer_name} on {new Date(s.created_at).toLocaleString()}{s.note ? `: ${s.note}` : ''}</li>)}</ul>
        ) : <p className="small">No reviewer sign-off.</p>}
        <p className="small muted">Map checks: {r.readiness.can_analyze ? 'ready to analyze' : 'not ready to analyze'}; {r.readiness.can_mark_reviewed ? 'reviewable' : 'not reviewable yet'}. Nodes referenced: {nodes.size}.</p>
      </section>
    </article>
  )
}

function show(v: { status: string; value?: string | null; low: string; high: string } | null | undefined): string {
  if (!v) return 'unknown'
  if (v.status === 'exact') return `${pct1(v.value)} (exact ${pctExact(v.value)})`
  if (v.status === 'bounds') return `${pctExact(v.low)}–${pctExact(v.high)}`
  return Number(v.low) === 0 ? 'unknown (partial)' : `at least ${pctExact(v.low)} (partial)`
}

const STROKE: Record<string, { color: string; dash?: string; width: number }> = {
  success: { color: '#1f7a3f', width: 1.5 }, failure: { color: '#b42318', width: 2.5 },
  delay: { color: '#b7791f', dash: '8 4', width: 1.8 }, recovery: { color: '#1d4ed8', dash: '2 4', width: 1.8 },
  residual: { color: '#6b7280', dash: '4 4', width: 1.4 },
}

/** A static SVG of the modeled map from the saved layout, readable on screen and paper. */
function GraphSnapshot({ graph }: { graph: ScenarioGraph }) {
  const W = 190
  const H = 54
  const nodes = graph.nodes.filter((n) => isModeled(n.review_status))
  const pos = new Map(nodes.map((n, i) => [n.id, n.layout ?? { x: (i % 5) * 260, y: Math.floor(i / 5) * 160 }]))
  const xs = [...pos.values()].map((p) => p.x)
  const ys = [...pos.values()].map((p) => p.y)
  const minX = Math.min(0, ...xs) - 20
  const minY = Math.min(0, ...ys) - 30
  const width = Math.max(...xs, 0) + W + 40 - minX
  const height = Math.max(...ys, 0) + H + 50 - minY
  const links = graph.transitions.filter((t) => isModeled(t.review_status) && pos.has(t.source_node_id) && pos.has(t.target_node_id))
  if (nodes.length === 0) return <p className="muted">No modeled map yet.</p>
  return (
    <svg className="snapshot" viewBox={`${minX} ${minY} ${width} ${height}`} role="img" aria-label="Scenario map snapshot">
      <defs>{Object.entries(STROKE).map(([k, s]) => (
        <marker key={k} id={`arrow-${k}`} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill={s.color} />
        </marker>
      ))}</defs>
      {links.map((t) => {
        const a = pos.get(t.source_node_id)!
        const b = pos.get(t.target_node_id)!
        const s = STROKE[t.outcome] ?? STROKE.success!
        // Up/down: connect bottom/top centres. Forward on a row: right edge to left edge.
        // Backward on a row: loop underneath.
        const dy = b.y - a.y
        let path: string
        let lx: number
        let ly: number
        let anchor: 'start' | 'middle' | 'end' = 'middle'
        if (Math.abs(dy) > H) {
          const down = dy > 0
          const shift = down ? W / 6 : -W / 6 // keep a down link and its way back apart
          const sx = a.x + W / 2 + shift
          const sy = down ? a.y + H : a.y
          const tx = b.x + W / 2 + shift
          const ty = down ? b.y : b.y + H
          const my = (sy + ty) / 2
          path = `M ${sx} ${sy} C ${sx} ${my}, ${tx} ${my}, ${tx} ${ty}`
          lx = (sx + tx) / 2 + (down ? 4 : -4)
          ly = my - 4
          anchor = down ? 'start' : 'end' // the two labels of a pair point away from each other
        } else if (b.x > a.x + W) {
          const x1 = a.x + W
          const y1 = a.y + H / 2
          const x2 = b.x
          const y2 = b.y + H / 2
          path = `M ${x1} ${y1} C ${x1 + 50} ${y1}, ${x2 - 50} ${y2}, ${x2} ${y2}`
          lx = (x1 + x2) / 2
          ly = (y1 + y2) / 2 - 6
        } else {
          path = `M ${a.x + W / 2} ${a.y + H} C ${a.x + W / 2} ${a.y + H + 70}, ${b.x + W / 2} ${b.y + H + 70}, ${b.x + W / 2} ${b.y + H}`
          lx = (a.x + b.x) / 2 + W / 2
          ly = Math.max(a.y, b.y) + H + 58
        }
        return (
          <g key={t.id}>
            <path d={path} fill="none" stroke={s.color} strokeWidth={s.width} strokeDasharray={s.dash} markerEnd={`url(#arrow-${t.outcome})`} />
            <text x={lx} y={ly} textAnchor={anchor} fontSize="11" fill={s.color} className="snap-label">
              {OUTCOME_LABEL[t.outcome]} {estimateLabel(t.probability)}{t.max_traversals ? ` · max ${t.max_traversals}×` : ''}
            </text>
          </g>
        )
      })}
      {nodes.map((n) => {
        const p = pos.get(n.id)!
        const label = n.label.length > 30 ? `${n.label.slice(0, 29)}…` : n.label
        return (
          <g key={n.id}>
            <rect x={p.x} y={p.y} width={W} height={H} rx="7" className={`snap-node kind-${n.kind}`} />
            <text x={p.x + 8} y={p.y + 16} fontSize="10" fontWeight="700" fill="#4b5563">{KIND_LABEL[n.kind].toUpperCase()}{n.terminal && n.kind !== 'goal' ? ' · END' : ''}</text>
            <text x={p.x + 8} y={p.y + 36} fontSize="12" fontWeight="600" fill="#1c2430">{label}</text>
          </g>
        )
      })}
    </svg>
  )
}
