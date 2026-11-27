import type { Analysis, ProbValue } from './api'
import { pct1, pctExact } from './prob'

const OUTCOME_NAMES: Record<string, string> = {
  goal: 'Goal reached',
  failure: 'Ends in failure',
  residual: 'Other modeled outcome',
  retries_exhausted: 'Retries exhausted',
  unmodeled: 'Not modeled yet',
  truncated: 'Cut off by analysis limits',
}

const STATUS_TEXT = {
  exact: 'Exact', bounds: 'Bounds from ranges', partial: 'Partial', blocked: 'Cannot analyze yet',
}

/** "72.7%" (exact 72.675%), "80%–90%", or "at least 85%". */
function Prob({ v }: { v: ProbValue | null | undefined }) {
  if (!v) return <span className="muted">—</span>
  if (v.status === 'exact') {
    return <span title={`Exactly ${pctExact(v.value)}`}>{pct1(v.value)} <span className="muted small">(exact {pctExact(v.value)})</span></span>
  }
  if (v.status === 'bounds') return <span>{pctExact(v.low)} – {pctExact(v.high)}</span>
  if (Number(v.low) === 0) return <span>unknown <span className="muted small">(partial: estimates missing)</span></span>
  return <span>at least {pctExact(v.low)} <span className="muted small">(partial)</span></span>
}

export default function AnalysisPanel(props: {
  analysis: Analysis | null
  loading: boolean
  error: string | null
  stale: boolean
  onPick: (ref: string) => void
}) {
  const { analysis: a, loading, error } = props
  return (
    <section className="panel-section" aria-labelledby="prob-h">
      <h2 id="prob-h">Probability</h2>
      {props.stale && <p className="muted small">Shows the last saved map. Save to update it.</p>}
      {error && <p className="field-error" role="alert">{error}</p>}
      {loading && !a && <p className="muted small" role="status">Calculating…</p>}
      {a && (
        <>
          <p className="small"><span className={`tag analysis-${a.status}`}>{STATUS_TEXT[a.status]}</span></p>
          {a.status === 'blocked' ? (
            <ul className="plain-list small">{a.blockers.map((b) => <li key={b}>{b}</li>)}</ul>
          ) : (
            <>
              <dl className="facts prob-facts">
                <dt>Clean first-pass success</dt><dd><Prob v={a.first_pass} /></dd>
                <dt>Eventual success</dt><dd><Prob v={a.eventual_success} /></dd>
              </dl>
              {a.eventual_success?.note && <p className="muted small">{a.eventual_success.note}</p>}
              <h3 className="sub">Every run ends in exactly one of these</h3>
              <ul className="plain-list small">
                {Object.entries(a.outcomes).map(([k, v]) => <li key={k}>{OUTCOME_NAMES[k] ?? k}: <Prob v={v} /></li>)}
              </ul>
            </>
          )}

          {a.groups.some((g) => g.status !== 'ok') && (
            <>
              <h3 className="sub">Decisions to check</h3>
              <ul className="issues small">
                {a.groups.filter((g) => g.status !== 'ok').map((g) => (
                  <li key={g.node_id} className={g.status === 'invalid' ? 'error' : 'warning'}>
                    {g.message} <button type="button" className="link" onClick={() => props.onPick(g.missing[0] ?? g.node_id)}>Show</button>
                  </li>
                ))}
              </ul>
            </>
          )}
          {a.missing_estimates.length > 0 && (
            <p className="small">
              {a.missing_estimates.length} link(s) still need an estimate.{' '}
              <button type="button" className="link" onClick={() => props.onPick(a.missing_estimates[0] as string)}>Show the first</button>
            </p>
          )}

          {a.paths.length > 0 && (
            <details className="small">
              <summary>Most likely paths ({a.limits.paths_shown} of {a.limits.paths_found})</summary>
              <p className="muted">Examples only. The totals above come from the full calculation, not from adding these paths.</p>
              <ol className="plain-list numbered">
                {a.paths.map((p) => (
                  <li key={p.transitions.join()}>
                    <Prob v={p.probability} /> · {OUTCOME_NAMES[p.ends_in] ?? p.ends_in}{p.uses_recovery ? ' · with recovery' : ''}
                    <div className="muted">{p.labels.join(' → ')}</div>
                  </li>
                ))}
              </ol>
            </details>
          )}
          <p className="muted small">
            Limits: depth {a.limits.max_depth}, {a.limits.expansions_used} of {a.limits.max_expansions} states explored
            {a.limits.truncated ? ' (limit reached: results are partial)' : ''}
            {a.limits.paths_omitted > 0 ? `, ${a.limits.paths_omitted} lower-probability paths not listed` : ''}
            {a.limits.path_search_truncated ? ', path list cut off by limits' : ''}.
          </p>
          <details className="small">
            <summary>How this is calculated</summary>
            <ul className="plain-list">{a.assumptions.map((s) => <li key={s}>{s}</li>)}</ul>
          </details>
        </>
      )}
    </section>
  )
}
