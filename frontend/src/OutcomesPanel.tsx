import { useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  outcomesApi, versionsApi, type Estimate, type GraphOut, type ObservedOutcome, type Proposal,
  type ScenarioGraph, type ScenarioVersion, type VersionSummary,
} from './api'
import { pctExact, toFraction } from './prob'

// Record what actually happened at a decision, then review an estimate candidate built only
// from comparable observations. Nothing is applied until a reviewer approves it.

type Props = {
  projectId: string
  graph: ScenarioGraph // the working map
  revision: number
  dirty: boolean
  canRecord: boolean
  canApprove: boolean
  onApplied: (out: GraphOut) => void
}

const today = () => new Date().toISOString().slice(0, 10)

function decisionLinks(graph: ScenarioGraph) {
  const label = new Map(graph.nodes.map((n) => [n.id, n.label]))
  const groups = new Map(graph.decision_groups.map((g) => [g.id, g.label]))
  return graph.transitions
    .filter((t) => t.decision_group_id && (t.review_status === 'accepted' || t.review_status === 'edited'))
    .map((t) => ({
      id: t.id, group: t.decision_group_id as string,
      text: `${groups.get(t.decision_group_id as string) ?? 'Decision'}: ${label.get(t.source_node_id)} → ${label.get(t.target_node_id)} (${t.trigger})`,
    }))
}

export default function OutcomesPanel(p: Props) {
  const [versions, setVersions] = useState<VersionSummary[]>([])
  const [outcomes, setOutcomes] = useState<ObservedOutcome[]>([])
  const [reload, setReload] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    versionsApi.list(p.projectId).then((v) => { if (live) setVersions(v) }, () => undefined)
    outcomesApi.list(p.projectId).then((o) => { if (live) setOutcomes(o) }, (e: Error) => { if (live) setError(e.message) })
    return () => { live = false }
  }, [p.projectId, reload])

  return (
    <div className="versions">
      {error && <p className="alert error" role="alert">{error}</p>}
      {notice && <p className="alert ok" role="status">{notice}</p>}
      {p.canRecord && (
        <RecordForm projectId={p.projectId} versions={versions}
          onSaved={() => { setNotice('Outcome recorded.'); setError(null); setReload((n) => n + 1) }}
          onError={setError} />
      )}
      <section className="card">
        <h3>Observed outcomes ({outcomes.length})</h3>
        {outcomes.length === 0 ? <p className="muted">None recorded yet.</p> : (
          <table className="cmp-table small">
            <thead><tr><th scope="col">Observed</th><th scope="col">Version</th><th scope="col">Result</th><th scope="col">Context</th><th scope="col">Notes</th></tr></thead>
            <tbody>{outcomes.map((o) => (
              <tr key={o.id}>
                <td>{o.period_start ? `${o.period_start} to ${o.period_end ?? o.observed_on}` : o.observed_on}</td>
                <td>v{versions.find((v) => v.id === o.version_id)?.number ?? '?'}</td>
                <td>{o.occurrences} of {o.exposure} eligible runs</td>
                <td>{o.context.join(', ') || 'no tags'}</td>
                <td>{o.notes ?? ''}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
      <ProposalBox {...p} onApplied={(out) => { setNotice('Estimate updated in a new revision. The previous map was saved as a snapshot version.'); setReload((n) => n + 1); p.onApplied(out) }} />
    </div>
  )
}

function RecordForm(props: { projectId: string; versions: VersionSummary[]; onSaved: () => void; onError: (m: string) => void }) {
  const [versionId, setVersionId] = useState('')
  const [version, setVersion] = useState<ScenarioVersion | null>(null)
  const [f, setF] = useState({ link: '', observed_on: today(), period_start: '', period_end: '', exposure: '', occurrences: '', context: '', notes: '' })
  const chosen = versionId || props.versions.at(-1)?.id || ''

  useEffect(() => {
    if (!chosen) return
    let live = true
    versionsApi.get(props.projectId, chosen).then((v) => { if (live) setVersion(v) }, () => undefined)
    return () => { live = false }
  }, [props.projectId, chosen])

  const links = useMemo(() => (version ? decisionLinks(version.graph) : []), [version])
  if (props.versions.length === 0) {
    return <section className="card"><h3>Record an outcome</h3><p className="muted">Save a version first: outcomes are tied to a saved version and one of its decisions.</p></section>
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const link = links.find((l) => l.id === f.link)
    if (!link) return props.onError('Choose the decision branch that was observed.')
    try {
      await outcomesApi.record(props.projectId, {
        version_id: chosen, decision_group_id: link.group, transition_id: link.id,
        observed_on: f.observed_on, period_start: f.period_start || null, period_end: f.period_end || null,
        exposure: Number(f.exposure), occurrences: Number(f.occurrences),
        context: f.context.split(',').map((s) => s.trim()).filter(Boolean), notes: f.notes.trim() || null,
      })
      setF({ ...f, exposure: '', occurrences: '', notes: '' })
      props.onSaved()
    } catch (err) {
      props.onError(err instanceof Error ? err.message : String(err))
    }
  }
  const input = (k: keyof typeof f, label: string, type = 'text') => (
    <div className="field"><label htmlFor={`oc-${k}`}>{label}</label>
      <input id={`oc-${k}`} type={type} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
  )
  return (
    <section className="card" aria-labelledby="rec-h">
      <h3 id="rec-h">Record an outcome</h3>
      <form onSubmit={(e) => void submit(e)}>
        <div className="grid2">
          <div className="field"><label htmlFor="oc-version">Version</label>
            <select id="oc-version" value={chosen} onChange={(e) => setVersionId(e.target.value)}>
              {props.versions.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.label}</option>)}
            </select></div>
          <div className="field"><label htmlFor="oc-link">Branch observed</label>
            <select id="oc-link" value={f.link} onChange={(e) => setF({ ...f, link: e.target.value })}>
              <option value="">Choose…</option>
              {links.map((l) => <option key={l.id} value={l.id}>{l.text}</option>)}
            </select></div>
        </div>
        <div className="grid2">{input('exposure', 'Eligible runs (reached the decision)', 'number')}{input('occurrences', 'Runs that took this branch', 'number')}</div>
        <div className="grid2">{input('period_start', 'Period from', 'date')}{input('period_end', 'Period to', 'date')}</div>
        <div className="grid2">{input('observed_on', 'Recorded on', 'date')}{input('context', 'Context tags (comma separated)')}</div>
        {input('notes', 'Notes')}
        <button type="submit" className="primary">Record outcome</button>
      </form>
    </section>
  )
}

function ProposalBox(p: Props) {
  const links = useMemo(() => decisionLinks(p.graph), [p.graph])
  const [link, setLink] = useState('')
  const [context, setContext] = useState('')
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [siblings, setSiblings] = useState<Record<string, { percent: string; assumption: string }>>({})
  const [quality, setQuality] = useState('moderate')
  const [error, setError] = useState<string | null>(null)

  const check = async () => {
    setError(null)
    try { setProposal(await outcomesApi.proposal(p.projectId, link, context)); setSiblings({}) } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  const approve = async () => {
    if (!proposal) return
    const changes: { transition_id: string; new_probability: Estimate }[] = []
    for (const s of proposal.siblings) {
      const row = siblings[s.transition_id]
      if (!row?.percent) continue
      const value = toFraction(row.percent)
      if (value === null || !row.assumption.trim()) { setError('Each sibling change needs a percentage and the assumption behind it.'); return }
      changes.push({ transition_id: s.transition_id, new_probability: { kind: 'point', value, source: { type: 'hypothetical', assumption: row.assumption.trim() }, verification: 'unverified' } })
    }
    try {
      const out = await outcomesApi.apply(p.projectId, { transition_id: proposal.transition_id, context: proposal.context, sibling_changes: changes, evidence_quality: quality, revision: p.revision })
      setProposal(null)
      p.onApplied(out)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  return (
    <section className="card" aria-labelledby="prop-h">
      <h3 id="prop-h">Estimate candidate from outcomes</h3>
      <p className="muted small">Only observations for the same decision and branch, with exactly the context tags below, are used. With fewer than 30 eligible runs the estimate stays unresolved.</p>
      <div className="inline-form">
        <select aria-label="Branch to estimate" value={link} onChange={(e) => setLink(e.target.value)}>
          <option value="">Choose a branch…</option>
          {links.map((l) => <option key={l.id} value={l.id}>{l.text}</option>)}
        </select>
        <input aria-label="Context tags to match" placeholder="Context tags, e.g. web" value={context} onChange={(e) => setContext(e.target.value)} />
        <button type="button" disabled={!link} onClick={() => void check()}>Check</button>
      </div>
      {error && <p className="field-error" role="alert">{error}</p>}
      {proposal && (
        <div className="proposal">
          <p><span className={`tag ${proposal.status === 'candidate' ? 'ok' : 'bad'}`}>{proposal.status === 'candidate' ? 'Candidate' : 'Unresolved'}</span> {proposal.message}</p>
          <ul className="plain-list small">
            <li>Count / denominator: {proposal.occurrences} / {proposal.exposure} ({proposal.used.length} record(s) used)</li>
            <li>Date range: {proposal.date_from ?? '—'} to {proposal.date_to ?? '—'}</li>
            <li>Comparability: {proposal.comparability}</li>
            <li>Current estimate: {proposal.current.kind === 'point' ? pctExact(String(proposal.current.value)) : proposal.current.kind}</li>
            {proposal.excluded.map((x) => <li key={x.outcome_id} className="muted">Excluded {x.outcome_id}: {x.reason}</li>)}
          </ul>
          {proposal.status === 'candidate' && p.canApprove && (
            <>
              {proposal.group_complete && proposal.siblings.length > 0 && (
                <>
                  <p className="small">This decision is complete, so it must still total 100%. Enter the other branches' new estimates explicitly (leave blank to keep):</p>
                  {proposal.siblings.map((s) => (
                    <div className="change-row" key={s.transition_id}>
                      <span className="small">{s.trigger} (now {s.current})</span>
                      <input aria-label={`New percent for ${s.trigger}`} placeholder="%" value={siblings[s.transition_id]?.percent ?? ''} onChange={(e) => setSiblings({ ...siblings, [s.transition_id]: { percent: e.target.value, assumption: siblings[s.transition_id]?.assumption ?? '' } })} />
                      <input aria-label={`Assumption for ${s.trigger}`} placeholder="Assumption behind it" value={siblings[s.transition_id]?.assumption ?? ''} onChange={(e) => setSiblings({ ...siblings, [s.transition_id]: { percent: siblings[s.transition_id]?.percent ?? '', assumption: e.target.value } })} />
                    </div>
                  ))}
                </>
              )}
              <div className="inline-form">
                <label>Evidence quality <select aria-label="Evidence quality" value={quality} onChange={(e) => setQuality(e.target.value)}>
                  {['strong', 'moderate', 'weak', 'anecdotal'].map((q) => <option key={q} value={q}>{q}</option>)}
                </select></label>
                <button type="button" className="primary" disabled={p.dirty} onClick={() => void approve()}>Approve and apply</button>
              </div>
              {p.dirty && <p className="muted small">Save or discard your map changes first.</p>}
            </>
          )}
          {proposal.status === 'candidate' && !p.canApprove && <p className="muted small">An owner or reviewer must approve this before it is applied.</p>}
        </div>
      )}
    </section>
  )
}
