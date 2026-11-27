// Form state for a project draft, plus conversion to and from the API shape.
// Every field is a string while editing; blanks become null on save.

import type { DraftIn, DraftOut, Prerequisite, ProjectContext } from './api'
import { newId } from './api'

export type TaskForm = {
  id: string
  title: string
  description: string
  owner: string
  duration_days: string
  cost: string
}

export type DraftForm = {
  title: string
  start: string
  goal: string
  success_criteria: string
  domain: string
  budget: string
  currency: string
  deadline: string
  risk_tolerance: '' | 'low' | 'medium' | 'high'
  resource_limits: string // one per line
  integrations: string
  stakeholders: string
  notes: string
  external_processing_opt_in: boolean
  tasks: TaskForm[] // array order is display order
  prerequisites: Prerequisite[]
}

export const emptyDraft = (): DraftForm => ({
  title: '', start: '', goal: '', success_criteria: '', domain: '', budget: '', currency: '',
  deadline: '', risk_tolerance: '', resource_limits: '', integrations: '', stakeholders: '',
  notes: '', external_processing_opt_in: false, tasks: [], prerequisites: [],
})

export const emptyTask = (): TaskForm => ({
  id: newId('tsk'), title: '', description: '', owner: '', duration_days: '', cost: '',
})

const s = (v: string | null | undefined) => v ?? ''
const lines = (v: string[]) => v.join('\n')
// The API stores 2 decimal places; show "2" or "0.5" rather than "2.00" or "0.50" for days.
const days = (v: string | null) => (v === null ? '' : v.includes('.') ? v.replace(/\.?0+$/, '') : v)

export function fromApi(out: DraftOut): DraftForm {
  const { project, tasks, prerequisites } = out.plan
  const c = project.context
  return {
    title: project.title,
    start: project.start,
    goal: project.goal,
    success_criteria: s(project.success_criteria),
    domain: s(c.domain),
    budget: s(c.budget),
    currency: s(c.currency),
    deadline: s(c.deadline),
    risk_tolerance: c.risk_tolerance ?? '',
    resource_limits: lines(c.resource_limits),
    integrations: lines(c.integrations),
    stakeholders: lines(c.stakeholders),
    notes: s(c.notes),
    external_processing_opt_in: project.external_processing_opt_in,
    tasks: [...tasks]
      .sort((a, b) => a.position - b.position)
      .map((t) => ({
        id: t.id,
        title: t.title,
        description: s(t.description),
        owner: s(t.owner),
        duration_days: days(t.duration_days),
        cost: s(t.cost),
      })),
    prerequisites,
  }
}

const orNull = (v: string) => (v.trim() === '' ? null : v.trim())
const toList = (v: string) => v.split('\n').map((x) => x.trim()).filter(Boolean)

export function toApi(f: DraftForm): DraftIn {
  const context: ProjectContext = {
    domain: orNull(f.domain),
    budget: orNull(f.budget),
    currency: orNull(f.currency)?.toUpperCase() ?? null,
    deadline: orNull(f.deadline),
    resource_limits: toList(f.resource_limits),
    integrations: toList(f.integrations),
    stakeholders: toList(f.stakeholders),
    risk_tolerance: f.risk_tolerance === '' ? null : f.risk_tolerance,
    notes: orNull(f.notes),
  }
  return {
    title: orNull(f.title),
    start: f.start.trim(),
    goal: f.goal.trim(),
    success_criteria: orNull(f.success_criteria),
    context,
    external_processing_opt_in: f.external_processing_opt_in,
    tasks: f.tasks.map((t, position) => ({
      id: t.id,
      title: t.title.trim(),
      description: orNull(t.description),
      position,
      owner: orNull(t.owner),
      duration_days: orNull(t.duration_days),
      cost: orNull(t.cost),
    })),
    prerequisites: f.prerequisites,
  }
}

const AMOUNT = /^\d+(\.\d{1,2})?$/

/** Problems that would make the API reject the save. Keys are field paths. */
export function formErrors(f: DraftForm): Record<string, string> {
  const e: Record<string, string> = {}
  if (!f.start.trim()) e.start = 'Describe where the project starts.'
  if (!f.goal.trim()) e.goal = 'Describe the goal.'
  if (f.budget.trim() && !AMOUNT.test(f.budget.trim())) e.budget = 'Use a number like 1200 or 99.50.'
  if (f.currency.trim().length > 3) e.currency = 'Use a 3-letter code such as USD.'
  f.tasks.forEach((t, i) => {
    if (!t.title.trim()) e[`task.${t.id}.title`] = `Task ${i + 1} needs a title.`
    if (t.duration_days.trim() && !AMOUNT.test(t.duration_days.trim()))
      e[`task.${t.id}.duration_days`] = 'Use a number of days like 3 or 0.5.'
    if (t.cost.trim() && !AMOUNT.test(t.cost.trim()))
      e[`task.${t.id}.cost`] = 'Use a number like 1200 or 99.50.'
  })
  return e
}

/** Move the item at `from` to `to`, returning a new array. */
export function move<T>(items: T[], from: number, to: number): T[] {
  if (to < 0 || to >= items.length || from === to) return items
  const next = [...items]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item as T)
  return next
}

/** Remove a task and every prerequisite that mentions it. */
export function removeTask(f: DraftForm, id: string): DraftForm {
  return {
    ...f,
    tasks: f.tasks.filter((t) => t.id !== id),
    prerequisites: f.prerequisites.filter((p) => p.before_task_id !== id && p.after_task_id !== id),
  }
}

export function addPrerequisite(f: DraftForm, before: string, after: string): DraftForm {
  return {
    ...f,
    prerequisites: [
      ...f.prerequisites,
      { id: newId('pre'), before_task_id: before, after_task_id: after },
    ],
  }
}
