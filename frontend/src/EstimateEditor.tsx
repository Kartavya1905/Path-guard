import { useState, type FormEvent } from 'react'
import type { Estimate } from './api'
import { toFraction, toPercent } from './prob'

// Edits one link's probability: exact %, a range, or unknown, with where the number came
// from. Nothing is filled in automatically; an unknown stays unknown.

type SourceType = 'measured' | 'expert' | 'hypothetical'
type Form = {
  kind: 'unknown' | 'point' | 'range'
  value: string; low: string; high: string
  source: SourceType
  data_source: string; count: string; denominator: string; date_from: string; date_to: string; context: string
  reviewer: string; rationale: string; assumptions: string; review_date: string
  assumption: string
  reviewed: boolean; reviewed_by: string; reviewed_on: string
}

const str = (v: unknown) => (v == null ? '' : String(v))

function fromEstimate(e: Estimate | undefined): Form {
  const src = (e?.source ?? {}) as Record<string, unknown>
  return {
    kind: e?.kind ?? 'unknown',
    value: e?.kind === 'point' ? toPercent(str(e.value)) : '',
    low: e?.kind === 'range' ? toPercent(str(e.low)) : '',
    high: e?.kind === 'range' ? toPercent(str(e.high)) : '',
    source: (['measured', 'expert', 'hypothetical'].includes(str(src.type)) ? src.type : 'hypothetical') as SourceType,
    data_source: str(src.data_source), count: str(src.count), denominator: str(src.denominator),
    date_from: str(src.date_from), date_to: str(src.date_to), context: str(src.context),
    reviewer: str(src.reviewer), rationale: str(src.rationale),
    assumptions: Array.isArray(src.assumptions) ? (src.assumptions as string[]).join('\n') : '',
    review_date: str(src.review_date), assumption: str(src.assumption),
    reviewed: e?.verification === 'reviewed', reviewed_by: str(e?.reviewed_by), reviewed_on: str(e?.reviewed_on),
  }
}

/** Build the API estimate, or return the problems that stop it from being valid. */
function toEstimate(f: Form): { estimate?: Estimate; errors: string[] } {
  const errors: string[] = []
  if (f.kind === 'unknown') {
    return { estimate: { kind: 'unknown', source: { type: 'unknown' }, verification: 'unverified' }, errors }
  }
  const numbers: Record<string, string> = {}
  if (f.kind === 'point') {
    const v = toFraction(f.value)
    if (v === null) errors.push('Enter a percentage from 0 to 100.')
    else numbers.value = v
  } else {
    const lo = toFraction(f.low)
    const hi = toFraction(f.high)
    if (lo === null || hi === null) errors.push('Enter both range ends as percentages from 0 to 100.')
    else if (Number(lo) >= Number(hi)) errors.push('The low end must be below the high end.')
    else { numbers.low = lo; numbers.high = hi }
  }
  let source: Record<string, unknown> = { type: f.source }
  if (f.source === 'measured') {
    const count = Number(f.count)
    const denominator = Number(f.denominator)
    if (!f.data_source.trim() || !f.context.trim()) errors.push('Measured data needs a data source and its context.')
    if (!Number.isInteger(count) || count < 0 || !Number.isInteger(denominator) || denominator < 1 || f.count === '' || f.denominator === '')
      errors.push('Measured data needs a whole-number count and a sample size of at least 1.')
    else if (count > denominator) errors.push('The count cannot be larger than the sample size.')
    if (!f.date_from || !f.date_to) errors.push('Measured data needs its date range.')
    else if (f.date_from > f.date_to) errors.push('The date range starts after it ends.')
    source = { ...source, data_source: f.data_source.trim(), count, denominator, date_from: f.date_from, date_to: f.date_to, context: f.context.trim() }
  } else if (f.source === 'expert') {
    if (!f.reviewer.trim() || !f.rationale.trim() || !f.review_date) errors.push('An expert estimate needs the reviewer, their rationale and the review date.')
    source = { ...source, reviewer: f.reviewer.trim(), rationale: f.rationale.trim(), review_date: f.review_date,
      assumptions: f.assumptions.split('\n').map((s) => s.trim()).filter(Boolean) }
  } else {
    if (!f.assumption.trim()) errors.push('A hypothetical estimate needs the scenario assumption it rests on.')
    source = { ...source, assumption: f.assumption.trim() }
  }
  if (f.reviewed && (!f.reviewed_by.trim() || !f.reviewed_on)) errors.push('A reviewed estimate needs who reviewed it and when.')
  if (errors.length) return { errors }
  return {
    errors,
    estimate: {
      kind: f.kind, ...numbers, source,
      verification: f.reviewed ? 'reviewed' : 'unverified',
      reviewed_by: f.reviewed ? f.reviewed_by.trim() : null,
      reviewed_on: f.reviewed ? f.reviewed_on : null,
    } as Estimate,
  }
}

export default function EstimateEditor(props: { estimate: Estimate | undefined; disabled: boolean; onApply: (e: Estimate) => void }) {
  const [form, setForm] = useState<Form>(() => fromEstimate(props.estimate))
  const [errors, setErrors] = useState<string[]>([])
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }))

  const apply = (e: FormEvent) => {
    e.preventDefault()
    const result = toEstimate(form)
    setErrors(result.errors)
    if (result.estimate) props.onApply(result.estimate)
  }

  const input = (k: keyof Form, label: string, type = 'text') => (
    <div className="field">
      <label htmlFor={`est-${k}`}>{label}</label>
      <input id={`est-${k}`} type={type} value={form[k] as string} onChange={(e) => set(k, e.target.value as never)} />
    </div>
  )

  return (
    <form className="estimate" onSubmit={apply} aria-label="Probability estimate">
      <fieldset disabled={props.disabled} className="plain">
        <div className="field">
          <label htmlFor="est-kind">Probability</label>
          <select id="est-kind" value={form.kind} onChange={(e) => set('kind', e.target.value as Form['kind'])}>
            <option value="unknown">Unknown (no number)</option>
            <option value="point">Exact percentage</option>
            <option value="range">Range</option>
          </select>
        </div>
        {form.kind === 'point' && input('value', 'Percent (%)')}
        {form.kind === 'range' && <div className="grid2">{input('low', 'Low (%)')}{input('high', 'High (%)')}</div>}
        {form.kind !== 'unknown' && (
          <>
            <div className="field">
              <label htmlFor="est-source">Source</label>
              <select id="est-source" value={form.source} onChange={(e) => set('source', e.target.value as SourceType)}>
                <option value="hypothetical">Hypothetical (scenario assumption)</option>
                <option value="expert">Expert estimate</option>
                <option value="measured">Measured data</option>
              </select>
            </div>
            {form.source === 'hypothetical' && input('assumption', 'Assumption')}
            {form.source === 'expert' && (
              <>
                {input('reviewer', 'Reviewer')}
                {input('rationale', 'Rationale')}
                <div className="field">
                  <label htmlFor="est-assumptions">Assumptions (one per line)</label>
                  <textarea id="est-assumptions" rows={2} value={form.assumptions} onChange={(e) => set('assumptions', e.target.value)} />
                </div>
                {input('review_date', 'Review date', 'date')}
              </>
            )}
            {form.source === 'measured' && (
              <>
                {input('data_source', 'Data source')}
                <div className="grid2">{input('count', 'Count')}{input('denominator', 'Sample size')}</div>
                <div className="grid2">{input('date_from', 'From', 'date')}{input('date_to', 'To', 'date')}</div>
                {input('context', 'Applies when')}
              </>
            )}
            <label className="check">
              <input type="checkbox" checked={form.reviewed} onChange={(e) => set('reviewed', e.target.checked)} />
              Reviewed by a person
            </label>
            {form.reviewed && <div className="grid2">{input('reviewed_by', 'Reviewed by')}{input('reviewed_on', 'Reviewed on', 'date')}</div>}
          </>
        )}
        {errors.length > 0 && <ul className="field-error plain-list" role="alert">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
        <button type="submit">Apply estimate</button>
      </fieldset>
    </form>
  )
}
