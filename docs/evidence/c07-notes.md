# c07 notes: workflow repository, compare-and-set, ported API

Scope: `backend/onboarding/{domain,repository,serializers,views,urls,pagination}.py`, ported tests, seed options.
This file records decisions and deviations only; it contains no measured figures.

## Decisions
- **Replay response (user decision).** `POST .../steps/<key>/complete/` returns `{key, state: "done", completion}`.
  The winner and every replay get byte-identical bodies (spec 10.1). A replay is flagged only by the response header
  `Idempotent-Replayed: true`. v1's `changed` field is gone from this endpoint; `resolve/` keeps v1's `{changed}`.
- **One workflow per employee (user decision).** v1 had `OneToOneField(employee)`. `ensure_indexes` now builds
  `employee_id_1` as a unique index with `partialFilterExpression: {employee_id: {$type: "objectId"}}`. The partial
  filter keeps documents without an employee (contract-fixture tests, the provisioning smoke script) out of the index.
  An older plain `employee_id_1` index is dropped and rebuilt (codes 85/86). Queries by employee add `$type: "objectId"`
  (`repository.employee_filter`) because the planner only uses a partial index when the query implies its filter; I
  checked this with `explain()` (COLLSCAN without it, IXSCAN with it). v1's 400 pre-check is kept, and a race now
  returns 409 (DuplicateKeyError), as v1 did on IntegrityError. Spec 4 lists `{employee_id}` without `unique`, so
  this is a deliberate deviation from the spec text.
- **Ordering.** The list is sorted by `_id` descending only (rule: never order by timestamp). v1 used `-created_at, -id`.
  The `{created_at:-1}` index from spec 4 is left in place but unused. ObjectIds come from the Django process, so
  `_id` order follows creation order for a single backend.
- **Created document.** One `insert_one` with `_id` generated first. It has 20 open steps (`due_at` = start_date +
  offset at 17:00 UTC, as in v1), empty `issues` and `events`, `version: 1`, `status: "active"`, and one `pending` job
  per `CONNECTORS` entry. `updated_at` is set to the creation time, not null, because the contract requires a date.
  `fence` is stored as Int64, as in the provisioning smoke script.
- **Storage types.** User references (`employee_id`, `manager_id`, `created_by`, `completion.by`,
  `issues.resolved_by`) are ObjectIds. `events.actor` is a user id hex string, matching the string type of Go's
  `provisioner:<worker>`. Issue ids are ObjectIds.
- **Millisecond timestamps.** Every written datetime is truncated to milliseconds (`repository.now_ms`), so the
  winner's response equals the stored completion that replays return.
- **Event types** keep v1 names: `completed`, `rescheduled`, `issue_resolved`. No separate event is written when an
  issue is raised (v1 had none either).
- **Reschedule** writes `steps.<index>.due_at` positional paths. This is safe because the version guard pins the
  snapshot and the steps array never reorders.
- **resolve_issue** is guarded by `version` plus `issues: {$elemMatch: {id, resolved_at: null}}`, and writes through
  `arrayFilters [{"i.id": id}]`.
- **Profile proof** needs the employee's username. `complete_step` reads it once, before `mutate`, only for `profile`,
  so `build` stays pure.
- **Response shapes.** Steps are addressed by `key` (v1 used integer ids). `dependencies` is `[{key, title,
  completed}]` and `reviewed_tasks` lists 19 step keys. `state` is `done|blocked|ready` (v1 said `completed`), and
  there is a new `overdue` field. `evidence` in task-step output re-merges `_proof` as in v1; storage keeps
  `completion.evidence` and `completion.proof` separate. Workflow detail/list show `events` newest first (the array
  reversed, as v1 showed them), and `timeline/` returns them in array order. The workflow output adds `status`. The
  access step detail includes read-only `provisioning` panel data (spec 8).
- **Idempotency-Key** is optional. When present it must be 1-255 printable ASCII characters (no spaces), otherwise 400.
  This is new validation; v1 had no header.
- **Seed.** v1's `--walkthrough-employee`, `--pagination-demo` and the demo workflow are ported because test_demo
  covers them. Without a transaction, every collision (demo roles, walkthrough name, pagination users) is checked
  before any write. Pagination users get unusable passwords.
- **Tests** use direct collection writes (`testing.mark_done`, `reset_steps`) as fixtures, in place of v1's
  `Step.objects.update(...)`. Application code writes business state only through `repository.py`.
- `users/tests.py::test_old_onboarding_routes_are_unavailable` (t04) asserted the routes did not exist. c07 adds
  them on purpose, so it now asserts 401 without a token, plus 404 for the old integer `steps/<id>/` route.
- README line about `_v1_reference` updated after deleting the directory.

## v1 behaviors not preserved exactly
- No `created` event at workflow creation (the task says events start empty). v1's pagination test counted one
  event on the old workflow; the port asserts none.
- The overdue scan task and its `overdue` events are gone. Overdue is computed on read, and the port asserts that
  reads write nothing.
- Create-time errors: format errors (bad ObjectId, bad date, missing field) are reported first; user existence and
  role errors come only after the format passes. v1 reported all field errors at once.
- An invisible workflow on `complete/` returns 403 with "This onboarding is not visible to you." (v1 view: DRF's
  default 403 message). Status codes are unchanged.
- v1 seed checks for `is_staff`, `is_superuser` and inactive users have no v2 equivalent; the v2 users schema has
  none of these fields.
- `test_final_signoff...` used to clear the per-instance prerequisite rows. v2 prerequisites live in the blueprint,
  so the test patches `domain.PREREQUISITES['ready']` instead.
- Query-budget tests now count MongoDB commands through a listener-enabled client, in place of SQL queries: list =
  aggregate (count) + find + one `$in` users find; task detail = find + one `$in` users find.

## Guards and the tests that fail when each is removed
I checked this by editing `guard_filter` temporarily, running the tests, and restoring the file.
- `version` + step `$elemMatch` both removed: `test_twenty_threads_one_step_complete_once_with_identical_responses`,
  `test_same_key_concurrency_completes_once`, `test_stale_snapshot_reschedule_to_same_date_is_a_noop_after_retry`,
  `test_stale_profile_proof_is_rebuilt_from_the_new_start_date` and `test_retry_exhaustion_returns_409_without_changes`
  fail. In-suite negative control: `test_negative_control_without_guards_duplicates_are_detected`.
- `version` removed (step `$elemMatch` kept): the three stale-snapshot/exhaustion tests above fail. In-suite control:
  `test_stale_snapshot_without_version_guard_records_a_duplicate`.
- Step `$elemMatch` removed alone (`version` kept): no completion test fails, because the version guard also gives
  exactly-once. This matches the spec's layering. `test_step_element_guard_alone_gives_exactly_once` (version dropped,
  `$elemMatch` kept) shows the `$elemMatch` alone is enough, and the all-guards-removed control shows duplicates
  appear once both are gone.

## Left undone (by instruction: task t10)
- The `access` provisioning gate, IT retry/override transitions and the `provisioning/<connector>/retry|override/`
  endpoints. Spec 10 item 6 (IT transitions) is therefore not covered here.
- Frontend (t09): it still calls the v1 integer step routes and reads `changed` and `state: "completed"`.
- t10 note: once the access gate exists, the ported tests that complete `access` (matrix, 20-step flow, seeded demo)
  will need the jobs set to `succeeded`/`overridden` first.
