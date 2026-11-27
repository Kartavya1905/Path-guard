// Typed client for the PathGuard API. Shapes mirror backend/app/schemas.py and
// backend/pathguard_engine/model.py. Decimals travel as strings.

export type Role = 'owner' | 'editor' | 'reviewer' | 'viewer'

export type ProjectContext = {
  domain: string | null
  budget: string | null
  currency: string | null
  deadline: string | null
  resource_limits: string[]
  integrations: string[]
  stakeholders: string[]
  risk_tolerance: 'low' | 'medium' | 'high' | null
  notes: string | null
}

export type Project = {
  id: string
  owner_id: string
  title: string
  start: string
  goal: string
  success_criteria: string | null
  context: ProjectContext
  revision: number
  external_processing_opt_in: boolean
}

export type Task = {
  id: string
  title: string
  description: string | null
  position: number
  owner: string | null
  duration_days: string | null
  cost: string | null
}

export type Prerequisite = { id: string; before_task_id: string; after_task_id: string }

export type Issue = {
  code: string
  severity: 'error' | 'warning'
  message: string
  refs: string[]
}

export type DraftOut = {
  plan: { project: Project; tasks: Task[]; prerequisites: Prerequisite[] }
  issues: Issue[]
  role: Role
}

export type DraftIn = {
  title: string | null
  start: string
  goal: string
  success_criteria: string | null
  context: ProjectContext
  external_processing_opt_in: boolean
  tasks: Task[]
  prerequisites: Prerequisite[]
}

export type ProjectSummary = {
  id: string
  title: string
  start: string
  goal: string
  revision: number
  role: Role
  updated_at: string
}

/** Error carrying the API's status and its structured `detail`. */
export class ApiError extends Error {
  status: number
  code: string | null
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(describe(status, detail))
    this.status = status
    this.detail = detail
    this.code =
      detail && typeof detail === 'object' && 'code' in detail ? String(detail.code) : null
  }
}

function describe(status: number, detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg}, ...]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        `${(d.loc ?? []).filter((p) => p !== 'body').join('.')}: ${d.msg ?? 'invalid'}`)
      .join('; ')
  }
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: string; issues?: Issue[] }
    if (d.message) return d.message
    if (d.issues?.length) return d.issues.map((i) => i.message).join('; ')
  }
  if (status === 404) return 'Project not found, or you do not have access to it.'
  if (status === 403) return 'Your role on this project does not allow that.'
  return `Request failed (${status})`
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (res.status === 204) return undefined as T
  const payload: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? payload.detail
      : payload
    throw new ApiError(res.status, detail)
  }
  return payload as T
}

export const api = {
  listProjects: () => request<ProjectSummary[]>('GET', '/projects'),
  createProject: (draft: DraftIn) => request<DraftOut>('POST', '/projects', draft),
  getProject: (id: string) => request<DraftOut>('GET', `/projects/${encodeURIComponent(id)}`),
  saveProject: (id: string, draft: DraftIn, revision: number) =>
    request<DraftOut>('PUT', `/projects/${encodeURIComponent(id)}`, { ...draft, revision }),
  deleteProject: (id: string) => request<void>('DELETE', `/projects/${encodeURIComponent(id)}`),
}

/** Random prefixed ID matching the engine's ID pattern (e.g. `tsk_` + 16 hex). */
export function newId(prefix: 'tsk' | 'pre'): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8))
  return `${prefix}_${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`
}

// --------------------------------------------------------------------------- scenario map

export type ReviewStatus = 'suggested' | 'accepted' | 'edited' | 'rejected'
export type Origin = 'user' | 'rule' | 'ai'
export type Severity = 'low' | 'medium' | 'high' | 'critical'
export type NodeKind =
  'start' | 'step' | 'success' | 'failure' | 'delay' | 'recovery' | 'goal' | 'residual'
export type Outcome = 'success' | 'failure' | 'delay' | 'recovery' | 'residual'
export type TargetKind = 'node' | 'transition' | 'risk' | 'task' | 'decision_group' | 'mitigation'

/** Probability estimate; edited in the probability step, carried through untouched here. */
export type Estimate = { kind: 'point' | 'range' | 'unknown'; [key: string]: unknown }

export type ScenarioNode = {
  id: string
  kind: NodeKind
  label: string
  description?: string | null
  task_id?: string | null
  terminal: boolean
  severity?: Severity | null
  origin: Origin
  review_status: ReviewStatus
  layout?: { x: number; y: number } | null
  collapsed: boolean
  rule_key?: string | null
}

export type Transition = {
  id: string
  source_node_id: string
  target_node_id: string
  outcome: Outcome
  trigger: string
  condition?: string | null
  decision_group_id?: string | null
  probability?: Estimate
  max_traversals?: number | null
  cost?: string | null
  delay_days?: string | null
  origin: Origin
  review_status: ReviewStatus
  rule_key?: string | null
}

export type DecisionGroup = {
  id: string
  node_id: string
  label: string
  exclusive: boolean
  complete: boolean
  residual_transition_id?: string | null
  origin: Origin
  review_status: ReviewStatus
  rule_key?: string | null
}

export type RiskCandidate = {
  id: string
  category: string
  title: string
  trigger: string
  affected_task_id?: string | null
  affected_node_ids: string[]
  rationale: string
  consequence: string
  mitigation_hint?: string | null
  impact?: Severity | null
  likelihood?: Estimate
  origin: Origin
  rule_id?: string | null
  dedupe_key?: string | null
  review_status: ReviewStatus
}

export type Evidence = {
  id: string
  title: string
  kind: 'dataset' | 'incident' | 'document' | 'interview' | 'test_result' | 'other'
  source_ref?: string | null
  summary?: string | null
  quality: 'strong' | 'moderate' | 'weak' | 'anecdotal'
  collected_on?: string | null
  sample_size?: number | null
}

export type EvidenceLink = {
  id: string; evidence_id: string; target_kind: TargetKind; target_id: string; note?: string | null
}

export type Assumption = {
  id: string
  text: string
  target_kind?: TargetKind | null
  target_id?: string | null
  owner?: string | null
  status: 'open' | 'validated' | 'invalidated'
}

export type Mitigation = {
  id: string
  title: string
  target_kind: TargetKind
  target_id: string
  rationale: string
  owner?: string | null
  expected_effect: string
  cost?: string | null
  delay_days?: string | null
  assumptions: string[]
  estimate_changes: unknown[]
}

export type ReviewerNote = {
  id: string; target_kind: TargetKind; target_id: string; author_id: string; body: string
  created_at: string
}

export type ScenarioGraph = {
  nodes: ScenarioNode[]
  transitions: Transition[]
  decision_groups: DecisionGroup[]
  risks: RiskCandidate[]
  evidence: Evidence[]
  evidence_links: EvidenceLink[]
  assumptions: Assumption[]
  mitigations: Mitigation[]
  notes: ReviewerNote[]
}

export type Readiness = {
  issues: Issue[]
  can_analyze: boolean
  can_mark_reviewed: boolean
  pending_suggestions: number
  blockers: string[]
}

export type GraphOut = { graph: ScenarioGraph; revision: number; readiness: Readiness }
export type GenerateOut = GraphOut & {
  added: string[]; refreshed: string[]; removed: string[]; preserved: string[]
}
export type Me = { id: string; display_name: string; demo: boolean }

const pid = (id: string) => `/projects/${encodeURIComponent(id)}`

export const mapApi = {
  me: () => request<Me>('GET', '/me'),
  get: (id: string) => request<GraphOut>('GET', `${pid(id)}/graph`),
  save: (id: string, graph: ScenarioGraph, revision: number) =>
    request<GraphOut>('PUT', `${pid(id)}/graph`, { graph, revision }),
  generate: (id: string, revision: number) =>
    request<GenerateOut>('POST', `${pid(id)}/graph/generate`, { revision }),
  review: (id: string, riskId: string, decision: 'accept' | 'reject', modelBranch: boolean,
    revision: number) =>
    request<GraphOut>('POST', `${pid(id)}/risks/${encodeURIComponent(riskId)}/review`,
      { decision, model_branch: modelBranch, revision }),
}

/** Random prefixed ID for items created in the map editor. */
export function newMapId(prefix: 'nod' | 'trn' | 'dgp' | 'rsk' | 'evd' | 'evl' | 'asm' | 'mit' | 'not'): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8))
  return `${prefix}_${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`
}

// --------------------------------------------------------------------------- analysis

export type ProbValue = {
  status: 'exact' | 'bounds' | 'partial'; value?: string | null; low: string; high: string; note: string
}
export type GroupCheck = {
  node_id: string; group_id: string | null; label: string
  status: 'ok' | 'unresolved' | 'invalid'; complete: boolean
  total_low: string; total_high: string; missing: string[]
  unmodeled_low: string; unmodeled_high: string; message: string
}
export type PathResult = {
  transitions: string[]; nodes: string[]; labels: string[]; probability: ProbValue
  ends_in: string; uses_recovery: boolean
}
export type Analysis = {
  status: 'exact' | 'bounds' | 'partial' | 'blocked'
  blockers: string[]
  first_pass: ProbValue | null
  eventual_success: ProbValue | null
  outcomes: Record<string, ProbValue>
  groups: GroupCheck[]
  missing_estimates: string[]
  paths: PathResult[]
  limits: {
    max_depth: number; max_expansions: number; max_results: number; expansions_used: number
    truncated: boolean; paths_found: number; paths_shown: number; paths_omitted: number
    path_search_truncated: boolean
  }
  assumptions: string[]
  issues: Issue[]
}

export const analysisApi = {
  get: (id: string) => request<Analysis>('GET', `/projects/${encodeURIComponent(id)}/analysis`),
}

// --------------------------------------------------------------------------- worst cases

export type Likelihood = {
  basis: 'modeled' | 'estimate' | 'unknown'; value: ProbValue | null; worst_case: string; note: string
}
export type CascadeStep = {
  node_id: string; label: string; kind: string
  via_outcome: string | null; via_trigger: string | null; probability: ProbValue | null
}
export type RiskView = {
  id: string; title: string; category: string; trigger: string; consequence: string
  review_status: ReviewStatus; origin: Origin; impact: Severity | null; affected_node_ids: string[]
  likelihood: Likelihood
  evidence: { strength: 'strong' | 'moderate' | 'weak' | 'anecdotal' | 'none'; count: number; titles: string[] }
  rank: number; reasons: string[]; pinned_critical: boolean; modeled_branch: boolean
  cascade: { steps: CascadeStep[]; ends_in: string; outcomes_if_it_happens: Record<string, ProbValue>; summary: string } | null
}
export type Explorer = {
  risks: RiskView[]; total: number; excluded_rejected: number; analyzable: boolean
  categories: string[]; order_note: string
}

export const explorerApi = {
  get: (id: string) => request<Explorer>('GET', `/projects/${encodeURIComponent(id)}/explorer`),
}

// --------------------------------------------------------------------------- versions

export type VersionSummary = {
  id: string; number: number; label: string; kind: 'baseline' | 'variant' | 'snapshot'
  parent_version_id: string | null; created_by: string; created_at: string; content_hash: string
}
export type ScenarioVersion = VersionSummary & {
  project_id: string; plan: DraftOut['plan']; graph: ScenarioGraph
}
export type Side = {
  status: Analysis['status']; first_pass: ProbValue | null; eventual_success: ProbValue | null
  severe_risks: string[]; open_assumptions: string[]; hypothetical_estimates: number
  missing_estimates: number; mitigation_cost: string | null; mitigation_delay_days: string | null
  task_cost: string | null; task_days: string | null; mitigations: string[]
}
export type Delta = { comparable: boolean; pp: string | null; low_pp: string | null; high_pp: string | null; note: string }
export type Comparison = {
  baseline: Side; variant: Side; first_pass_delta: Delta; eventual_delta: Delta
  changed_branches: { transition_id: string; label: string; change: 'added' | 'removed' | 'changed'; before: string | null; after: string | null; fields: string[] }[]
  added_nodes: string[]; removed_nodes: string[]; flags: string[]
}
export type MitigationIn = {
  id: string; title: string; target_kind: TargetKind; target_id: string; rationale: string
  owner: string | null; expected_effect: string; cost: string | null; delay_days: string | null
  assumptions: string[]; estimate_changes: { transition_id: string; new_probability: Estimate }[]
}

const vp = (id: string) => `/projects/${encodeURIComponent(id)}`
export const versionsApi = {
  list: (id: string) => request<VersionSummary[]>('GET', `${vp(id)}/versions`),
  get: (id: string, vid: string) => request<ScenarioVersion>('GET', `${vp(id)}/versions/${encodeURIComponent(vid)}`),
  saveBaseline: (id: string, label: string, revision: number) =>
    request<ScenarioVersion>('POST', `${vp(id)}/versions`, { label, kind: 'baseline', revision }),
  createVariant: (id: string, vid: string, label: string, mitigation: MitigationIn) =>
    request<ScenarioVersion>('POST', `${vp(id)}/versions/${encodeURIComponent(vid)}/variants`, { label, mitigation }),
  compare: (id: string, baseline: string, variant: string) =>
    request<Comparison>('GET', `${vp(id)}/compare?baseline=${encodeURIComponent(baseline)}&variant=${encodeURIComponent(variant)}`),
}
