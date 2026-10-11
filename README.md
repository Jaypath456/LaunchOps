# LaunchOps

For the reviewed ZIP, use `sh scripts/review-compose.sh up -d --no-build --wait` to preview the isolated review project on ports 33000/38000/39090. The standard startup below is for a deployment you choose to perform; see [the review and handoff report](docs/REVIEW_UPDATED_ZIP.md) first.

A local onboarding demo built with Django REST Framework, PostgreSQL, Redis/Celery and React, plus a new Go live-quiz path backed by MongoDB. HR launches a 20-task workflow shared by HR, IT, a manager and an employee. Every task has an owner, prerequisites, a detail page, stored evidence and a completion event.

**New live-learning experiment:** two Go WebSocket workers use Redis to coordinate quiz rooms and MongoDB to retain per-room answers. Open the same instructor live-quiz page and student join page to try it. The Go path is on by default through `ws://localhost:19090`. Saved live scores appear at session end. Live scores are separate from the five asynchronous onboarding assessments, which still use PostgreSQL. See [live quiz architecture, migration status, and benchmark](docs/LIVE_ARCHITECTURE.md). The earlier [onboarding audit](docs/AUDIT.md) was completed *before* this new Go work; its test counts do not verify Go.

**This app does not provision or verify real accounts, identities, payroll, devices, signatures or deliveries.** Those actions are explicitly labeled simulations. The server generates demo references and links the simulated device across IT and employee tasks. Planning, feedback and acknowledgments are local records; external attendance and discussions are self-reported. Do not enter passwords, recovery codes, identity numbers or financial details into task forms.

## Start or update

```bash
docker compose up --build --wait -d
docker compose exec backend python manage.py migrate --noinput
docker compose exec backend python manage.py seed_launchops
```

The gateway uses port `19090`. Override `LIVE_PORT` and `REACT_APP_LIVE_WS_URL` together if this port is occupied, for example `LIVE_PORT=19100 REACT_APP_LIVE_WS_URL=ws://localhost:19100 docker compose up --build --wait -d`. Existing PostgreSQL and Redis volumes are retained. New MongoDB live answers use a separate `mongo_data` volume. To create Django live rooms from the browser, set `LIVE_QUIZ_ENGINE=django` and recreate the backend container. The browser routes each room according to its saved engine; clearing the Go URL alone does not change a room’s engine. Both paths stay available to benchmark independently.

Open [http://localhost:13000](http://localhost:13000). The API is at [http://localhost:18000/api/](http://localhost:18000/api/). All demo accounts use the local-only password **LaunchOpsDemo2026!**:

| Username | Role | Five assigned tasks |
| --- | --- | --- |
| `demo_hr` | HR | Profile, paperwork, payroll, handbook publication, final signoff |
| `demo_it` | IT | Equipment, access, MFA, device security, handoff |
| `demo_manager` | Manager | Mentor, orientation, goals, check-ins, manager signoff |
| `demo_employee` | Employee | Contact preferences, handbook acknowledgment, training, receipt, feedback |

Sign in as HR and select a workflow. **Ready for you** contains direct links to tasks that have satisfied prerequisites and belong to the signed-in account. An employee viewing ready IT work sees **Waiting for IT**, with no completion form. **Prerequisites needed** links to the tasks blocking progress. Use Logout and sign in to switch accounts; accounts in separate browser tabs have separate session storage.

The employee’s Learning page links to five lessons and five three-question assessments. Each lesson has an acknowledgment button and previous/next navigation. These acknowledgments record a declaration, not proof of reading. Assessments are graded on the server. Pass requires an exact score of at least 70%; with three one-point questions, all three must be correct. A failed assessment can be retried from its result page; the failed answers, score and time are archived, and the activity log records the retry. Passed assessments and completed training cannot be retried. Return to the training task to finish after all lesson acknowledgments and assessment passes.

The handbook task contains the actual versioned demo handbook that HR publishes locally. Manager tasks save assignments, goals and check-in plans; they do not send invitations or messages. Employee receipt and feedback can report **Needs support**. HR sees these blockers and records a resolution on final signoff. The resolution is explicitly a self-report. Final signoff checks all other 19 tasks, a realistic start date and no unresolved blockers, and displays the saved evidence for review.

The dashboard and task pages refresh every 15 seconds and on window focus. Duplicate completion requests preserve the original evidence and do not add completion events. New workflows require a published learning track. HR can change an incomplete workflow’s track in its summary; a completed training record is preserved. There is one workflow per employee.

## Existing data and dates

Migrations preserve completed steps, evidence and activity. Seven-step workflows expand to 20 steps; a historical final signoff stays signed off even if later-added tasks remain unfinished. Historical evidence is labeled unverified rather than upgraded into new simulation proof.

Year-1111 and other out-of-range start dates are flagged for HR. Use **Correct start date** with the actual intended date. The app does not guess one. Correction updates due dates and records the old and new date without erasing completion history. New/corrected dates must fall within the past year or next three years. Date-only values display on the correct local calendar day. A very old legitimate historical date may also trigger this warning; correcting it requires human judgment.

The seed adds missing demo users, lessons and assessments without resetting passwords or existing publication settings, or duplicating workflows. It repairs two exact old incident-response answer keys that incorrectly rewarded sharing a password/public posting, preserving historical submissions. Custom question content is not overwritten. Run it after updating. Historical assessment scores are not regraded automatically.

**Preserve the PostgreSQL volume. Do not run `docker compose down -v`.** Before this audit a SQL backup was saved outside the repository at `/tmp/launchops-before-audit.sql`; move it to durable storage if needed. Audit walkthroughs create clearly named `audit_employee_*` / `audit_migrated_*` accounts and leave their workflow history in place. The original `jay` workflow keeps its date and completed profile until HR supplies the intended date.

## Verification

The updated ZIP review, isolated startup commands, failures/fixes, and measured live benchmark are recorded in [docs/REVIEW_UPDATED_ZIP.md](docs/REVIEW_UPDATED_ZIP.md). Use `sh scripts/review-compose.sh up -d --no-build --wait` for that separate review project; its ports are 33000/38000/39090 and its database volumes are separate from the running copy.

```bash
# PostgreSQL tests in a separate test database; Redis DB 15 isolates test cache/channel traffic.
docker compose exec -e REDIS_URL=redis://redis:6379/15 backend python manage.py test --noinput
docker compose exec backend python manage.py check
docker compose exec backend python manage.py makemigrations --check --dry-run
docker compose exec frontend sh -c 'CI=true npm test -- --watchAll=false --runInBand'
docker compose exec frontend sh -c 'CI=true npm run build'
```

Browser walkthrough (installed Chrome, Node 18+ with Playwright 1.51.1, or use a current Playwright with Node 20+):

```bash
npm install --prefix /tmp/launchops-browser playwright@1.51.1
NODE_PATH=/tmp/launchops-browser/node_modules node scripts/walkthrough.cjs
```

This script signs in through the UI, opens every task under the demo roles, creates an audit employee through registration, launches a workflow through HR, completes all tasks, takes assessments, retries a failed assessment, checks direct unauthorized API calls, duplicate completion, saved evidence/activity and role switching. It writes `docs/audit/walkthrough.json`. It completes remaining demo tasks and retains the new audit workflow. It is intended for this local demo, not production data. An optional `AUDIT_MIGRATED_EMPLOYEE` includes an audit copy of an existing migrated workflow; `scripts/create_audit_fixture.py` creates that copy from local workflow 2 without changing the source. The migration test separately runs the real historical migration on the test database.

The [audit report](docs/AUDIT.md) lists all 20 task contracts, checks, changed files and limitations. Confirmed results: 256 backend regression tests plus 3 subsequently added focused tests passed; 3 frontend tests passed; the production build and the four-role browser walkthrough passed. A final live-site smoke check also passed. Tests are evidence for specific behavior; they are not a production security assessment. The old `loadtests/` suite predates onboarding and does not establish its performance.

The reviewed ZIP omits all `.env*` files, installed dependencies, builds and caches. The review Compose wrapper uses explicit settings and Compose defaults; create local environment files separately when needed.

## Local development and limits

Use Python 3.12+, Node 20+, PostgreSQL and Redis. Copy `backend/.env.example` to `backend/.env`, configure the database and secret, install `backend/requirements.txt`, then migrate, seed and run Django. Run `npm ci` and `npm start` in `frontend/`. Docker is the recommended local setup and serves the React development server; a successful production build is not a production deployment.

PostgreSQL and Redis are exposed only inside Compose. Default host ports are frontend 13000 and backend 18000. Override `FRONTEND_PORT`, `BACKEND_PORT`, `REACT_APP_API_URL` and `CORS_ALLOWED_ORIGINS` in the root `.env` together. Rebuild/restart services after configuration changes.

HR and IT see all workflows in this single workspace; managers see their assigned workflows, and employees see their own. Public registration creates employees only. Staff provisioning requires trusted administration. There is no tenant isolation, invitation system, identity provider, external evidence verification or production notification channel. A Celery scan records one overdue event per ready task; it sends no notifications. Lists show at most 100 workflows. The legacy learning authoring, messaging, live quiz and scheduling modules have separate role gates and are not part of the four-role onboarding walkthrough.
