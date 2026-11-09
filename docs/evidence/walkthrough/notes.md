# Walkthrough notes

Result: `report.json` has `result: PASS`, empty `errors`, 14 checks. The run used the real stack (compose project `cc-e2e`:
mongo, backend, 3 provisioner replicas, mock-saas, Vite frontend) and Chrome through Playwright. mock-saas is a simulator.

## What the walkthrough covers
- Four role logins through the landing page. HR creates two fresh hires (A and B) through the create-hire form.
- Provisioners are stopped before the hires are created so the pending state is observable. With jobs pending, the access
  form is disabled and shows "Waiting for every job to succeed or be overridden"; a direct API completion returns 400
  "Every job must succeed or be overridden before completing access." After the provisioners start, hire A's three jobs reach
  `succeeded` with an external reference.
- Hire B: `workspace` and `slack` were set to `dead` through MongoDB (provisioners stopped). IT retried `workspace` (it
  succeeded) and overrode `slack` with a reason (`overridden`, reason shown); `github` succeeded by itself. Events
  `provisioning.manual_retry` and `provisioning.overridden` exist. IT then completed B's access step.
- Hire A: all 20 steps completed through the UI forms by demo_hr, demo_it, demo_manager and the employee, in dependency order.
  Each completion was replayed with its Idempotency-Key: identical body, `Idempotent-Replayed: true`, one `completed` event.
- Timeline: UI list equals the `timeline/` API in order (20 events for A; the Go `provisioning.*` events are in the array too).
- Final sign-off: `status` is `ready`.

## Changed
1. `backend/onboarding/serializers.py` (`event_detail`): **bug fix.** Go provisioner events store `detail` as an object
   (`{connector, attempt, fence, external_ref, ...}`). The API passed it through, the frontend rendered it inside `<strong>`,
   React threw ("Objects are not valid as a React child") and, with no error boundary, the whole workspace went blank for any
   user whose workflow had a provisioning event. The API now returns a string, `connector github, attempt 1, fence 1,
   external_ref ...`. Test added: `test_provisioning.ProvisioningTests.test_go_provisioner_event_details_are_serialized_as_strings`.
2. `docker-compose.yml`: **bug fix.** The frontend's `REACT_APP_API_URL` default was `http://localhost:18000/api`. The Vite dev
   proxy uses it as its target from inside the container, where `localhost` is the container: every `/api` call returned 500
   (ECONNREFUSED), reproduced in a fresh project with defaults. The default is now `http://backend:8000/api`; checked with a
   fresh default-port project (login through the frontend port returned 401 for bad credentials, 200 for demo_hr).
3. `scripts/walkthrough.cjs`: rewritten for the v2 flow (see above). New env: `COMPOSE_PROJECT`, `LAUNCHOPS_SCREEN_DIR`;
   the report is `report.json`.
4. `README.md`: removed "The frontend workflow returns in a later task"; added a Full stack section (commands verified in a
   fresh project, default ports, demo accounts and password, which the seed message referred to but README lacked).
   The original quick start was run exactly as written and passed (122 tests).

## Not fixed / unresolved
- `docs/DESIGN.md` does not exist, so the DESIGN.md check could not be done. Not created.
- README "Origins" and "AI tools used" are statements about history that no code or evidence file can verify; left as is.
- Nothing needed a change in `repository.py`, `domain.py`, `provisioner/internal/`, `contracts/` or the chaos tooling.

## Suspicious / worth knowing
- No React error boundary: any render exception blanks the whole app (this is how bug 1 showed up). Not changed.
- A hire whose start date is today shows an Overdue badge on `access` (due start-2 days). This follows the blueprint offsets,
  not a bug, but it looks odd in the screenshots.
- The duplicate-click check (`dblclick` on Complete) produced exactly one POST: the client's in-flight guard absorbs it. The
  server's exactly-once behaviour is shown by the same-key replay in every step, not by the double click.
- Dead jobs were produced by writing `provisioning.$[j].status` directly in MongoDB (as the task asked); that is test setup, not an application path.
- Two earlier walkthrough runs (one crashed on bug 1, one on my script) left extra `walk_*` hires in the `cc-e2e` database; it is deleted with the project.
- Working tree already had uncommitted changes to `.github/workflows/ci.yml`, `scripts/chaos/*` and `docs/CHAOS_RELIABILITY_REVIEW.md` before this task; untouched.
- Test results at the end: backend 122 tests OK; frontend typecheck clean, 45 tests passed (7 files), build succeeded.
