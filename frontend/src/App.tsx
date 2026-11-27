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
        <h1><a href="#/">PathGuard</a></h1>
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
