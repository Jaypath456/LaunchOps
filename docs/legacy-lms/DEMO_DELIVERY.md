**LaunchOps demonstration delivery — October 28, 2026**

The local demonstration runs at http://localhost:13000, with the API at http://localhost:18000/api/. The working tree was clean before implementation. Existing application records and Docker volumes were preserved. No database reset, volume deletion, history rewrite, or force-push was performed. Django's test runner creates and destroys its separate test database.

The initial inspection confirmed existing task ownership, prerequisites, evidence validation, atomic/idempotent completion, assessment retry, and final signoff. Confirmed defects were password trimming in both browser forms and registration serialization; an unpaginated workflow list sliced at 100; missing backend/client refresh; a seed without a production guard; a walkthrough that completed existing shared workflows; and LMS permissions that treated training enrollment as legacy participation. Identity providers, tenant isolation, real provisioning and bundle splitting were left outside this task.

The implementation was delivered as tested increments: role entry/password preservation; refresh/session handling; pagination; guarded seed/repeatable walkthrough; and LMS boundaries with this evidence. The landing page contains dedicated `/login/hr`, `/login/it`, `/login/manager`, and `/login/employee` entries. The server validates the selected role against the authenticated account before issuing tokens. Public registration ignores supplied privilege fields and creates employees only. No demo passwords are displayed in the application.

Executed environment: Docker Desktop on macOS; backend Python 3.12.16, Django 6.0.6, DRF 3.17.1; PostgreSQL 16.15; Redis 7 image. Host frontend checks used Node 24.21.0 and npm 11.19.0. The Compose frontend/browser server used Node 22.23.4 and npm 10.9.9. Runtime versions were obtained with `docker compose exec backend python -c "import sys, django, rest_framework; print(sys.version, django.get_version(), rest_framework.VERSION)"`, `docker compose exec postgres psql -U launchops_user -d launchops_db -c 'SHOW server_version;'`, `node --version`, `npm --version`, and the corresponding frontend container commands.

| Executed check | Result | Saved evidence |
| --- | --- | --- |
| Full backend suite in Compose, PostgreSQL, Redis database 15 | 284 tests, OK, no skips; 222.916 seconds | [Backend log](audit/delivery/backend-tests.txt) |
| Host frontend `CI=true npm test` | 21 tests across 6 files, all passed | [Vitest log](audit/delivery/frontend-tests.txt) |
| Host `npx tsc --noEmit` | Exit 0 | [Command and exit status](audit/delivery/frontend-typecheck.txt) |
| Host `npm run build` | Passed; bundle-size warning | [Build log](audit/delivery/frontend-build.txt) |
| Django `check` | No issues, 0 silenced | [System check](audit/delivery/system-check.txt) |
| Django `makemigrations --check --dry-run` | No changes detected | [Migration check](audit/delivery/migration-check.txt) |
| Full browser walkthrough, first fresh workflow | PASS; workflow 2; 20 completed tasks and 20 completion events | [Run 1](audit/delivery/walkthrough-run1.json) |
| Full browser walkthrough, second fresh workflow | PASS; workflow 108; 20 completed tasks and 20 completion events; 140 inspected task pages | [Run 2](audit/delivery/walkthrough-run2.json) |
| Browser pagination and signed token-expiry scenarios | PASS; 108 unique workflows across 9 pages; newest 108 and oldest 1 opened in UI | [Session/pagination report](audit/delivery/session-pagination.json) |

Startup and checks were executed from the repository root unless a working directory is shown:

```bash
# The original environment had DEBUG=False. The guarded seed correctly refused
# that configuration. This invocation-only local override does not edit .env.
DEBUG=True docker compose up --build --wait -d
docker compose exec backend python manage.py migrate --noinput
docker compose exec backend python manage.py seed_launchops
docker compose exec backend python manage.py seed_launchops --pagination-demo
docker compose ps

docker compose exec -e REDIS_URL=redis://redis:6379/15 backend python manage.py test --noinput > /tmp/t.log 2>&1; tail -30 /tmp/t.log
docker compose exec backend python manage.py check
docker compose exec backend python manage.py makemigrations --check --dry-run

cd frontend
npx tsc --noEmit
CI=true npm test
npm run build
```

[`docker compose ps`](audit/delivery/services.txt) showed the backend, PostgreSQL and Redis healthy, with frontend, worker and beat running. Migrations reported no pending migrations. One backend run was interrupted when the local DEBUG configuration required recreating the backend; the complete final run above passed. Expected cache-failure tracebacks in the test log come from deliberate Redis-outage tests, not suite failures.

The AGENTS.md command `CI=true npm test -- --watchAll=false` was executed and rejected by Vitest with `Unknown option --watchAll`. This checkout already uses Vite/Vitest, so the supported non-watch `CI=true npm test` was used. The [failed compatibility invocation](audit/delivery/unsupported-cra-test-flag.txt) is retained rather than represented as passing.

Browser commands used the installed Chrome and the documented Playwright version. Each report records actual role entry paths, task actors, completion modes, hostile-request status codes, and browser errors:

```bash
npm install --prefix /tmp/launchops-browser playwright@1.51.1
SKIP_INSPECTION=1 LAUNCHOPS_REPORT_DIR=/tmp/launchops-delivery-run1 NODE_PATH=/tmp/launchops-browser/node_modules node scripts/walkthrough.cjs
LAUNCHOPS_REPORT_DIR=/tmp/launchops-delivery-run2 NODE_PATH=/tmp/launchops-browser/node_modules node scripts/walkthrough.cjs
LAUNCHOPS_REPORT_DIR=/tmp/launchops-session-pagination NODE_PATH=/tmp/launchops-browser/node_modules node scripts/check_session_pagination.cjs
```

Both walkthroughs used the four landing-page role entries, then registered a new audit employee and launched that employee's workflow through HR's form. They acknowledged the seeded lessons, failed an assessment, retried and passed the assessments, completed each task as its owner, created an employee support blocker, verified final signoff was rejected, resolved the blocker as HR, and signed off. Repeated completion requests returned `changed: false`, with one completion event per task. Wrong-employee reads/completions, wrong-role completion, skipped prerequisites, and forged simulation evidence were rejected. The second run demonstrates repeatability without resetting or reusing the first audit workflow.

The final browser session report follows every API page, verifies unique workflow IDs, and navigates actual Next buttons to the oldest workflow. The backend pagination tests separately exercise tied creation timestamps, role filtering before pagination, page boundaries, maximum page size, and preservation of historical evidence beyond 100 records. Frontend tests cover page navigation and stale response handling. Metrics are explicitly scoped to the displayed page.

For token-expiry checks, the browser script asks the local DEBUG-enabled backend to sign already-expired tokens in memory. Concurrent protected requests received actual authentication failures and completed after exactly one refresh. A session containing only a valid refresh token restored the current user. Expired and missing refresh tokens cleared stored identity, exited protected routes, and displayed a sign-in message without a retry loop. Backend tests also reject invalid token types, invalid/expired refresh tokens, inactive accounts and deleted accounts. Client tests cover rejected retries, non-401 failures, public auth failures, late concurrent 401s, and logout during a pending refresh.

LMS audit results and deliberate boundaries:

| Route family or coupling | Finding and resulting behavior |
| --- | --- |
| Course/chapter/quiz authoring | Instructor role plus ownership is required. HR, IT, managers and employees cannot author. Public registration cannot create instructors. |
| Lesson content | Chapter lists leaked content to unenrolled viewers even though detail reads required enrollment. Unenrolled previews now omit content. Enrolled employees retain training access. |
| Draft courses | Quiz detail/taking/submission and section reads now respect parent-course publication; owners retain author access. |
| Enrollment | Employees could call legacy enrollment mutations directly despite hidden UI controls. Employee mutations now return 403; HR workflow services retain assignment ownership. |
| Chat | Auto-enrolled employees could access chat through REST/WebSocket membership checks. Both transports now require legacy student membership or instructor ownership. |
| Live quiz | Employee training enrollment no longer authorizes Django WebSocket joins, room lookup, private bootstrap, or result routes. Existing student/instructor paths and service-secret checks remain. The optional Go runtime was not launched. |
| Scheduling | Employee-inclusive student permissions granted legacy breaks/saved schedules. Student-only and legacy learner gates now separate scheduling from training. |
| Leaderboards | Employee-facing access was already denied, but legacy students could see employee scores on a shared course. Non-owner rankings now exclude employees; course instructors retain their course results. |
| Intended integration | Training deliberately reuses lessons, enrollment and server grading. The quiz submission wrapper locks the onboarding row and writes training audit events only for the assigned employee/track. This shares lock order with retry and training completion. |
| Public metadata | Published course/quiz metadata remains readable to authenticated users. Legacy students/instructors may look up room metadata by code; actual room participation still requires enrollment/host authorization. |

`onboarding/test_legacy_boundaries.py` covers REST role boundaries, retained employee training and legacy student access, score isolation, content/draft restrictions, and employee WebSocket rejection. The full suite also retains the existing legacy LMS tests.

Persistent-state verification used this read-only Compose shell command:

```bash
docker compose exec backend python manage.py shell -v 0 -c "from onboarding.models import Onboarding, Event; from users.models import User; from django.db.models import Count; demo=Onboarding.objects.get(employee__username='demo_employee'); print({'demo_workflow_id':demo.pk,'demo_completed_steps':demo.steps.filter(completed_at__isnull=False).count(),'workflow_count':Onboarding.objects.count(),'users':User.objects.count(),'audit_completions':list(Event.objects.filter(action='completed',onboarding__employee__username__startswith='audit_employee_').values('onboarding_id').annotate(total=Count('id')))})"
```

The initial read-only snapshot was 2 users and 0 workflows/steps/events. The [final snapshot](audit/delivery/data-snapshot.txt) was 114 users and 108 workflows: the shared demo workflow remained ID 1 with 0 completed tasks, and audit workflows 2 and 108 each had 20 completion events. The optional pagination fixture flag adds 105 clearly named employees/workflows; their passwords are unusable. Seed regression tests check repeatability, transaction rollback on collisions, production refusal, and preservation of passwords, evidence, completion timestamps and publication settings.

[Desktop landing preview](audit/delivery/landing-desktop.png) and [mobile landing preview](audit/delivery/landing-mobile.png) were captured from the Compose frontend with Playwright. The mobile check returned no horizontal overflow.

Remaining limitations: this is a single-workspace demonstration with simulated provisioning and self-reported external evidence. Tokens remain in tab-scoped session storage; refresh tokens are not rotated or revoked on server logout and keep their existing lifetime. Existing local demo accounts are preserved when DEBUG changes, so this seeded database must not become a production database. The optional Go/Mongo live profile was not started or browser-tested. The frontend build still warns about bundle size. This audit and these tests establish the described behavior, not a production security assessment.
