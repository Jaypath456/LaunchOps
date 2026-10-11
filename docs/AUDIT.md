# LaunchOps product audit — 27 September 2026

Scope: the pages and actions exposed to the four README demo roles, all 20 onboarding tasks, the linked five lessons and assessments, API authorization, existing workflow preservation, migration/seed behavior and local Docker operation. The legacy instructor, live-session, messaging and scheduling products were covered by backend regression tests, not a complete browser acceptance audit.

A database backup was taken before mutation at `/tmp/launchops-before-audit.sql`. No volume was deleted. The original `jay` workflow (2), including its completed profile and year-1111 start date, is retained. The intended date cannot be inferred reliably; the UI flags it and HR can correct it. A separate audit copy exercises date correction and completion without rewriting the source. The demo workflow and clearly named audit workflows are intentionally completed during acceptance testing.

## Task contracts

Each completion follows `TaskDetail` → `POST /api/onboarding/steps/{id}/complete/` → `complete_step` → locked workflow/step rows → `Step.evidence`, `completed_by`, `completed_at` and one `Event(action=completed)`. Permissions and prerequisites are checked on the server before mutation. Retrying a completed task as its owner returns `changed: false` and preserves its evidence. List/detail GETs subsequently return the stored result. Simulation references and proof metadata are generated on the server; clients cannot submit them.

**S** = explicit simulation; real-world effect remains unverified. **L** = actual local artifact or acknowledgment; statements about external events remain unverified. **A** = actual server-graded assessment plus recorded lesson acknowledgments; neither identity nor reading is independently verified. All tasks are intended to support the local demo contract, not certify real employee readiness.

| # | Task / owner | What the person does | Stored evidence and completion rule | Unlocks after | Boundary |
| --- | --- | --- | --- | --- | --- |
| 1 | Verify employee profile / HR | Review displayed employee, team, job and start date; run demo match | Selected match, snapshot of displayed profile, server demo reference; realistic date required | None | S: no identity verification |
| 2 | Confirm paperwork / HR | Select demo agreement and run review | Versioned demo document selection and generated result | Profile | S: no signature verification |
| 3 | Confirm payroll setup / HR | Select a simulated payroll schedule | Schedule and generated demo setup result | Paperwork | S: no payroll integration or financial input |
| 4 | Assign required policies / HR | Publish the displayed demo handbook packet locally | Packet selection, server handbook version and completion actor/time | Profile | L: publication in this workspace; no external delivery |
| 5 | Prepare equipment / IT | Select demo laptop/workstation and plan delivery | Device choice, bounded delivery date, generated asset ID reused downstream | Profile | S: no physical equipment or shipment |
| 6 | Provision work access / IT | Choose and run a demo access bundle | Bundle and generated provisioning result | Profile | S: no accounts created |
| 7 | Verify MFA / IT | Choose simulated authenticator method and run check | Method and generated demo result | Access | S: no enrollment tested; no recovery codes |
| 8 | Verify device security / IT | Review assigned demo device and run the baseline | Encryption/protection baseline, linked simulated asset, generated result | Equipment | S: no endpoint queried |
| 9 | Complete IT handoff / IT | Review equipment/MFA/security records; publish support channel | Support channel and linked simulated asset | MFA, device security | S: local handoff artifact, no delivery verification |
| 10 | Assign a mentor / manager | Save mentor assignment and introduction plan | Mentor name and plan attributed to the assigned manager | Profile | L: no message sent or introduction verified |
| 11 | Attend team orientation / manager | Record date, place and discussion summary | Nonfuture attendance date, location and summary | Equipment, mentor | L: self-reported attendance |
| 12 | Set first-week goals / manager | Define deliverable, success criteria and review date | Plan owned by workflow employee/manager | Orientation | L: saves an actual plan, not achievement of the goal |
| 13 | Schedule check-ins / manager | Save a date and meeting channel | Local check-in plan | Orientation | L: no calendar invitation sent |
| 14 | Manager check-in / manager | Review displayed goal/check-in records; attest to discussion | Notes and explicit attestation | Goals, check-ins | L: discussion remains self-reported |
| 15 | Confirm contact preferences / employee | Choose work channel and availability | Selected channel and availability | Profile | L: actual preference record; no contact verification |
| 16 | Review handbook / employee | Read displayed handbook and acknowledge | Acknowledgment and server-supplied version | Policies | L: acknowledgment, not proof of reading |
| 17 | Complete security training / employee | Acknowledge five lessons, pass five assessments, return and finish | Unique lesson reviews; server-graded submissions; archived failed attempts; score snapshot in task evidence | Access | A: every published assessment ≥70%; every public lesson acknowledged |
| 18 | Acknowledge equipment receipt / employee | Review assigned simulated asset/handoff and report condition | Condition and linked asset; Needs support creates an Issue | IT handoff | S: no physical receipt verified |
| 19 | Share first-week feedback / employee | Save feedback and explicitly identify blockers | Feedback/status; Needs support creates an Issue | Handbook, training | L: actual feedback record |
| 20 | Confirm onboarding readiness / HR | Review displayed evidence for 19 tasks, resolve blockers and approve demo closure | Review summary, attestation, 19 reviewed task IDs; server requires all complete, valid date and no open issues | All other 19 tasks (also dependency graph) | L: local signoff only; HR resolution is self-reported |

A local plan or self-report can still be untruthful. Text validation cannot establish that a conversation happened. The repair changes the claimed completion rule instead of treating arbitrary ticket text or a checkbox as independent verification. Simulation success is deterministic demo behavior, not a probe of an external system.

## Verified defects and repairs

- **READY misrepresented permission.** Dependency status and actor permission are now distinct throughout task/checklist views. Other roles see “Waiting for IT” (or the relevant owner), IT gets direct actionable links, and API role enforcement remains mandatory.
- **Fake verification forms.** Ticket/asset text and verification checkboxes were replaced with explicitly simulated operations and server-generated references, linked devices and profile snapshots. Historical evidence is retained and marked unverified.
- **Empty handbook and unrecorded lessons.** The task now contains a versioned demo handbook; lessons store unique acknowledgments and offer next/previous/course navigation. Training checks acknowledgments as well as assessment passes.
- **Unsafe seeded answer keys.** Two incident questions previously treated password sharing/public posting as correct. Only the exact defective seed fixtures are repaired. Historical submissions are retained without retroactive regrading.
- **Quiz retry history loss.** Retrying previously deleted the only full attempt record. Failed answers, score and timestamp are now archived; retries and new assessment submissions get activity events. Employee submission/retry/training completion share the workflow lock.
- **Assessment error and pass display.** The result page no longer dereferences a null result after non-404 failures. It uses the server's exact pass rule, avoiding a rounded 69.5% display suppressing retry. Nested malformed choice answers no longer raise an unhashable-type server error; forged score fields are rejected.
- **Signoff ignored reported trouble.** Receipt/feedback blockers now create records. HR sees and resolves them on final review, and the server checks all 19 tasks even on an incomplete historical dependency graph.
- **Dates and recovery.** Date-only display avoids UTC day shifts. Invalid historical dates are visible and require an explicit HR correction before profile/final approval. Future attendance cannot be asserted. HR can repair a missing/incomplete learning track without replacing completed training.
- **Dead ends and stale state.** Added a not-found route, course return paths, lesson navigation and task refresh on timer/focus. Forms reset on task changes. Changing selected workflows closes a pending date editor. Completion preserves prior evidence on retries.
- **Seed repeatability.** Existing passwords and publication settings remain intact. Repeated seeds do not duplicate users, courses, lessons, quizzes, questions, workflows, steps or events.

## Validation results

Confirmed results:

| Check | Result |
| --- | --- |
| Full backend regression suite, PostgreSQL | **256 passed**, 662.912 seconds. Expected Redis-outage tracebacks are deliberately injected by passing resilience tests. |
| Three tests added after the full run started | **3 passed**, 12.422 seconds: concurrent first completion, HR-only learning-track repair/preservation, and refusal of future attendance. |
| Frontend unit tests | **3 passed**, 2 suites. |
| Final frontend production build | **Compiled successfully**; main JavaScript 316.98 kB gzip. |
| Django check / migration drift check | No issues; no model changes missing migrations. Migration 0005 applied. |
| Four-role page inspection | **140 task page visits**, covering all 20 tasks on each workflow visible to each demo role. |
| Browser workflow acceptance | **Passed**. Demo workflow 1 and newly launched workflow 4 completed 20/20; migrated audit copy 3 retained its historical profile and completed the other 19. All roles submitted their own forms. |
| Direct API attacks | **7 checks passed**: another employee’s workflow/completion, another manager’s workflow/completion, employee attempting IT work, skipped prerequisites, and forged simulation proof. Repeated owner completions returned unchanged evidence and a single completion event. |
| Assessment retry | Failed the first assessment on the new workflow, retried through its result page, and passed all five quizzes. Lesson acknowledgments, server scores and task completion persisted. |
| Equipment issue and signoff | **Passed** on audit copy 5: employee selected Needs support; HR’s open page refreshed; signoff was rejected until HR recorded resolution, then succeeded. |
| Navigation and errors | **7 browser edge checks passed**, including lesson next/previous/course links, assessment result returns, injected result API 503 recovery, wrong-role instructor navigation and another employee’s unavailable task page. |
| Final running-site verification | **Passed** after resuming: fresh UI login for all four demo roles, 20/20 completion, visible simulation labels and no browser errors. Host/container backend and frontend source hashes match; migration 0005 is applied. See `audit/final-smoke.json`. |
| Data preservation | Source workflow 2, all 20 steps, all 8 original events and all 29 prerequisite edges exactly match the pre-audit SQL backup. Its one completed task and year-1111 date remain intact. Backup SHA-256 is in `audit/preservation.json`. |

All 20 task contracts in the table above were exercised through their assigned role’s browser form, API, saved evidence and completion event. **S** entries work as simulations only; the corresponding real-world operation remains unverified. **L** entries work as local records, with the stated limits on self-reported external actions. **A** grading and acknowledgment gates passed the local tests.

Measured during the browser run under concurrent regression-test load: workflow GET median **63 ms**, p95 **119 ms** (62 requests); task GET median **48.5 ms**, p95 **69 ms** (110 requests). Outliers were 1,165 ms and 740 ms respectively. These small local measurements are not a load-capacity claim. Query-budget tests passed (list ≤15 queries, training detail ≤12).

Evidence: `audit/page-inspection.json` contains the page inspection pass portion; `audit/walkthrough-initial.json` and `audit/walkthrough.json` together record 59 distinct task completions across the three workflows. The initial run stopped on an existing quiz option whose wording differed from the new fixture; the harness was corrected and resumed without resetting completed tasks. `audit/browser-edges.json` records the unresolved-equipment/signoff test. `audit/employee-waiting.png` shows the repaired employee state. Test/build logs are retained alongside these reports. No requested local test/build/browser check remained blocked.

The initial browser harness needed corrections for exact form-label selection and a reload during development compilation. Those harness failures are not counted as product acceptance passes. The initial backend run also correctly failed old tests that omitted the newly required lesson acknowledgments; fixtures and explicit missing-acknowledgment regression tests were then added.

## Files changed

- `backend/onboarding/blueprint.py`, `models.py`, `services.py`, `serializers.py`, `views.py`, `urls.py`: task contracts, stored lesson/attempt/issue records, authorization and validation, task review data, track/date repair and API endpoints.
- `backend/onboarding/migrations/0005_assessmentattempt_issue_lessonreview.py`: additive schema migration; no deletion/rewrite of workflow history.
- `backend/onboarding/management/commands/seed_launchops.py`: precise answer-key repair and preservation of existing publication choices.
- `backend/onboarding/tests.py`, `test_migrations.py`, `test_concurrency.py`: focused workflow, API, evidence, dates, retry, seed, query-budget and real migration tests.
- `backend/quizzes/grading.py`, `serializers.py`, `views.py`: malformed-answer handling, exact pass metadata, course return information and serialized/audited employee submissions.
- `frontend/src/pages/onboarding/TaskDetail.js`, `Workspace.js`, `workspace.css`, `taskState.js`, `taskState.test.js`: role-aware states, actionable links, review/evidence display, simulation forms, blockers, refresh and local date formatting.
- `frontend/src/pages/student/ChapterReader.js`, `QuizTake.js`, `QuizResult.js`, `frontend/src/App.js`: lesson/quiz navigation and acknowledgment, route-state resets, error handling and not-found page.
- `scripts/walkthrough.cjs`, `scripts/create_audit_fixture.py`, `scripts/browser_edges.cjs`, `scripts/final_smoke.cjs`, `README.md`, `docs/AUDIT.md`, `docs/audit/*`: repeatable acceptance tooling, audit evidence and documentation.

## Remaining limits

No external identity, signature, account, MFA, endpoint, payroll, shipment, calendar or messaging integration was verified or added. Existing historical evidence and scores are not upgraded into new proof. The handbook is demo content. There is no anti-cheating, identity check, reading detection or time-on-lesson guarantee. Course authoring can change lessons/questions after a score or acknowledgment; completion snapshots are retained, but there is no immutable course release/version system.

The original year-1111 date needs the intended date from HR. Very old valid historical dates may also be flagged by the bounded-date rule. Plans, attendance, feedback and issue resolutions remain attributable self-reports. HR/IT have workspace-wide access, not tenant isolation. Lists are capped at 100 workflows; large-scale pagination, production concurrency/load, accessibility coverage across assistive technologies and a production security review remain outside this acceptance run. Docker uses development defaults and a development frontend server. Passing tests does not establish a secure or fully functional production onboarding service.
