# LaunchOps

A local onboarding demo built with Django REST Framework, PostgreSQL, Redis/Celery and a React frontend (Vite, with TypeScript for the API layer and onboarding pages). HR launches a 20-task workflow shared by HR, IT, a manager and an employee. Every task has an owner, prerequisites, a detail page, stored evidence and a completion event. I built this project with help from AI tools, including GitHub Copilot, Cursor, Claude Code and Codex.

**This app does not provision or verify real accounts, identities, payroll, devices, signatures or deliveries.** Those actions are explicitly labeled simulations. The server generates demo references and links the simulated device across IT and employee tasks. Planning, feedback and acknowledgments are local records; external attendance and discussions are self-reported. Do not enter passwords, recovery codes, identity numbers or financial details into task forms.

## Start

```bash
DEBUG=True docker compose up --build --wait -d
docker compose exec backend python manage.py migrate --noinput
docker compose exec backend python manage.py seed_launchops
```

The default stack runs six services: `postgres`, `redis`, `backend`, `worker`, `beat` and `frontend`. Open [http://localhost:13000](http://localhost:13000). The API is at [http://localhost:18000/api/](http://localhost:18000/api/). Override `FRONTEND_PORT`, `BACKEND_PORT`, `REACT_APP_API_URL` and `CORS_ALLOWED_ORIGINS` in the root `.env` together if a port is occupied.

**Preserve existing Docker volumes and application data. Do not run `docker compose down -v` or renew volumes.**

The explicit `DEBUG=True` override is for this local demonstration and does not edit `.env`. The seed refuses to run with `DEBUG=False`, rolls back on account collisions, and never resets existing passwords or workflows. Do not seed or reuse demo accounts in a production database.

Open the shared landing page and choose **HR**, **IT**, **Manager**, or **Employee**. Each entry has a dedicated sign-in path; the server verifies the account's assigned role before issuing tokens. Existing legacy accounts can use **Existing account**. Public registration always creates an employee.

New demo accounts use the local-only password **LaunchOpsDemo2026!**:

| Username | Role | Five assigned tasks |
| --- | --- | --- |
| `demo_hr` | HR | Profile, paperwork, payroll, handbook publication, final signoff |
| `demo_it` | IT | Equipment, access, MFA, device security, handoff |
| `demo_manager` | Manager | Mentor, orientation, goals, check-ins, manager signoff |
| `demo_employee` | Employee | Contact preferences, handbook acknowledgment, training, receipt, feedback |

## How a workflow behaves

- **Ready and blocked work.** Sign in as HR and select a workflow. **Ready for you** links to tasks that have satisfied prerequisites and belong to the signed-in account. Other roles see **Waiting for ...** without a completion form. **Prerequisites needed** links to the blocking tasks.
- **Server-side checks.** The server enforces task ownership, prerequisites and the exact evidence fields for each task. Public registration creates employees only.
- **Duplicate requests.** A repeated completion request returns the original result. It keeps the original evidence and adds no second completion event.
- **Final signoff.** It requires all other 19 tasks, a realistic start date and no unresolved blockers, and it displays the saved evidence for review.
- **Training.** The employee's Learning page has five lessons with acknowledgment buttons and five three-question assessments graded on the server. A pass needs all three answers correct. Acknowledgments record a declaration, not proof of reading. A failed assessment can be retried; the failed attempt is archived.
- **Overdue scan.** A Celery scan records at most one overdue event per ready task and sends no notifications. The legacy chat purge, which hard-deletes chat messages of courses whose term has ended, is off by default; `CHAT_PURGE_ENABLED=true` schedules it.
- **Refresh and pagination.** The dashboard refreshes every 15 seconds and on window focus. There is one workflow per employee. Lists use numbered pages with previous/next controls; metrics and ready tasks describe the current page. The API returns `count`, `page`, `total_pages`, `next`, `previous`, and `results`, ordered by newest creation and ID. The default page size is 12; `page_size` is capped at 100 per page, with no total-record cutoff.
- **Session renewal.** Expired access tokens use `/api/auth/refresh/`. Concurrent 401s share a refresh request, each request retries once, and a failed renewal clears the session and asks for sign-in. Login errors, permission errors and network failures are not replayed. Logout prevents a pending refresh from restoring the session.

## Concurrency design

`complete_step` locks the workflow row first and then the step row (`SELECT ... FOR UPDATE`) inside one transaction. This serializes concurrent completions, including different steps of the same workflow, and makes a duplicate submission a no-op. `backend/onboarding/test_concurrency.py` starts 20 threads released together by a barrier against PostgreSQL and asserts exactly one completion event. With the two `select_for_update` calls removed, the same test fails (5 of the 20 requests reported creating the completion), so the test detects the race it is meant to prevent.

## Tests and CI

```bash
docker compose exec -e REDIS_URL=redis://redis:6379/15 backend python manage.py test --noinput
docker compose exec backend python manage.py check
docker compose exec backend python manage.py makemigrations --check --dry-run
cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

The phase-one cleanup passed **212 backend tests** on PostgreSQL and **20 frontend tests**, with no failures or skips. Typecheck, production build, Django system checks and migration checks passed. Exact commands, environment and raw output are in [t01 evidence](docs/evidence/t01/COMMAND.txt). Earlier browser and delivery results are [archived](docs/legacy-lms/DEMO_DELIVERY.md). The onboarding-specific tests include:

- **Workflow tests** (`onboarding/tests.py`): all 20 tasks end to end with owner boundaries, invalid and forged evidence, the dependency gate, final signoff rules, idempotent overdue scan and a query budget.
- **Matrix tests** (`onboarding/test_matrix.py`, table-driven with `subTest`): blueprint integrity (20 steps, 5 per role, unique keys, existing dependencies, acyclic graph), every step against every non-owner role, every step's prerequisites, and missing, unknown and empty evidence fields for every step.
- **Race test** (`onboarding/test_concurrency.py`): described above.

GitHub Actions runs on every pull request and every push to `main`. The `backend` job runs `manage.py check`, `makemigrations --check` and the full suite against PostgreSQL 16 and Redis 7. The `frontend` job runs `npm ci`, the type check, the tests and the build. Tests are evidence for specific behavior; they are not a production security assessment.

The browser walkthrough (installed Chrome and Node 18+ with Playwright 1.51.1) signs in through the UI under all four demo roles and completes the workflow:

```bash
npm install --prefix /tmp/launchops-browser playwright@1.51.1
NODE_PATH=/tmp/launchops-browser/node_modules node scripts/walkthrough.cjs
```

It prints its report path under `/tmp/launchops-walkthrough-*` and keeps the fresh audit employee/workflow it creates. Each run uses a new employee, completes all tasks through the correct role's forms, deliberately fails and retries an assessment, reports and resolves a blocker before final signoff, and checks duplicate completion requests. Existing workflows are only inspected. `CHROME_BIN` overrides the detected macOS/Linux Chrome path; `LAUNCHOPS_DEMO_PASSWORD` supports existing demo accounts whose passwords were changed. Both browser scripts refuse non-local hosts.

To exercise real pagination beyond 100 workflows and signed token expiry in Chrome:

```bash
docker compose exec backend python manage.py seed_launchops --pagination-demo
NODE_PATH=/tmp/launchops-browser/node_modules node scripts/check_session_pagination.cjs
```

The optional flag adds named pagination fixtures with unusable passwords and preserves existing records. The session check requires the local Compose backend with `DEBUG=True`; it creates signed expired tokens in memory without logging or saving them. It checks page navigation, concurrent refresh, refresh-only session restoration, expired refresh and missing refresh. `LAUNCHOPS_REPORT_DIR` selects either script's report directory. See [executed delivery evidence and LMS audit](docs/legacy-lms/DEMO_DELIVERY.md). The earlier [audit report](docs/legacy-lms/AUDIT.md) predates the current suite; its counts are historical.

## Frontend

The frontend uses Vite, Vitest and a strict `tsconfig.json`. The shared API layer, auth context, app shell, login and registration pages and the onboarding pages are TypeScript (`.ts` and `.tsx`, with API model types in `frontend/src/types`). The legacy learning pages (courses, quizzes, schedule, chat history) are still JavaScript and compile through `allowJs`. `npm start` runs the Vite dev server; a successful production build is not a production deployment.

Course chat displays saved history only; sending is unavailable during this phase.

## Existing data and dates

Migrations preserve completed steps, evidence and activity. Seven-step workflows expand to 20 steps, and a historical final signoff stays signed off even if later-added tasks remain unfinished. Historical evidence is labeled unverified rather than upgraded into new simulation proof. Out-of-range start dates (for example the year 1111) are flagged for HR; use **Correct start date** with the intended date, which updates due dates and records the old and new values. New or corrected dates must fall within the past year or next three years. `seed_launchops` adds missing demo users, lessons and assessments without resetting passwords, publication settings or workflows; run it after updating.

## Origins

LaunchOps started as a learning-management system I built (courses, chapters, quizzes, scheduling, course chat history) and that I converted into this onboarding platform. The onboarding workflow engine is the later work in this repository: the 20-task blueprint, the role and prerequisite rules, the evidence forms and checks, transactional completion with row locks, the overdue scan, the onboarding pages, and the tests, CI and frontend tooling described above. The learning modules remain for the training step and are not part of the onboarding walkthrough. This repository's history starts from a snapshot of the combined code, so it does not show that split.

## Limits

Use Python 3.12+, Node 20+, PostgreSQL and Redis for a non-Docker setup: copy `backend/.env.example` to `backend/.env`, install `backend/requirements.txt`, then migrate, seed with `DEBUG=True` for local use, and run Django, and run `npm ci` and `npm start` in `frontend/`. Docker is the recommended setup. HR and IT see all workflows in this single workspace; managers see their assigned workflows and employees see their own. Staff provisioning requires trusted administration. There is no tenant isolation, invitation system, identity provider, external evidence verification or production notification channel. Training reuses course lessons and server-graded quizzes; employees cannot use legacy chat history or scheduling routes or change their enrollment. Legacy students retain read-only chat history and scheduling. The course score API excludes employee training scores. Tokens remain in tab-scoped session storage; logout clears that tab but does not revoke an already-issued refresh token on the server. Access tokens last 24 hours and refresh tokens seven days.
