# Architecture

PathGuard is three parts. The scenario engine is pure Python and has no idea a web app exists.

```
 browser (React + React Flow)  ──HTTP /api──▶  FastAPI app (backend/app)  ──▶  PostgreSQL
                                                    │
                                                    └── pathguard_engine (pure Python + Pydantic)
```

## Scenario engine (`backend/pathguard_engine/`)

It has no FastAPI, database or model-provider imports. `tests/test_engine_isolation.py` enforces this.

| Module | Responsibility |
|---|---|
| `ids.py` | Prefixed IDs: random ones for things people create, deterministic `stable_id` for rule output so regeneration can merge |
| `probability.py` | `Estimate`: point, range or unknown, as exact `Decimal` fractions. Provenance: measured, expert, hypothetical or unknown. Verification: unverified or reviewed |
| `model.py` | Schemas: plan (project, tasks, prerequisites) and map (nodes, transitions, decision groups, risks, evidence and links, assumptions, mitigations, notes), immutable hashed `ScenarioVersion`, `ObservedOutcome` |
| `plan.py` | Plan checks: broken prerequisites, cycles, and dependency order, which is separate from display order |
| `rules.py` | Deterministic rule engine. It builds the spine from the plan, adds checkout and generic risk suggestions with optional branches, and merges into an existing map without losing decisions or edits |
| `graph.py` | Map validation and readiness (can the map be analyzed, can it be marked reviewed). Only accepted and edited items are modeled |
| `analysis.py` | Probability engine: decision checks, exact or bounded or partial results, state-by-state runs with retry counters, and best-first path listing with limits |
| `explorer.py` | Worst-case ranking (impact, then worst-case likelihood) and cascade tracing |
| `compare.py` | Applying a mitigation to a copy, and comparing a baseline with a variant |
| `report.py` | Risk-register CSV with formula protection, estimate sources, evidence gaps, review summary |
| `outcomes.py` | Turning comparable observed outcomes into an estimate candidate. Never applied automatically |
| `examples.py`, `fixtures.py` | The two hypothetical checkout examples, and synthetic branching maps for performance checks |

### How the probability engine counts runs

A run moves through the map's states. The state is the current node plus, for each attempt-limited link in the loop the run is currently in, how many times that link has been taken.

- **Attempt limits:** a run that would take a link past its limit ends as **retries exhausted**.
- **Leaving a loop:** once a run leaves a loop, a strongly connected component, it can't come back. Those counters are dropped. This keeps the state count linear in map size without changing any result.
- **No double counting:** results are memoized per state, so paths that meet again aren't counted twice.
- **Ranges:** these give outer bounds through interval arithmetic.
- **Missing data:** a missing estimate counts as [0, 1] and marks the result **partial**. So does unmodeled outcome mass, or hitting the depth or expansion limit. A partial result reports only the guaranteed minimum.

## API (`backend/app/`)

| Module | Responsibility |
|---|---|
| `config.py` | `PATHGUARD_*` settings and safety guards (production can't use demo identity; visitor identity needs a secret) |
| `auth.py` | Identity: local demo users, per-browser visitors in signed cookies, or no access. Project roles: owner, editor, reviewer, viewer. Visitor data expiry |
| `api.py` | Projects, drafts, map, generation, review, analysis, worst cases, versions, variants, comparison, members, audit |
| `api_reports.py` | Report, CSV, sign-off, observed outcomes, estimate proposals and approval |
| `versions.py`, `examples_seed.py`, `seed.py`, `purge.py` | Version snapshots, example seeding, demo users, expiry of visitor data |
| `models.py`, `migrations/` | SQLAlchemy tables and Alembic migrations 0001–0002 |

Storage rules:

- **Tables:** the plan is relational, with composite foreign keys for tasks and prerequisites. The map is one JSONB document validated by the engine on every write.
- **Revisions:** one revision counter covers the draft and the map. It is updated by compare-and-swap, and a stale write gets 409.
- **Transactions:** each request is one transaction.
- **Write-once tables:** a database trigger keeps `scenario_versions`, `report_signoffs` and `observed_outcomes` from being updated, and `audit_events` is append-only.

## Web app (`frontend/src/`)

| File | What it is |
|---|---|
| `App.tsx` | Hash routes: `#/` projects, `#/new`, `#/p/<id>` workspace, `#/p/<id>/edit` plan form, `#/p/<id>/report` report |
| `DraftEditor.tsx`, `draft.ts` | Plan form (start and goal required, optional tasks, prerequisites, context) |
| `Workspace.tsx` | Three panels: plan, map checks, probability and suggestions on the left; Map, Worst cases, Versions or Outcomes in the centre; inspector on the right |
| `MapCanvas.tsx`, `graphUtil.ts` | React Flow map: outcome labels and line styles, collapse, cause and effect highlighting |
| `Inspector.tsx`, `EstimateEditor.tsx` | Editing steps, links and risks, plus estimates with provenance, notes, evidence, assumptions and mitigations |
| `AnalysisPanel.tsx`, `WorstCases.tsx`, `VersionsPanel.tsx`, `OutcomesPanel.tsx`, `ReportPage.tsx` | Results, ranking, comparison, outcomes and the printable report |
| `prob.ts` | Exact percent ↔ fraction conversion on decimal strings, with no floating point |
| `index.css` | The design system: colour, type, spacing, radius and shadow tokens, then every component built from them. Outcome colours (success, failure, delay, recovery) always come with a text label or line style. All text meets WCAG AA contrast, which `e2e/a11y.spec.ts` checks |

## Running it

- **Local:** `compose.yaml` runs Postgres, the API (port 8600) and nginx serving the built app (port 3600). Everything is bound to 127.0.0.1.
- **Deployment:** not set up yet. It will be planned once the web app is finished. Visitor identity (`PATHGUARD_VISITOR_IDENTITY_ENABLED`) is already available for a future public demo.

Optional AI suggestions (plan step 11) are built but off by default. They use a local Ollama model only when someone asks; every calculation is local.
