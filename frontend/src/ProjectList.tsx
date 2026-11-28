import { useEffect, useState } from 'react'
import { api, type ProjectSummary } from './api'

function updated(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return days < 30 ? `${days} d ago` : new Date(iso).toLocaleDateString()
}

export default function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listProjects().then(setProjects, (e: Error) => setError(e.message))
  }, [])

  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h2>Projects</h2>
          <p className="page-sub">
            Map what could happen between where a project starts and where it needs to end up: the risks,
            the recoveries, and what a safeguard really changes.
          </p>
        </div>
        <a className="button primary" href="#/new">New project</a>
      </div>
      {error && <p className="alert error" role="alert">{error}</p>}
      {projects === null && !error && <p className="muted" role="status">Loading…</p>}
      {projects?.length === 0 && (
        <div className="empty-state">
          <EmptyArt />
          <h3>No projects yet</h3>
          <p>Start with just a starting point and a goal. Tasks, owners and deadlines can come later.</p>
          <a className="button primary" href="#/new">Create your first project</a>
        </div>
      )}
      {projects && projects.length > 0 && (
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id}>
              <a className="project-card" href={`#/p/${p.id}`}>
                <div className="project-card-top">
                  <strong>{p.title}</strong>
                  {p.title.endsWith('(example)') && <span className="tag example">Example</span>}
                </div>
                <dl className="route">
                  <dt>From</dt><dd>{p.start}</dd>
                  <dt>To</dt><dd>{p.goal}</dd>
                </dl>
                <div className="project-meta">
                  <span className="tag">{p.role}</span>
                  <span>Updated {updated(p.updated_at)}</span>
                </div>
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** A start node branching to a goal and a failure, rejoined by a recovery. */
function EmptyArt() {
  return (
    <svg className="empty-art" viewBox="0 0 120 72" aria-hidden="true" focusable="false">
      <path d="M22 36 C40 36 44 16 62 16 H86" fill="none" stroke="#15803d" strokeWidth="2.5" />
      <path d="M22 36 C40 36 44 56 62 56" fill="none" stroke="#c2261d" strokeWidth="3" />
      <path d="M66 52 C80 44 82 26 86 20" fill="none" stroke="#0369a1" strokeWidth="2.5" strokeDasharray="2 4" strokeLinecap="round" />
      <rect x="6" y="28" width="20" height="16" rx="5" fill="#0f172a" />
      <rect x="84" y="8" width="28" height="16" rx="5" fill="#ecfdf3" stroke="#15803d" strokeWidth="2" />
      <rect x="58" y="48" width="22" height="16" rx="5" fill="#fef2f1" stroke="#c2261d" strokeWidth="2" />
    </svg>
  )
}
