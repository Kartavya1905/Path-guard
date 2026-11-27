import { useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  newMapId, versionsApi, type Comparison, type Delta, type ProbValue, type ScenarioVersion,
  type Side, type VersionSummary,
} from './api'
import { pct1, pctExact, toFraction } from './prob'

// Saved versions: save the map as a baseline, create a variant by applying a mitigation
// (on the server, so the baseline never changes), and compare the two side by side.

type Props = {
  projectId: string
  revision: number
  dirty: boolean
  canPublish: boolean
  onSaved: () => void
}

type ChangeRow = { transition_id: string; percent: string; assumption: string }

function prob(v: ProbValue | null | undefined): string {
  if (!v) return '—'
  if (v.status === 'exact') return `${pct1(v.value)} (exact ${pctExact(v.value)})`
  if (v.status === 'bounds') return `${pctExact(v.low)}–${pctExact(v.high)}`
  return Number(v.low) === 0 ? 'unknown (partial)' : `at least ${pctExact(v.low)} (partial)`
}

function delta(d: Delta): string {
  if (d.comparable && d.pp !== null) return `${d.pp.startsWith('-') ? '' : '+'}${d.pp} pp`
  return 'not comparable'
}

export default function VersionsPanel(p: Props) {
  const [versions, setVersions] = useState<VersionSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const [baseId, setBaseId] = useState('')
  const [variantId, setVariantId] = useState('')
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [label, setLabel] = useState('Baseline')

  useEffect(() => {
    let live = true
    versionsApi.list(p.projectId).then((vs) => {
      if (!live) return
      setVersions(vs)
      const variant = [...vs].reverse().find((v) => v.kind === 'variant')
      if (variant?.parent_version_id) { setBaseId(variant.parent_version_id); setVariantId(variant.id) }
      else if (vs[0]) setBaseId(vs[0].id)
    }, (e: Error) => { if (live) setError(e.message) })
    return () => { live = false }
  }, [p.projectId, reload])

  useEffect(() => {
    if (!baseId || !variantId || baseId === variantId) return
    let live = true
    versionsApi.compare(p.projectId, baseId, variantId).then(
      (c) => { if (live) { setComparison(c); setError(null) } },
      (e: Error) => { if (live) setError(e.message) })
    return () => { live = false }
  }, [p.projectId, baseId, variantId])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null); setNotice(null)
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  const saveBaseline = (e: FormEvent) => {
    e.preventDefault()
    void run(async () => {
      const v = await versionsApi.saveBaseline(p.projectId, label.trim() || 'Baseline', p.revision)
      setNotice(`Saved version ${v.number} (“${v.label}”) as a baseline. It can't be changed.`)
      setBaseId(v.id)
      setReload((n) => n + 1)
      p.onSaved()
    })
  }

  const byId = new Map((versions ?? []).map((v) => [v.id, v]))
  const shown = comparison && baseId && variantId && baseId !== variantId ? comparison : null

  return (
    <div className="versions">
      {error && <p className="alert error" role="alert">{error}</p>}
      {notice && <p className="alert ok" role="status">{notice}</p>}

      <section className="card">
        <h3>Saved versions</h3>
        {versions === null ? <p className="muted">Loading…</p> : versions.length === 0 ? (
          <p className="muted">No versions yet. Save the current map as a baseline to compare against.</p>
        ) : (
          <ol className="plain-list version-list">
            {versions.map((v) => (
              <li key={v.id}>
                <strong>v{v.number} · {v.label}</strong> <span className="tag">{v.kind}</span>
                {v.parent_version_id && <span className="muted small"> from v{byId.get(v.parent_version_id)?.number ?? '?'}</span>}
                <span className="muted small"> · {new Date(v.created_at).toLocaleString()}</span>
              </li>
            ))}
          </ol>
        )}
        {p.canPublish && (
          <form className="inline-form" onSubmit={saveBaseline}>
            <input aria-label="Baseline name" value={label} onChange={(e) => setLabel(e.target.value)} />
            <button type="submit" className="primary" disabled={busy || p.dirty}>Save current map as baseline</button>
          </form>
        )}
        {p.dirty && <p className="muted small">Save or discard your map changes first.</p>}
      </section>

      {p.canPublish && versions && versions.length > 0 && (
        <VariantForm projectId={p.projectId} versions={versions} busy={busy} run={run}
          onCreated={(v) => { setNotice(`Created variant v${v.number} (“${v.label}”). The baseline is unchanged.`); setBaseId(v.parent_version_id ?? ''); setVariantId(v.id); setReload((n) => n + 1) }} />
      )}

      {versions && versions.length > 1 && (
        <section className="card" aria-labelledby="cmp-h">
          <h3 id="cmp-h">Compare</h3>
          <div className="inline-form">
            <label>Baseline <select aria-label="Compare baseline" value={baseId} onChange={(e) => setBaseId(e.target.value)}>
              {versions.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.label}</option>)}
            </select></label>
            <label>Variant <select aria-label="Compare variant" value={variantId} onChange={(e) => setVariantId(e.target.value)}>
              <option value="">Choose…</option>
              {versions.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.label}</option>)}
            </select></label>
          </div>
          {shown && <ComparisonView c={shown} base={byId.get(baseId)} variant={byId.get(variantId)} />}
        </section>
      )}
    </div>
  )
}

function ComparisonView({ c, base, variant }: { c: Comparison; base?: VersionSummary; variant?: VersionSummary }) {
  const rows: [string, (s: Side) => string][] = [
    ['Clean first-pass success', (s) => prob(s.first_pass)],
    ['Eventual success', (s) => prob(s.eventual_success)],
    ['Analysis', (s) => s.status],
    ['Severe risks (critical or high)', (s) => s.severe_risks.length ? `${s.severe_risks.length}: ${s.severe_risks.join(', ')}` : 'none'],
    ['Mitigation cost', (s) => s.mitigation_cost ?? '—'],
    ['Mitigation delay (days)', (s) => s.mitigation_delay_days ?? '—'],
    ['Task cost / days', (s) => `${s.task_cost ?? '—'} / ${s.task_days ?? '—'}`],
    ['Open assumptions', (s) => s.open_assumptions.length ? s.open_assumptions.join('; ') : 'none'],
    ['Hypothetical estimates', (s) => String(s.hypothetical_estimates)],
    ['Links without estimates', (s) => String(s.missing_estimates)],
    ['Mitigations', (s) => s.mitigations.join(', ') || 'none'],
  ]
  return (
    <div className="comparison">
      <div className="delta-cards">
        <div className={`delta-card${c.first_pass_delta.comparable ? '' : ' muted-card'}`}>
          <span className="small">Clean first-pass success</span>
          <strong>{delta(c.first_pass_delta)}</strong>
          <span className="small muted">{c.first_pass_delta.note}</span>
        </div>
        <div className={`delta-card${c.eventual_delta.comparable ? '' : ' muted-card'}`}>
          <span className="small">Eventual success</span>
          <strong>{delta(c.eventual_delta)}</strong>
          <span className="small muted">{c.eventual_delta.note}</span>
        </div>
      </div>
      {c.flags.length > 0 && (
        <ul className="issues small">{c.flags.map((f) => <li key={f} className="warning">{f}</li>)}</ul>
      )}
      <table className="cmp-table">
        <thead><tr><th scope="col"></th><th scope="col">{base ? `v${base.number} · ${base.label}` : 'Baseline'}</th><th scope="col">{variant ? `v${variant.number} · ${variant.label}` : 'Variant'}</th></tr></thead>
        <tbody>{rows.map(([name, get]) => <tr key={name}><th scope="row">{name}</th><td>{get(c.baseline)}</td><td>{get(c.variant)}</td></tr>)}</tbody>
      </table>
      <h4>Changed branches ({c.changed_branches.length})</h4>
      {c.changed_branches.length === 0 ? <p className="muted small">No branch differs.</p> : (
        <table className="cmp-table small">
          <thead><tr><th scope="col">Link</th><th scope="col">Change</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
          <tbody>{c.changed_branches.map((b) => (
            <tr key={b.transition_id} className={`chg-${b.change}`}><td>{b.label}</td><td>{b.change}{b.fields.length ? ` (${b.fields.join(', ')})` : ''}</td><td>{b.before ?? '—'}</td><td>{b.after ?? '—'}</td></tr>
          ))}</tbody>
        </table>
      )}
    </div>
  )
}

function VariantForm(props: {
  projectId: string; versions: VersionSummary[]; busy: boolean
  run: (fn: () => Promise<void>) => Promise<void>
  onCreated: (v: ScenarioVersion) => void
}) {
  const [parentId, setParentId] = useState(props.versions.find((v) => v.kind === 'baseline')?.id ?? props.versions[0]?.id ?? '')
  const [parent, setParent] = useState<ScenarioVersion | null>(null)
  const [f, setF] = useState({ label: '', title: '', rationale: '', expected: '', owner: '', cost: '', delay: '', assumptions: '' })
  const [rows, setRows] = useState<ChangeRow[]>([{ transition_id: '', percent: '', assumption: '' }])
  const [problems, setProblems] = useState<string[]>([])

  useEffect(() => {
    if (!parentId) return
    let live = true
    versionsApi.get(props.projectId, parentId).then((v) => { if (live) setParent(v) }, () => undefined)
    return () => { live = false }
  }, [props.projectId, parentId])

  const links = useMemo(() => {
    if (!parent) return []
    const label = new Map(parent.graph.nodes.map((n) => [n.id, n.label]))
    return parent.graph.transitions
      .filter((t) => t.review_status === 'accepted' || t.review_status === 'edited')
      .map((t) => ({ id: t.id, text: `${label.get(t.source_node_id)} → ${label.get(t.target_node_id)} (${t.trigger})`, current: t.probability }))
  }, [parent])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const errs: string[] = []
    if (!f.label.trim() || !f.title.trim() || !f.expected.trim()) errs.push('Give the variant a name, and the mitigation a title and expected effect.')
    const changes = rows.filter((r) => r.transition_id).map((r) => {
      const value = toFraction(r.percent)
      if (value === null) errs.push('Each estimate change needs a percentage from 0 to 100.')
      if (!r.assumption.trim()) errs.push('Each estimate change needs the assumption behind it.')
      return { transition_id: r.transition_id, new_probability: { kind: 'point' as const, value, source: { type: 'hypothetical', assumption: r.assumption.trim() }, verification: 'unverified' } }
    })
    for (const n of [f.cost, f.delay]) if (n.trim() && !/^\d+(\.\d{1,2})?$/.test(n.trim())) errs.push('Cost and delay must be numbers like 1200 or 1.5.')
    setProblems(errs)
    if (errs.length) return
    void props.run(async () => {
      const v = await versionsApi.createVariant(props.projectId, parentId, f.label.trim(), {
        id: newMapId('mit'), title: f.title.trim(), target_kind: 'transition',
        target_id: changes[0]?.transition_id ?? links[0]?.id ?? '', rationale: f.rationale.trim(),
        owner: f.owner.trim() || null, expected_effect: f.expected.trim(),
        cost: f.cost.trim() || null, delay_days: f.delay.trim() || null,
        assumptions: f.assumptions.split('\n').map((s) => s.trim()).filter(Boolean),
        estimate_changes: changes,
      })
      props.onCreated(v)
    })
  }

  const field = (k: keyof typeof f, label: string) => (
    <div className="field"><label htmlFor={`mit-${k}`}>{label}</label>
      <input id={`mit-${k}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
  )

  return (
    <section className="card" aria-labelledby="var-h">
      <h3 id="var-h">Create a variant with a mitigation</h3>
      <p className="muted small">The variant is a new, separate version. The version you start from never changes. If a change breaks a complete decision's 100% total, change its sibling estimates explicitly too.</p>
      <form onSubmit={submit}>
        <div className="field"><label htmlFor="mit-parent">Start from</label>
          <select id="mit-parent" value={parentId} onChange={(e) => setParentId(e.target.value)}>
            {props.versions.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.label}</option>)}
          </select></div>
        <div className="grid2">{field('label', 'Variant name')}{field('title', 'Mitigation')}</div>
        {field('expected', 'Expected effect')}
        {field('rationale', 'Why it helps')}
        <div className="grid2">{field('owner', 'Owner')}{field('cost', 'Cost')}</div>
        <div className="grid2">{field('delay', 'Delay (days)')}{field('assumptions', 'Assumption')}</div>
        <h4>Estimate changes</h4>
        {rows.map((r, i) => (
          <div className="change-row" key={i}>
            <select aria-label={`Link ${i + 1}`} value={r.transition_id} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, transition_id: e.target.value } : x))}>
              <option value="">Choose a link…</option>
              {links.map((l) => <option key={l.id} value={l.id}>{l.text}</option>)}
            </select>
            <input aria-label={`New percent ${i + 1}`} placeholder="%" value={r.percent} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, percent: e.target.value } : x))} />
            <input aria-label={`Assumption ${i + 1}`} placeholder="Assumption behind it" value={r.assumption} onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, assumption: e.target.value } : x))} />
          </div>
        ))}
        <div className="row-actions start">
          <button type="button" onClick={() => setRows([...rows, { transition_id: '', percent: '', assumption: '' }])}>+ Another change</button>
          <button type="submit" className="primary" disabled={props.busy}>Create variant</button>
        </div>
        {problems.length > 0 && <ul className="field-error plain-list" role="alert">{problems.map((x) => <li key={x}>{x}</li>)}</ul>}
      </form>
    </section>
  )
}
