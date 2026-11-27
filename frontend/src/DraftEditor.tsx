import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, api, type DraftOut, type Issue, type Role } from './api'
import {
  addPrerequisite, emptyDraft, emptyTask, formErrors, fromApi, move, removeTask, toApi,
  type DraftForm, type TaskForm,
} from './draft'

type Props = { projectId: string | null }

type Loaded = { revision: number; role: Role; saved: string }

const CAN_WRITE: Role[] = ['owner', 'editor']

export default function DraftEditor({ projectId }: Props) {
  const [form, setForm] = useState<DraftForm>(emptyDraft)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [loading, setLoading] = useState(projectId !== null)
  const [issues, setIssues] = useState<Issue[]>([])
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [saving, setSaving] = useState(false)
  const [showErrors, setShowErrors] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const apply = useCallback((out: DraftOut) => {
    const next = fromApi(out)
    setForm(next)
    setIssues(out.issues)
    setLoaded({ revision: out.plan.project.revision, role: out.role, saved: JSON.stringify(next) })
    setConflict(false)
    setError(null)
  }, [])

  useEffect(() => {
    if (projectId === null) return
    let live = true
    api.getProject(projectId).then(
      (out) => { if (live) { apply(out); setLoading(false) } },
      (e: Error) => { if (live) { setError(e.message); setLoading(false) } },
    )
    return () => { live = false }
  }, [projectId, apply])

  const dirty = loaded ? JSON.stringify(form) !== loaded.saved : JSON.stringify(form) !== JSON.stringify(emptyDraft())
  const errors = useMemo(() => formErrors(form), [form])
  const canWrite = loaded === null || CAN_WRITE.includes(loaded.role)

  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const set = <K extends keyof DraftForm>(key: K, value: DraftForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }))
  const setTask = (id: string, patch: Partial<TaskForm>) =>
    setForm((f) => ({ ...f, tasks: f.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)) }))

  async function save() {
    setShowErrors(true)
    setNotice(null)
    if (Object.keys(errors).length > 0) {
      setError('Fix the highlighted fields, then save again.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (projectId === null) {
        const out = await api.createProject(toApi(form))
        apply(out)
        window.location.hash = `#/p/${out.plan.project.id}/edit`
      } else {
        const out = await api.saveProject(projectId, toApi(form), loaded?.revision ?? 0)
        apply(out)
        setNotice(`Saved (revision ${out.plan.project.revision}).`)
      }
      setShowErrors(false)
    } catch (e) {
      if (e instanceof ApiError && e.code === 'revision_conflict') setConflict(true)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  async function reloadLatest() {
    if (projectId === null) return
    try {
      apply(await api.getProject(projectId))
      setNotice('Loaded the latest saved version.')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove() {
    if (projectId === null) return
    if (!window.confirm('Delete this project and its scenario map? This cannot be undone.')) return
    try {
      await api.deleteProject(projectId)
      setLoaded(null)
      setForm(emptyDraft())
      window.location.hash = '#/'
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (loading) return <section className="page"><p className="muted">Loading…</p></section>
  if (projectId !== null && loaded === null) {
    return (
      <section className="page">
        <p className="alert error" role="alert">{error ?? 'Project not found.'}</p>
        <a href="#/">Back to projects</a>
      </section>
    )
  }

  const err = (key: string) => (showErrors ? errors[key] : undefined)
  const titles = new Map(form.tasks.map((t, i) => [t.id, t.title.trim() || `Task ${i + 1}`]))

  return (
    <section className="page">
      <div className="page-head">
        <h2>{projectId === null ? 'New project' : form.title || 'Project'}</h2>
        <span className="head-links">
          {projectId !== null && (
            <a href={`#/p/${projectId}`} className="button" onClick={(e) => {
              if (dirty && !window.confirm('Leave without saving your changes?')) e.preventDefault()
            }}>Open workspace</a>
          )}
          <a href="#/" onClick={(e) => {
            if (dirty && !window.confirm('Leave without saving your changes?')) e.preventDefault()
          }}>Back to projects</a>
        </span>
      </div>

      {conflict && (
        <div className="alert warning" role="alert">
          <p>
            Someone saved this project after you opened it. Your changes were not saved, and the
            newer version is kept.
          </p>
          <button type="button" onClick={reloadLatest}>
            Load the latest version (discards your unsaved edits)
          </button>
        </div>
      )}
      {error && !conflict && <p className="alert error" role="alert">{error}</p>}
      {notice && <p className="alert ok" role="status">{notice}</p>}
      {!canWrite && (
        <p className="alert info">Your role ({loaded?.role}) can view this project but not edit it.</p>
      )}

      <form onSubmit={(e) => { e.preventDefault(); void save() }} noValidate>
        <fieldset disabled={!canWrite || saving} className="plain">
          <div className="card">
            <h3>Where you are and where you want to be</h3>
            <Field id="start" label="Starting point" required error={err('start')}>
              <textarea id="start" aria-required rows={2} value={form.start}
                onChange={(e) => set('start', e.target.value)}
                placeholder="e.g. Checkout converts 72% of carts on the legacy payment page" />
            </Field>
            <Field id="goal" label="Goal" required error={err('goal')}>
              <textarea id="goal" aria-required rows={2} value={form.goal}
                onChange={(e) => set('goal', e.target.value)}
                placeholder="e.g. Launch the new checkout without losing conversion" />
            </Field>
            <Field id="title" label="Title" hint="Defaults to the goal if left blank.">
              <input id="title" value={form.title} onChange={(e) => set('title', e.target.value)} />
            </Field>
            <Field id="success_criteria" label="Success criteria">
              <textarea id="success_criteria" rows={2} value={form.success_criteria}
                onChange={(e) => set('success_criteria', e.target.value)} />
            </Field>
          </div>

          <details className="card">
            <summary>Optional context</summary>
            <div className="grid">
              <Field id="domain" label="Domain" hint="“checkout” enables checkout-specific rules later.">
                <input id="domain" list="domains" value={form.domain}
                  onChange={(e) => set('domain', e.target.value)} />
                <datalist id="domains"><option value="checkout" /></datalist>
              </Field>
              <Field id="deadline" label="Deadline">
                <input id="deadline" type="date" value={form.deadline}
                  onChange={(e) => set('deadline', e.target.value)} />
              </Field>
              <Field id="budget" label="Budget" error={err('budget')}>
                <input id="budget" inputMode="decimal" value={form.budget}
                  onChange={(e) => set('budget', e.target.value)} />
              </Field>
              <Field id="currency" label="Currency" error={err('currency')}>
                <input id="currency" maxLength={3} value={form.currency}
                  onChange={(e) => set('currency', e.target.value)} placeholder="USD" />
              </Field>
              <Field id="risk_tolerance" label="Risk tolerance">
                <select id="risk_tolerance" value={form.risk_tolerance}
                  onChange={(e) => set('risk_tolerance', e.target.value as DraftForm['risk_tolerance'])}>
                  <option value="">Not set</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </Field>
            </div>
            <div className="grid">
              <Field id="stakeholders" label="Stakeholders" hint="One per line.">
                <textarea id="stakeholders" rows={3} value={form.stakeholders}
                  onChange={(e) => set('stakeholders', e.target.value)} />
              </Field>
              <Field id="integrations" label="Integrations" hint="One per line.">
                <textarea id="integrations" rows={3} value={form.integrations}
                  onChange={(e) => set('integrations', e.target.value)} />
              </Field>
              <Field id="resource_limits" label="Resource limits" hint="One per line.">
                <textarea id="resource_limits" rows={3} value={form.resource_limits}
                  onChange={(e) => set('resource_limits', e.target.value)} />
              </Field>
            </div>
            <Field id="notes" label="Notes">
              <textarea id="notes" rows={3} value={form.notes}
                onChange={(e) => set('notes', e.target.value)} />
            </Field>
            <label className="check">
              <input type="checkbox" checked={form.external_processing_opt_in}
                onChange={(e) => set('external_processing_opt_in', e.target.checked)} />
              Allow optional external processing for this project
              <span className="muted"> (off by default; nothing leaves this machine unless the
                server setting also allows it)</span>
            </label>
          </details>

          <div className="card">
            <h3>Tasks <span className="muted">(optional)</span></h3>
            <p className="muted small">
              List order is for display only. Prerequisites below decide what must finish first.
            </p>
            {form.tasks.length === 0 && <p className="muted">No tasks yet.</p>}
            <ol className="task-list">
              {form.tasks.map((t, i) => (
                <li key={t.id}>
                  <fieldset className="task" aria-label={`Task ${i + 1}`}>
                    <div className="task-row">
                      <Field id={`${t.id}-title`} label="Title" required error={err(`task.${t.id}.title`)}>
                        <input id={`${t.id}-title`} aria-required value={t.title}
                          onChange={(e) => setTask(t.id, { title: e.target.value })} />
                      </Field>
                      <Field id={`${t.id}-owner`} label="Owner">
                        <input id={`${t.id}-owner`} value={t.owner}
                          onChange={(e) => setTask(t.id, { owner: e.target.value })} />
                      </Field>
                      <Field id={`${t.id}-days`} label="Days" error={err(`task.${t.id}.duration_days`)}>
                        <input id={`${t.id}-days`} inputMode="decimal" value={t.duration_days}
                          onChange={(e) => setTask(t.id, { duration_days: e.target.value })} />
                      </Field>
                      <Field id={`${t.id}-cost`} label="Cost" error={err(`task.${t.id}.cost`)}>
                        <input id={`${t.id}-cost`} inputMode="decimal" value={t.cost}
                          onChange={(e) => setTask(t.id, { cost: e.target.value })} />
                      </Field>
                    </div>
                    <Field id={`${t.id}-desc`} label="Description">
                      <textarea id={`${t.id}-desc`} rows={1} value={t.description}
                        onChange={(e) => setTask(t.id, { description: e.target.value })} />
                    </Field>
                    <div className="row-actions">
                      <button type="button" disabled={i === 0} aria-label={`Move task ${i + 1} up`}
                        onClick={() => set('tasks', move(form.tasks, i, i - 1))}>↑ Up</button>
                      <button type="button" disabled={i === form.tasks.length - 1}
                        aria-label={`Move task ${i + 1} down`}
                        onClick={() => set('tasks', move(form.tasks, i, i + 1))}>↓ Down</button>
                      <button type="button" className="danger" aria-label={`Delete task ${i + 1}`}
                        onClick={() => setForm((f) => removeTask(f, t.id))}>Delete</button>
                    </div>
                  </fieldset>
                </li>
              ))}
            </ol>
            <button type="button" onClick={() => set('tasks', [...form.tasks, emptyTask()])}>
              + Add task
            </button>
          </div>

          {form.tasks.length >= 2 && (
            <PrerequisiteEditor form={form} titles={titles}
              onAdd={(b, a) => setForm((f) => addPrerequisite(f, b, a))}
              onRemove={(id) => set('prerequisites', form.prerequisites.filter((p) => p.id !== id))} />
          )}
        </fieldset>

        {issues.length > 0 && (
          <div className="card" aria-labelledby="issues-h">
            <h3 id="issues-h">Plan checks</h3>
            <p className="muted small">Saved anyway. Resolve these before generating the scenario map.</p>
            <ul className="issues">
              {issues.map((i, n) => (
                <li key={`${i.code}-${n}`} className={i.severity}>
                  <strong>{i.severity === 'error' ? 'Problem' : 'Warning'}:</strong> {i.message}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="save-bar">
          <span className="muted" role="status">
            {saving ? 'Saving…' : dirty ? 'Unsaved changes' : loaded ? `Saved · revision ${loaded.revision}` : ''}
          </span>
          {projectId !== null && loaded?.role === 'owner' && (
            <button type="button" className="danger" onClick={remove}>Delete project</button>
          )}
          {canWrite && (
            <button type="submit" className="primary" disabled={saving || (!dirty && loaded !== null)}>
              {projectId === null ? 'Create project' : 'Save'}
            </button>
          )}
        </div>
      </form>
    </section>
  )
}

function Field(props: {
  id: string; label: string; required?: boolean; hint?: string; error?: string; children: ReactNode
}) {
  return (
    <div className={`field${props.error ? ' invalid' : ''}`}>
      <span className="label-row">
        <label htmlFor={props.id}>{props.label}</label>
        {props.required && <span className="req" aria-hidden> *</span>}
      </span>
      {props.children}
      {props.hint && <span className="muted small">{props.hint}</span>}
      {props.error && <span className="field-error" id={`${props.id}-error`}>{props.error}</span>}
    </div>
  )
}

function PrerequisiteEditor(props: {
  form: DraftForm
  titles: Map<string, string>
  onAdd: (before: string, after: string) => void
  onRemove: (id: string) => void
}) {
  const { form, titles } = props
  const [before, setBefore] = useState('')
  const [after, setAfter] = useState('')
  const valid = before !== '' && after !== '' && before !== after
  const exists = form.prerequisites.some((p) => p.before_task_id === before && p.after_task_id === after)

  return (
    <div className="card">
      <h3>Prerequisites</h3>
      <p className="muted small">Connect tasks explicitly: the first must finish before the second can start.</p>
      <div className="task-row">
        <div className="field">
          <label htmlFor="pre-before">Must finish first</label>
          <select id="pre-before" value={before} onChange={(e) => setBefore(e.target.value)}>
            <option value="">Choose a task</option>
            {form.tasks.map((t) => <option key={t.id} value={t.id}>{titles.get(t.id)}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="pre-after">Before this can start</label>
          <select id="pre-after" value={after} onChange={(e) => setAfter(e.target.value)}>
            <option value="">Choose a task</option>
            {form.tasks.map((t) => <option key={t.id} value={t.id}>{titles.get(t.id)}</option>)}
          </select>
        </div>
        <button type="button" className="align-end" disabled={!valid || exists}
          onClick={() => { props.onAdd(before, after); setBefore(''); setAfter('') }}>
          Add prerequisite
        </button>
      </div>
      {before !== '' && before === after && <p className="field-error">A task cannot depend on itself.</p>}
      {exists && <p className="muted small">That prerequisite already exists.</p>}
      {form.prerequisites.length > 0 && (
        <ul className="prereq-list" aria-label="Prerequisites">
          {form.prerequisites.map((p) => (
            <li key={p.id}>
              <span><strong>{titles.get(p.before_task_id) ?? 'Missing task'}</strong> must finish
                before <strong>{titles.get(p.after_task_id) ?? 'Missing task'}</strong></span>
              <button type="button" className="link" onClick={() => props.onRemove(p.id)}
                aria-label={`Remove prerequisite ${titles.get(p.before_task_id)} before ${titles.get(p.after_task_id)}`}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
