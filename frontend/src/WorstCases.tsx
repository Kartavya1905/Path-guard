import { useMemo, useState } from 'react'
import type { Explorer, ProbValue, RiskView, Severity } from './api'
import { OUTCOME_LABEL, STATUS_LABEL } from './graphUtil'
import { pctExact } from './prob'

// Worst-case explorer: risks ranked by impact, then worst-case likelihood. Likelihood,
// impact and evidence are shown side by side, never merged. Filters never hide a
// critical-impact risk unless you untick "critical" yourself.

const IMPACTS: (Severity | 'unset')[] = ['critical', 'high', 'medium', 'low', 'unset']
const EVIDENCE_ORDER = ['none', 'anecdotal', 'weak', 'moderate', 'strong'] as const
const LIKELIHOOD_STEPS = ['0', '0.01', '0.05', '0.1', '0.25', '0.5']
const ENDS: Record<string, string> = {
  goal: 'still reaches the goal', failure: 'ends in failure', retries_exhausted: 'runs out of retries',
}

function show(v: ProbValue | null | undefined): string {
  if (!v) return 'unknown'
  if (v.status === 'exact') return pctExact(v.value)
  if (v.status === 'bounds') return `${pctExact(v.low)}–${pctExact(v.high)}`
  return Number(v.low) === 0 ? 'unknown' : `at least ${pctExact(v.low)}`
}

type Filters = {
  minLikelihood: string
  impacts: Set<string>
  minEvidence: (typeof EVIDENCE_ORDER)[number]
  category: string
  includeSuggested: boolean
}

export default function WorstCases(props: {
  explorer: Explorer | null
  error: string | null
  stale: boolean
  onShow: (riskId: string) => void
}) {
  const [f, setF] = useState<Filters>({
    minLikelihood: '0', impacts: new Set(IMPACTS), minEvidence: 'none', category: '', includeSuggested: true,
  })
  const ex = props.explorer

  const { shown, keptCritical } = useMemo(() => {
    const shown: RiskView[] = []
    let keptCritical = 0
    for (const r of ex?.risks ?? []) {
      if (!f.impacts.has(r.impact ?? 'unset')) continue
      if (f.category && r.category !== f.category) continue
      if (!f.includeSuggested && r.review_status === 'suggested') continue
      const lowLikelihood = Number(r.likelihood.worst_case) < Number(f.minLikelihood)
      const weakEvidence = EVIDENCE_ORDER.indexOf(r.evidence.strength) < EVIDENCE_ORDER.indexOf(f.minEvidence)
      if (lowLikelihood || weakEvidence) {
        if (r.impact !== 'critical') continue
        keptCritical += 1
      }
      shown.push(r)
    }
    return { shown, keptCritical }
  }, [ex, f])

  if (props.error) return <p className="alert error banner" role="alert">{props.error}</p>
  if (!ex) return <p className="muted banner" role="status">Ranking risks…</p>

  const hidden = ex.risks.length - shown.length
  const toggleImpact = (i: string) => setF((x) => {
    const impacts = new Set(x.impacts)
    if (impacts.has(i)) impacts.delete(i); else impacts.add(i)
    return { ...x, impacts }
  })

  return (
    <div className="worst">
      <div className="filters" role="group" aria-label="Filter risks">
        <label>Likelihood (worst case) at least{' '}
          <select value={f.minLikelihood} onChange={(e) => setF({ ...f, minLikelihood: e.target.value })}>
            {LIKELIHOOD_STEPS.map((s) => <option key={s} value={s}>{s === '0' ? 'any' : pctExact(s)}</option>)}
          </select>
        </label>
        <fieldset className="inline-checks">
          <legend>Impact</legend>
          {IMPACTS.map((i) => (
            <label key={i}><input type="checkbox" checked={f.impacts.has(i)} onChange={() => toggleImpact(i)} />{i === 'unset' ? 'not set' : i}</label>
          ))}
        </fieldset>
        <label>Evidence at least{' '}
          <select value={f.minEvidence} onChange={(e) => setF({ ...f, minEvidence: e.target.value as Filters['minEvidence'] })}>
            {EVIDENCE_ORDER.map((s) => <option key={s} value={s}>{s === 'none' ? 'any' : s}</option>)}
          </select>
        </label>
        <label>Cause{' '}
          <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
            <option value="">all</option>
            {ex.categories.map((c) => <option key={c} value={c}>{c.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <label><input type="checkbox" checked={f.includeSuggested} onChange={(e) => setF({ ...f, includeSuggested: e.target.checked })} /> Include suggestions not yet reviewed</label>
      </div>

      <p className="worst-status small" role="status">
        Showing {shown.length} of {ex.risks.length} risks
        {hidden > 0 && ` · ${hidden} hidden by filters`}
        {keptCritical > 0 && ` · ${keptCritical} critical kept visible despite the filters`}
        {ex.excluded_rejected > 0 && ` · ${ex.excluded_rejected} rejected not listed`}
        {props.stale && ' · reflects the last saved map'}
        {!ex.analyzable && ' · the map can’t be analyzed yet, so modeled likelihoods and cascades are unavailable'}
      </p>
      <p className="muted small">{ex.order_note}</p>

      {ex.risks.length === 0 && <p className="muted">No risks yet. Generate the map or add a risk.</p>}
      <ol className="worst-list">
        {shown.map((r) => (
          <li key={r.id} className={r.pinned_critical ? 'critical' : ''}>
            <div className="worst-head">
              <span className="rank">#{r.rank}</span>
              <strong>{r.title}</strong>
              <span className="muted small">{r.category.replaceAll('_', ' ')} · {STATUS_LABEL[r.review_status]}</span>
              <button type="button" className="link" onClick={() => props.onShow(r.id)}>Show on map</button>
            </div>
            <dl className="dims">
              <div><dt>Impact</dt><dd>{r.impact ? <span className={`sev sev-${r.impact}`}>{r.impact}</span> : 'not set'}</dd></div>
              <div><dt>Likelihood</dt><dd>{show(r.likelihood.value)} <span className="muted small">({r.likelihood.basis})</span></dd></div>
              <div><dt>Evidence</dt><dd>{r.evidence.strength === 'none' ? 'none' : `${r.evidence.strength} (${r.evidence.count})`}</dd></div>
            </dl>
            <details>
              <summary>Why this rank</summary>
              <ul className="plain-list small">{r.reasons.map((s) => <li key={s}>{s}</li>)}</ul>
            </details>
            {r.cascade && (
              <details open={r.rank <= 3}>
                <summary>Cascade: what follows if it happens</summary>
                <ol className="cascade small">
                  {r.cascade.steps.map((s, i) => (
                    <li key={`${s.node_id}-${i}`}>
                      {s.via_outcome && <span className={`via via-${s.via_outcome}`}>{OUTCOME_LABEL[s.via_outcome as keyof typeof OUTCOME_LABEL] ?? s.via_outcome} {show(s.probability)}</span>}
                      <span className={`step kind-${s.kind}`}>{s.label}</span>
                    </li>
                  ))}
                </ol>
                <p className="small">
                  {r.cascade.ends_in === 'goal' ? 'No modeled path from here ends badly.' : `The most likely bad path ${ENDS[r.cascade.ends_in] ?? r.cascade.ends_in}.`}{' '}
                  {r.cascade.summary}
                </p>
              </details>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
