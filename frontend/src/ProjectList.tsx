import { useEffect, useState } from 'react'
import { api, type ProjectSummary } from './api'

export default function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listProjects().then(setProjects, (e: Error) => setError(e.message))
  }, [])

  return (
    <section className="page">
      <div className="page-head">
        <h2>Projects</h2>
        <a className="button primary" href="#/new">New project</a>
      </div>
      {error && <p className="alert error" role="alert">{error}</p>}
      {projects === null && !error && <p className="muted">Loading…</p>}
      {projects?.length === 0 && (
        <p className="muted">No projects yet. Start one with only a starting point and a goal.</p>
      )}
      {projects && projects.length > 0 && (
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id}>
              <a href={`#/p/${p.id}`}>
                <strong>{p.title}</strong>
                <span className="muted">{p.start} → {p.goal}</span>
              </a>
              <span className="tag">{p.role}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
