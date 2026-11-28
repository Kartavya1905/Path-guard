import { useEffect, useState } from 'react'
import DraftEditor from './DraftEditor'
import ProjectList from './ProjectList'
import ReportPage from './ReportPage'
import Workspace from './Workspace'

type Health = { state: 'pending' } | { state: 'ok' } | { state: 'down'; detail: string }

// Hash routes: #/ (projects), #/new, #/p/<id> (workspace), #/p/<id>/edit (plan form)
type Route =
  | { page: 'list' } | { page: 'edit'; id: string | null }
  | { page: 'workspace'; id: string } | { page: 'report'; id: string }

function parse(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  if (path === 'new') return { page: 'edit', id: null }
  const m = /^p\/([a-z]{3}_[A-Za-z0-9]{8,40})(\/edit|\/report)?$/.exec(path)
  if (!m?.[1]) return { page: 'list' }
  if (m[2] === '/edit') return { page: 'edit', id: m[1] }
  return m[2] === '/report' ? { page: 'report', id: m[1] } : { page: 'workspace', id: m[1] }
}

export default function App() {
  const [health, setHealth] = useState<Health>({ state: 'pending' })
  const [retention, setRetention] = useState<number | null>(null)  // set in the public demo
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))

  useEffect(() => {
    const onHash = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    fetch('/api/health')
      .then(async (res) => {
        const body = (await res.json()) as {
          database?: string; identity?: string; visitor_retention_days?: number | null
        }
        if (body.identity === 'visitor') setRetention(body.visitor_retention_days ?? 7)
        setHealth(res.ok ? { state: 'ok' } : { state: 'down', detail: body.database ?? 'error' })
      })
      .catch(() => setHealth({ state: 'down', detail: 'API unreachable' }))
  }, [])

  const label =
    health.state === 'ok' ? 'API ready' : health.state === 'down' ? `API: ${health.detail}` : 'Checking API…'

  return (
    <>
      <header className="app-header no-print">
        <h1 className="brand">
          <a href="#/">
            <BrandMark />
            <span>PathGuard</span>
            <span className="brand-tag">Project risk planner</span>
          </a>
        </h1>
        <nav className="header-nav" aria-label="Main">
          <a href="#/" aria-current={route.page === 'list' ? 'page' : undefined}>Projects</a>
          <a href="#/new" aria-current={route.page === 'edit' && route.id === null ? 'page' : undefined}>New project</a>
        </nav>
        <span className={`badge ${health.state}`} role="status">
          {label}
        </span>
      </header>
      {retention !== null && (
        <p className="demo-banner no-print" role="note">
          Public demo: your projects are private to this browser and are deleted after{' '}
          {retention} days without changes. Please don't enter real or sensitive data.
        </p>
      )}
      <main className={route.page === 'workspace' ? (retention !== null ? 'full with-banner' : 'full') : undefined}>
        {route.page === 'list' && <ProjectList />}
        {route.page === 'edit' && <DraftEditor key={route.id ?? 'new'} projectId={route.id} />}
        {route.page === 'workspace' && <Workspace key={route.id} projectId={route.id} />}
        {route.page === 'report' && <ReportPage key={route.id} projectId={route.id} />}
      </main>
    </>
  )
}

/** Logo: a shield with a path that branches and rejoins (the scenario map in miniature). */
function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="pg-shield" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
      </defs>
      <path d="M16 2.5 4.5 7v8.2c0 7 4.9 12.4 11.5 14.3 6.6-1.9 11.5-7.3 11.5-14.3V7L16 2.5Z" fill="url(#pg-shield)" />
      <path d="M10 21.5c2.2 0 3-1.6 3.6-3.5l1.2-4c.6-1.9 1.4-3.5 3.6-3.5H22" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M13.8 17.6c.9 2.4 2.6 3.9 5.6 3.9H22" fill="none" stroke="#c7d2fe" strokeWidth="2.2" strokeLinecap="round" strokeDasharray="0.1 3.4" />
      <circle cx="10" cy="21.5" r="1.9" fill="#fff" />
      <circle cx="22.5" cy="10.5" r="1.9" fill="#86efac" />
    </svg>
  )
}
