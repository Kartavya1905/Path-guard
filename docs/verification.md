# Verification record

Results of checks that were **actually run** on 2026-11-26. The final full re-run came after all 12 steps. They were run on Windows 11 with Docker Desktop, Python 3.12, Node 26, and Playwright driving the installed Microsoft Edge.

## Automated checks

| Check | Command (from the folder shown) | Result |
|---|---|---|
| Backend lint | `backend`: `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed |
| Backend tests (engine and API, real Postgres) | `backend`: `.\.venv\Scripts\python.exe -m pytest` | **183 passed** (after Step 11) |
| Migration from an empty database | included above (`tests/test_migrations.py`: drop schema → upgrade to head → downgrade to base → upgrade again) | passed |
| Frontend lint | `frontend`: `npm run lint` | no problems |
| Frontend type check | `frontend`: `npm run typecheck` | no errors |
| Production build | `frontend`: `npm run build` | built |
| Browser tests, Vite dev server | `frontend`: `npm run test:e2e` | **13 passed** |
| Browser tests, Docker Compose stack (port 3600) | `frontend`: `$env:PW_BASE_URL = "http://127.0.0.1:3600"; npm run test:e2e` | **13 passed** |
| Browser tests, hosted image in visitor mode on a **fresh, empty database** (port 8650) | see [hosting.md](hosting.md), "Try the hosted image locally" | **13 passed**. On start-up it ran migrations 0001 → 0002 → 0003, purged nothing, started in production / visitor mode with AI off |
| Accessibility (axe-core, WCAG 2.1 A and AA) | part of `npm run test:e2e` (`e2e/a11y.spec.ts`) | **0 violations** on the projects list, new-project form (with validation errors), plan form, workspace (map, risk selected, link estimate editor, Worst cases, Versions, Outcomes) and report. A sanity run confirmed the scanner catches a planted violation |
| Narrow screens | `e2e/a11y.spec.ts` | Phone width (375 px): no sideways scrolling on projects, new project, plan form or report. Tablet width (768 px): the workspace stacks the map first, then details, then plan and suggestions |
| Dependency checks | `backend`: `.\.venv\Scripts\python.exe -m pip check`; `frontend`: `npm audit` | No broken requirements; 0 known vulnerabilities |

The browser tests include `e2e/demo.spec.ts`, which runs the whole [demo script](demo.md):

1. Create with start and goal only, and generate.
2. Review a suggested risk and trace it.
3. Inspect an unknown and a hypothetical estimate.
4. Save a baseline, build the checklist variant, and get **+8.55 pp**.
5. Reload, and see that the map, the note and both versions persist.
6. Export the report and CSV, and log an outcome.

### What the backend tests cover (selected)

- **Plans and maps:** zero-task projects, malformed prerequisites, dependency cycles, unreachable goals, dangling references, invalid evidence input.
- **Probabilities:** ranges, incomplete decisions, invalid totals, unknowns, infeasible ranges, non-exclusive groups.
- **The brief's numbers:** checkout launch first-pass 72.675% → 81.225% (+8.55 pp), and transaction 81.144%. These are exact Decimal comparisons.
- **Runs and limits:** bounded retries, worked by hand (95.2% goal / 3.36% failure / 1.44% retries exhausted). Paths that merge again are counted once. Depth and expansion truncation is reported. A large branching fixture reports truncation.
- **Worst cases:** a critical risk with unknown likelihood stays ranked first, likelihood, impact and evidence are kept separate, and cascades are traced.
- **Persistence and access:** revision conflicts, roles (owner, editor, reviewer, viewer, outsider), visitor isolation, cookie tampering, data expiry, the baseline staying unchanged, versions that can't be edited, an append-only audit trail.
- **Export:** CSV escaping of user text and formula injection, draft vs reviewed labels, sign-off rules.
- **Outcomes:** comparable vs excluded records, sparse data left unresolved, approval required, the explicit sibling rule, older estimates kept in a snapshot.

## Optional AI suggestions (Step 11)

- **Automated tests (fake provider, no model needed):** 20 tests. They cover:
  - AI being off by default.
  - A prompt that includes only nearby nodes and no estimates.
  - Strict parsing: malformed JSON, numeric fields and text estimates are rejected.
  - Unknown node IDs and duplicates being rejected.
  - Suggestions joining the review queue with unknown likelihood.
  - Accepting a recovery branch and analyzing it.
  - Reuse of cached replies.
  - Reads, edits and recalculation never calling the model.
  - Timeouts and invalid output leaving the map unchanged.
  - A remote model needing both the server switch and the project opt-in.
- **Real local model:** Ollama `qwen2.5:1.5b` on this PC's CPU, through the Compose API, on a copy of the checkout launch focused on "Integration defect".
  - A direct call took 30.8 s, with 417 prompt tokens and 363 output tokens.
  - Through the API, with the model already loaded, the first request took 7.1 s. It added 2 suggested hazards, both with unknown likelihood, and rejected 1 recovery aimed at a final failure step.
  - The next request took 2.6 s. Every item came back as a duplicate and was rejected. That request wasn't served from the cache, because the map had changed (new suggestions), so the input differed.
  - Before raising the default timeout to 120 s, the very first call, which includes loading the model, timed out at 60 s and correctly left the map unchanged.
- AI was switched back off afterwards, and all browser tests passed with it off.

## Responsiveness

Command: `backend`: `.\.venv\Scripts\python.exe scripts/perf_check.py`, with the Compose stack running.

- **The two API rows** are real projects created through the API, with checkout rules, owners, 3 stakeholders and a prerequisite chain. Every suggestion is accepted with its branch, and every modeled link gets an explicit estimate.
- **The fixture rows** are `pathguard_engine.fixtures.branching_map(steps, retries=2)`. Each step has a complete 3-way decision (success / failure with a retry limited to 2 attempts / delay that rejoins).
- **Timings** are medians of 5 calls for the API rows, or single engine calls.

| Case | Nodes | Links | Result | States explored | Generate | Save | Analysis | Worst cases | Report | Paths listed |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 tasks (API) | 17 | 28 | exact | 47 | 43 ms | 62 ms | 23 ms | 52 ms | 63 ms | — |
| 10 tasks (API) | 28 | 45 | exact | 42 | 42 ms | 57 ms | 25 ms | 79 ms | 92 ms | — |
| Fixture, 10 steps (engine) | 33 | 61 | exact | 71 | — | — | 7 ms | 2 ms | — | top 10, more not listed |
| Fixture, 40 steps (engine) | 123 | 241 | exact | 281 | — | — | 15 ms | 5 ms | — | top 10, more not listed |
| Fixture, 250 steps (engine) | 753 | 1501 | **partial** (depth limit 200 reached, reported) | 1378 | — | — | 137 ms | 114 ms | — | top 10, more not listed |

Two performance fixes came out of this step:

- **Retry counters are dropped once a run leaves a loop**, since it can never return to it. Before, the 10-step fixture needed more than 20,000 states and was cut off. Now it needs 71, with identical results; every exact test value is unchanged.
- **The "most likely paths" list uses a best-first search.** Analysis of the 10-task workflow went from 555 ms to 23 ms.

## Not verified here

- **Human testing: pending.** See [pilot-plan.md](pilot-plan.md).
- **The hosted site on Render and Neon:** waiting for the owner's accounts. The same image passed all browser tests locally in production / visitor mode.
- **Other browsers:** only Edge (Chromium) was tested; Firefox and Safari weren't. Running them would mean downloading Playwright's browser builds.
- **Manual accessibility:** there has been no screen-reader or keyboard-only walkthrough. The automated axe scan finds only part of the possible issues.
- **Known warning:** the backend test run shows one third-party deprecation warning (Starlette's test client and httpx). It doesn't affect PathGuard.
