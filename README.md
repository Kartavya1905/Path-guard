# PathGuard

A visual project risk planner. You enter a starting situation and a goal, and PathGuard builds an editable scenario map of success, failure, delay and recovery paths. It ranks risks and compares a safeguard against a saved baseline. Everything runs locally. It uses no paid services, credentials or external AI.

> Status: **Step 7 (worst-case explorer)**. Later steps are tracked in the plan below.

## Scenario engine

`backend/pathguard_engine/` is plain Python and Pydantic. It never imports FastAPI, the database or a model provider, and a test enforces this.

- `ids.py`: stable prefixed IDs. Random IDs are used for anything a person creates. Rule-generated items get deterministic IDs so they can be deduplicated.
- `probability.py`: exact `Decimal` fractions in the range 0 to 1. An estimate is a point, a range or unknown, and records its source (measured, expert, hypothetical or unknown) and whether a human has reviewed it.
- `model.py`: schemas for projects, tasks, prerequisites, scenario nodes and transitions, decision groups, risks, evidence and evidence links, assumptions, mitigations, reviewer notes, immutable scenario versions (with a content hash) and observed outcomes.
- `plan.py`: finds broken prerequisites and dependency cycles, and works out a dependency order that is separate from display order.
- `rules.py`: the deterministic rule engine (no AI, no network). See below.
- `graph.py`: map validation and readiness (whether the map can be analyzed or marked reviewed).

### Rule engine

`POST /api/projects/{id}/graph/generate` builds the map from the saved plan:

- **Spine:** start → each task in dependency order → goal. It only restates your own plan, so it is modeled straight away. A plan with zero tasks gives start → goal.
- **Suggestions:** each rule hit becomes a risk with a type, trigger, affected step, rationale, consequence and a suggested mitigation. It carries **no probability**. Where it fits, it also gets a suggested branch:
  - a **delay** that rejoins the next step, or
  - a **failure** with a retry back to the step (at most 2 attempts) and a "Goal not reached" end state.
- **Checkout template** (context domain `checkout`): unclear requirements, payment API mismatch, incomplete testing, and release defect or failed rollback. Each attaches to the task whose title fits best.
- **Generic rules:**
  - Missing prerequisites (2+ tasks and none set).
  - Listed order that conflicts with a prerequisite, and prerequisite loops.
  - Approval bottlenecks (tasks mentioning approval, sign-off or review, or 3+ stakeholders).
  - Handoff delays (a prerequisite between different owners).
  - Resource conflicts (one owner on tasks with no order between them).
  - Deadline conflicts (the longest dependency chain is longer than the days left, or the deadline has passed).
  - Task costs over the budget.

  Each rule only fires when you have supplied the information it needs.
- **Deduplicated and stable:** each suggestion has a dedupe key and a deterministic ID.
- **Regeneration keeps your work.** It refreshes rule items nobody has decided on, adds new ones and removes stale undecided ones. It never changes your own items, edited items or accept/reject decisions. Changing a rule item through `PUT …/graph` marks it `edited`.
- **Review:** `POST /api/projects/{id}/risks/{risk_id}/review` with `decision` (`accept` or `reject`), `model_branch` and `revision`. Accepting with `model_branch: true` adds the suggested branch to the modeled map. Owners, editors and reviewers can review.

### Probability engine

`analysis.py` analyzes the saved, modeled map. It's local arithmetic only, with no AI and no network. `GET /api/projects/{id}/analysis` returns the results. Optional `max_depth` (default 200), `max_expansions` (default 20000) and `max_results` (default 10) cap the work, and every cap is reported.

- **Estimates:** each link has an exact percentage, a range or **unknown**, plus its source:
  - measured: data source, count and sample size, date range, context
  - expert: reviewer, rationale, assumptions, review date
  - hypothetical: labeled assumption

  Whether a person has reviewed it is recorded separately. Values are stored as exact decimal fractions, and the UI converts percentages without floating point.
- **Decisions:** every decision point is checked.
  - A **complete** decision must total exactly 100%, or with ranges, be able to reach 100%.
  - Totals over 100% are always invalid.
  - An incomplete decision, or a single link below 100%, keeps its **unmodeled** share visible. Nothing is normalized, and no zero, average or remainder is filled in.
- **Results:**
  - **Clean first-pass success:** the goal is reached through success links only.
  - **Eventual success:** the goal is reached after any modeled recovery.
  - **Outcomes:** every run ends in exactly one of goal, failure, other modeled outcome, retries exhausted, not modeled, or cut off by limits.
- **How runs are counted:** runs are followed state by state, where the state includes how often each attempt-limited link has been used. This keeps retries bounded and stops paths that merge again from being counted twice. A run that would go past a link's attempt limit ends as "retries exhausted".
- **Exact, bounds, partial:**
  - Exact needs point estimates wherever they matter.
  - Ranges give outer bounds, never midpoints.
  - A missing estimate, unmodeled outcomes or a limit being hit make the result **partial**. Then only the guaranteed minimum is shown, and no exact eventual figure is given.
- **Most likely paths** are listed as examples. The totals never come from adding them up.
- **Checkout examples** (`examples.py`, all numbers hypothetical):
  - Launch: clean first-pass 72.675% baseline and 81.225% with the checklist variant (+8.55 pp). Eventual success is 89.7065316% with rework and rollback.
  - Transaction: the primary path is 81.144%.

In the workspace, set an estimate in the right panel when a link is selected. Mark a decision complete there too, and see its total. Map links show their percentage, with "hyp." for hypothetical estimates. The **Probability** section in the left panel shows the results for the last saved map.

### Worst-case explorer

`explorer.py` ranks risks, and `GET /api/projects/{id}/explorer` returns the ranking (add `include_rejected=true` to include rejected risks). In the workspace, switch the centre panel from **Map** to **Worst cases**.

- **Three separate measures:** likelihood, impact and strength of evidence are shown side by side and never blended into one score.
  - **Likelihood** comes from the model if possible: the chance a run reaches the risk's accepted branch. Otherwise it comes from the risk's own estimate, or it is **unknown**. Nothing is guessed.
  - **Evidence** is the best quality linked to the risk or its steps.
- **Order:**
  1. Impact, with critical first.
  2. Worst-case likelihood: the upper bound, where unknown counts as "up to 100%".
  3. Title.

  So a critical risk with low or unknown likelihood stays at the top. Each risk says why it has its rank.
- **Cascades:** for a risk whose branch is on the map, the explorer traces the most likely path from the risk to a bad end. On ties, for example with unknown estimates, it uses the shortest path. It also shows how runs end once the risk happens: goal, failure, retries exhausted or not modeled. **Show on map** selects the risk and highlights its causes and effects.
- **Filters:** worst-case likelihood, impact, evidence quality, cause (category), and whether to include unreviewed suggestions.
  - The likelihood and evidence filters never hide a critical-impact risk. The status line counts the critical risks kept visible this way.
  - The status line says how many risks are shown, hidden by filters and rejected.
  - On the map, the toolbar counts collapsed nodes and hidden rejected nodes.

### Validation

Every graph response includes `readiness`: the issues, plus `can_analyze`, `can_mark_reviewed` and plain-language `blockers`.

- **Errors:**
  - Dangling references, duplicate IDs, and a missing or extra start node.
  - A missing or unreachable goal.
  - Dead ends, and end states that have outgoing links.
  - Branches that are not in one decision group, and a decision group attached to the wrong node.
  - Loops with no retry limit or no way out, and a retry limit with nowhere to go once retries run out.
  - A map order that contradicts a prerequisite.
- **Warnings:** unreachable nodes.
- Only **accepted or edited** nodes and links are part of the modeled map. Suggested and rejected items are still checked for broken references.
- Incomplete maps still save. Analysis needs zero errors. Marking the map reviewed also needs every suggestion accepted or rejected.

## API and storage

| Route | Purpose |
|---|---|
| `GET /api/me` | Current (demo) user |
| `GET/POST /api/projects` | List your projects. Create one (only `start` and `goal` are required) |
| `GET/PUT/DELETE /api/projects/{id}` | Read, save (send the `revision` you loaded) or delete a draft |
| `GET/PUT /api/projects/{id}/graph` | Read or save the scenario graph (same `revision` counter as the draft) |
| `POST /api/projects/{id}/graph/generate` | Run the rule engine on the saved plan and merge into the map (send `revision`) |
| `POST /api/projects/{id}/risks/{risk_id}/review` | Accept or reject a suggested risk, optionally modeling its branch |
| `GET/POST /api/projects/{id}/versions`, `GET …/versions/{vid}` | Immutable snapshots: baseline, variant (needs a parent) or snapshot |
| `POST /api/projects/{id}/members` | The owner adds an editor, reviewer or viewer |
| `GET /api/projects/{id}/audit` | Audit trail |

- **Storage:** project, tasks and prerequisites are relational rows. Prerequisites have foreign keys to tasks, so they can't point at a missing task. The graph (nodes, transitions, decision groups, risks, evidence, links, assumptions, mitigations, notes, layout) is one JSONB document. It is validated by the engine schema on every write and saved atomically with the revision.
- **Conflicts:** a save based on an old revision gets `409 revision_conflict`, and the newer data is kept.
- **Drafts:** drafts with problems such as dependency cycles can still be saved; the response lists them as `issues`. Only data that can't be stored is rejected with 422: prerequisites pointing at missing tasks, or duplicate task IDs.
- **Seed data:** the API container runs `alembic upgrade head` and then `python -m app.seed` on start. The seed creates three local demo users: Demo Planner (default), Demo Reviewer and Demo Outsider. It does nothing when demo identity is off.
- **Security:** see [docs/security.md](docs/security.md) for local protections and the TLS, authentication and encryption requirements for any hosted deployment.

## Project form (web app)

- **Projects page** (`#/`) lists the projects you can open. **New project** (`#/new`) needs only a starting point and a goal. Title, success criteria and the optional context (domain, budget, deadline, risk tolerance, stakeholders, integrations, resource limits, notes) can be filled in later.
- **Tasks are optional.** Add, edit, delete and reorder them with Up and Down. List order is display order only.
- **Prerequisites** are explicit connections ("A must finish before B"). Deleting a task also removes its prerequisites.
- **Saving** sends the revision you loaded. If someone saved in between, the save is refused, the newer version is kept, and you can load it. A draft with a dependency cycle still saves, and the cycle is listed under **Plan checks**.
- Viewers and reviewers see the form read-only. The browser warns before leaving with unsaved changes.

## Workspace (web app)

Open a project from the Projects page to get the workspace at `#/p/<id>`. The plan form lives at `#/p/<id>/edit`.

- **Left panel:** the plan (start, goal, context, tasks, dependencies, with an **Edit plan** link) and the map checks (whether the map can be analyzed or marked reviewed, with each problem and a **Show** button). Below that, **Risks and suggestions**, filtered to review, decided or all:
  - **Accept** or **Reject** a suggestion, or use **+ Branch** to accept it and add its branch to the map.
  - **+ Add a risk** adds your own risk, attached to the selected step.
- **Centre: the map.**
  - Pan, zoom, fit, and a minimap.
  - **Generate map** or **Regenerate from plan**, which keeps your decisions and edits. **+ Step** adds a step.
  - Drag nodes to move them, and drag from a node's right edge to another node to add a link.
  - **Collapse** on a main-path step hides its side branches and shows how many are hidden. **Collapse all** and **Expand all** do every step at once.
  - **Show rejected** brings rejected items back into view.
  - Edits stay local until **Save map**, and **Discard** throws them away.
- **Right panel:** details of the selected step, link or risk.
  - You can edit the name or trigger, type or outcome, severity, end state, condition, retry limit and decision group.
  - It also shows links and affected steps, and you can accept or reject the item.
  - Assumptions, evidence (with type and quality), mitigations (with owner and expected effect) and review notes can be added there.
- **Labels as well as colour:** every node shows its type in text (Start, Step, Failure, Delay…), and every link shows its outcome in text (Success, Failure, Delay, Recovery). Line styles differ too: failure is thick, delay dashed, recovery dotted. Suggested items are dashed and faded.
- **Tracing:** selecting a risk highlights its upstream **causes** (paths leading into the affected step) and downstream **effects** (what follows from its branch). Everything else is dimmed. Selecting a step traces it the same way.
- **States:** loading, error, an empty map (with a Generate button), unsaved changes, and save conflicts. If another tab saved first, you can load the latest version. Viewers get a read-only map. Reviewers can also accept or reject suggestions.
- **Review notes** are stamped by the server with the real author and time. They can't be edited or deleted.

## Stack

| Layer | Tech |
|---|---|
| Frontend | React, TypeScript, Vite, React Flow (`@xyflow/react`) |
| Backend | FastAPI, Pydantic, SQLAlchemy, Alembic |
| Database | PostgreSQL 16 |
| Local services | Docker Compose with health checks |

## Ports (host, bound to 127.0.0.1)

| Service | Port |
|---|---|
| Web (Compose, nginx serving the production build) | 3600 |
| Web (Vite dev server) | 5600 |
| API | 8600 |
| PostgreSQL | 26432 |

## Prerequisites (Windows)

- Docker Desktop
- Node.js 22.12+ with npm
- Python 3.12 (`py -3.12`)

## Free online hosting

[docs/hosting.md](docs/hosting.md) explains how to run PathGuard online at $0 as a public demo, on Render (free web service) and Neon (free Postgres). The root `Dockerfile` builds a single container that serves the web app and the API together. `render.yaml` sets it up in visitor mode, where each browser gets its own private anonymous account and data expires after 7 days.

## Run everything with Docker

Run these from the `pathguard` folder:

```powershell
docker compose up -d --build
```

```powershell
docker compose ps
```

```powershell
Invoke-RestMethod http://localhost:8600/api/health
```

Open http://localhost:3600.

Stop the stack:

```powershell
docker compose down
```

Reset the stack. This **deletes the local database volume**:

```powershell
docker compose down -v
```

## Backend development

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Run the tests and the linter. The API tests need the Compose database (`docker compose up -d db`). They create and reset a separate `pathguard_test` database, rebuilding it from the migrations, and are skipped if Postgres isn't reachable. Use `127.0.0.1`, not `localhost`, in database URLs: on Windows, `localhost` tries IPv6 first and hangs, because Docker only listens on IPv4 here.

```powershell
.\.venv\Scripts\python.exe -m pytest
```

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Run the API against the Compose database. Start only the database first with `docker compose up -d db`. Then:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8600
```

Before running the API outside Docker, apply the migrations and seed the demo users. The API container does both on start.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

```powershell
.\.venv\Scripts\python.exe -m app.seed
```

## Frontend development

```powershell
cd frontend
npm install
npm run dev
```

The dev server runs on http://127.0.0.1:5600 and proxies `/api` to `http://127.0.0.1:8600`.

Run the checks:

```powershell
npm run lint
```

```powershell
npm run typecheck
```

```powershell
npm run build
```

Browser tests (Playwright): the plan form (`e2e/smoke.spec.ts`) and the workspace (`e2e/workspace.spec.ts`). They need the API running (`docker compose up -d`), start the Vite dev server themselves, and delete every project they create. On Windows they use the installed Microsoft Edge, so no browser download is needed. On other systems, run `npx playwright install chromium` once.

```powershell
npm run test:e2e
```

To test the Compose build on port 3600 instead:

```powershell
$env:PW_BASE_URL = "http://127.0.0.1:3600"; npm run test:e2e
```

## Configuration

Settings are environment variables with the `PATHGUARD_` prefix. They are defined in `backend/app/config.py`.

| Variable | Default | Notes |
|---|---|---|
| `PATHGUARD_ENV` | `local` | `local`, `test` or `production` |
| `PATHGUARD_DATABASE_URL` | local Compose DB | SQLAlchemy URL |
| `PATHGUARD_DEMO_IDENTITY_ENABLED` | `true` | Seeded local demo user. Startup **fails** if this is true while `ENV=production` |
| `PATHGUARD_EXTERNAL_PROCESSING_ENABLED` | `false` | No project data leaves the machine unless enabled |
| `PATHGUARD_VISITOR_IDENTITY_ENABLED` | `false` | Public demo: a private anonymous account per browser. Needs `PATHGUARD_SECRET_KEY` (32+ characters) and demo identity off |
| `PATHGUARD_VISITOR_RETENTION_DAYS` | `7` | Visitor projects untouched this long are deleted |
| `PATHGUARD_VISITOR_PROJECT_LIMIT` | `20` | Projects per browser in the public demo |
| `PATHGUARD_STATIC_DIR` | unset | Serve a built web app from the API (set by the root `Dockerfile`) |

The Compose database password (`pathguard`) is a local-only demo value. The database port is bound to `127.0.0.1` only.

## Implementation plan

0. Scaffold
1. Schemas and scenario engine core
2. Persistence, migrations, access control
3. Project form UI and smoke test
4. Rule engine and graph validation
5. Three-panel workspace
6. Probability engine
7. Worst-case explorer
8. Baseline vs mitigation comparison
9. Export and outcome logging
10. Verification and documentation
11. Optional AI suggestions (off by default)
