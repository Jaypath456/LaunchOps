# LaunchOps v2 spec

Source of truth for all v2 tasks. If code and this file disagree, stop and report; do not silently pick one.

## 1. Scope

v2 = an onboarding-only workflow API (Django + MongoDB) plus a horizontally scaled Go `provisioner` that creates the new hire's
accounts in external systems, simulated by a Go `mock-saas` with injected faults.

Keep from v1: the 20-step workflow, 4 roles and their permissions, evidence forms, prerequisites, issues raised by
receipt/feedback, final sign-off rules, reschedule, pagination.

Removed: the LMS (courses, quizzes, schedule, chat), the old Go live quiz, Channels/WebSockets, Celery, Redis, PostgreSQL,
the Django ORM and admin, public registration, overdue background scanning (overdue is now computed on read), old load tests, old review/audit docs.

Not building: multi-tenancy, SSO, uploads, templates collection or editor, brokers (Kafka etc.), consensus, gRPC, Kubernetes,
replica set, change streams, Prometheus/Grafana, deployment. Stretch only: /metrics, a real GitHub connector, a Locust load test, Playwright.

## 2. Why the system is split this way

- Django is the system of record for workflow rules and step completion.
- External side effects (creating accounts elsewhere) are slow, fail partially and must not happen twice. A separate Go service runs
  them, can crash and restart without touching the API, and scales to 3 replicas. Go suits network-bound concurrent workers.
- No broker: the job state inside the workflow document is the durable queue. Creating a hire writes the workflow AND its pending jobs
  in one MongoDB write, so nothing can be lost between "saved" and "enqueued". Workers poll and claim with compare-and-set.
- Both services write the same document with strict field ownership (section 6). DESIGN.md explains the trade-off and what a larger
  system would do (outbox + queue, provisioner-owned store).

## 3. Services and layout

| service | what | replicas |
|---|---|---|
| `mongo` | mongo:8, standalone; databases `launchops` and `mocksaas` | 1 |
| `backend` | Django 6 + DRF + simplejwt, PyMongo only (no ORM) | 1 |
| `provisioner` | Go worker (`/healthz`) | 3 |
| `mock-saas` | Go HTTP service simulating Workspace/Slack/GitHub account APIs | 1 |
| `frontend` | React + TypeScript + Vite | 1 |

`backend/` holds the Django project; `backend/onboarding/`: `db.py` (tz-aware collection accessors; the only connection code),
`blueprint.py` (the versioned workflow definition + connectors, `BLUEPRINT_VERSION`), `graph.py` (`validate_blueprint`: unique keys, known
roles, prerequisites exist, no cycles via Kahn's algorithm), `domain.py` (pure rules: evidence validation, readiness, overdue, permissions,
proofs), `repository.py` (the only writer of business state), `auth.py` (users collection, login, refresh, actor), `views.py`, `serializers.py`,
`indexes.py` + `ensure_indexes` and `seed_launchops` commands, `testing.py` (`MongoTestCase`: a unique database per test class, dropped after).
`provisioner/` is one Go module: `cmd/provisioner`, `cmd/mocksaas`, `internal/jobs`, `internal/connectors`, `internal/mocksaas`, `internal/backoff`.
`contracts/provisioning_job.schema.json` + `contracts/fixtures/*.json` are shared by Python and Go tests. `scripts/chaos/` holds the chaos tooling.

## 4. Data model (database `launchops`)

`users`: `{_id, username (unique), display_name, role: hr|it|manager|employee, password (Django hasher)}`.

`workflow_instances` (one document per hire):
```
{ _id, blueprint_version, employee_id, manager_id, created_by,
  job_title, department, start_date: "YYYY-MM-DD", status: "active"|"ready", created_at,
  version: 1,                                   # guards business state (Django only)
  steps: [{ key, status: "open"|"done", due_at,
            completion: null | {by, at, evidence, proof, request_id} }],
  issues: [{ id, step_key, detail, resolution, resolved_at, resolved_by }],
  events: [{ type, step_key, actor, at, detail }],          # append-only; array order = event order
  provisioning: [{ connector, idempotency_key: "<instance_id>:<connector>",
                   payload: {username, display_name, department},
                   status: "pending"|"running"|"retry"|"succeeded"|"dead"|"overridden",
                   attempts, fence, lease_owner, lease_until, next_attempt_at,
                   last_error, external_ref, override_reason, updated_at }] }
```
Step structure (title, owner role, prerequisites, form, offset) comes from `blueprint.py`; instances record `blueprint_version`.
Readiness and overdue are computed on read: `done` | `blocked` | `ready`; `overdue = open and not blocked and due_at < now`.
Indexes: unique `users.username`; instances `{employee_id}`, `{manager_id}`, `{created_at:-1}`,
multikey `{"provisioning.status":1,"provisioning.next_attempt_at":1}`. A `$jsonSchema` validator on `workflow_instances`, built from
`contracts/provisioning_job.schema.json`, enforces the job shape for both writers. `ensure_indexes` is idempotent.

## 5. Business mutations (Django compare-and-set)

```
def mutate(instance_id, build, retries=5):
    for attempt in 1..retries:
        doc = find_one({_id})
        _between_read_and_write()            # no-op; tests patch it to inject a competing write
        plan = build(doc)                    # pure; raises domain errors; returns update, array_filters, result
        if plan.noop: return plan.result     # replay of an already-done step
        res = update_one(guard_filter(doc, plan), {**plan.update, "$inc": {"version": 1}},
                         array_filters=plan.array_filters, write_concern=majority)
        if res.matched_count == 1: return plan.result (+ attempts)
    raise Conflict  -> HTTP 409
```
`guard_filter` (one small function; tests patch it): `{_id, version: doc.version}` plus, for step completion,
`steps: {$elemMatch: {key, status: "open"}}`. Use `matched_count`.
Pitfall: with `arrayFilters`, an update whose filter matches no element still succeeds and still applies `$inc`/`$push`; the `$elemMatch`
in the query filter prevents that.
Layering: step transitions are monotone (open -> done), so the `$elemMatch` guard alone gives exactly-once completion; the `version` guard
keeps every mutation validating against a consistent snapshot (reschedule, sign-off rules) with one retry loop.
Idempotency: `Idempotency-Key` header stored as `completion.request_id`; a done step returns its original completion (200, no new event).
Permission checks run before the replay check. One update writes status, completion, event, any raised issue and, for `ready`, `status: "ready"`.

## 6. Provisioning: ownership and state machine

Ownership: the provisioner only `$set`s fields inside one `provisioning` element and `$push`es events whose type starts with `provisioning.`.
It never touches `version`, `steps` or `issues`. Django creates jobs inside the instance insert and performs the IT transitions. Job changes
never bump `version`. `succeeded` is terminal, so a stale step-completion snapshot can only reject, never wrongly pass, the `access` gate.

Every transition is a compare-and-set on the job element (guard via `$elemMatch` in the query filter, write via `arrayFilters: [{"j.connector": c}]`):

| transition | actor | guard | effect |
|---|---|---|---|
| claim | provisioner | `{connector, status: seen_status, fence: seen_fence}`, where the job is `pending`/`retry` with `next_attempt_at <= now` or `running` with `lease_until < now` | `running`, `fence = seen_fence + 1`, `attempts + 1`, `lease_owner`, `lease_until = now + LEASE` |
| succeed | provisioner | `{connector, status: running, fence: f}` | `succeeded`, `external_ref`, event |
| retryable failure | provisioner | same | `retry`, `next_attempt_at = now + full_jitter(min(cap, base * 2^(attempts-1)))`, `last_error`, event; `dead` when `attempts >= MAX_ATTEMPTS` (5) |
| permanent failure | provisioner | same | `dead`, `last_error`, event |
| manual retry | Django (IT) | `{connector, status: dead}` | `pending`, `attempts = 0`, `next_attempt_at = now`; `fence` unchanged |
| override | Django (IT) | `{connector, status in [pending, retry, dead]}` | `overridden`, `override_reason` (required), event; never while `running` |

`fence` increases on every claim and is never reset (fencing token). `attempts` counts the current round and is reset by a manual retry.
Resetting the fence would let a stale worker from an earlier round finish a newer attempt.

Provisioner: `WORKERS` goroutines per replica (default 4); each finds a small batch of due candidates, picks one at random, tries to claim;
claim lost -> next; nothing due -> sleep with jitter (1-5 s). Calls carry `Idempotency-Key = idempotency_key` (constant across attempts) and a
context deadline `CALL_TIMEOUT` (5 s); startup enforces `LEASE` (30 s) > `CALL_TIMEOUT` + margin, so no lease renewal is needed and fencing covers violations.
Classification: 2xx success; 409 "in progress", 429, 5xx, timeouts and connection errors retryable; other 4xx permanent.
SIGTERM: stop claiming, wait up to 20 s for in-flight jobs, exit (unfinished jobs are reclaimed after the lease).
Logs: `log/slog` JSON with `worker_id`, `job_key`, `fence`, `attempt`, `outcome`; a finish matching nothing logs `fence_rejected`. `/healthz` pings Mongo.
Worker clocks drive leases and backoff (single host; skew is a stated limitation).
Django: completing `access` requires every job `succeeded` or `overridden`; job results go into its evidence. The access task page shows a provisioning panel
(status, attempts, last error, lease owner, external ref) with IT retry/override.

## 7. mock-saas (simulated external system; database `mocksaas`)

- `POST /v1/{connector}/accounts`, header `Idempotency-Key` (required), body `{username, display_name, department}`:
  new key -> insert a `requests` row `processing` (unique key; this is the atomic claim), create the account (unique `(connector, username)`), store
  the response, mark `done`, respond. Same key + same body + `done` -> replay stored status and body. Same key + `processing` -> 409 "in progress".
  Each claim row also stores the intended account id, `started_at` and a `claim` counter. A `processing` row older than `PROCESSING_TIMEOUT`
  (default 10 s) with the same key and body is taken over by a compare-and-set on `claim` (one winner; others get 409 "in progress"), then
  reconciled: the account is inserted with the stored id, or found if it already exists, and the response is stored. Rows without an intended id
  are never taken over.
  Same key + different body -> 422. Account exists under another key -> 409 conflict.
- `GET /admin/accounts`, `GET /admin/stats` (created, replayed, in-progress conflicts, each fault fired), `POST /admin/faults`, `GET /healthz`.
- Faults (env defaults, runtime-adjustable): `error_rate` (503 before doing anything), `hang_rate` (sleep past the client timeout),
  `drop_after_commit_rate` (commit, then hijack and close the connection with no response). The last one is the key case: the work happened, the caller cannot know.

## 8. API (prefix `/api/onboarding/`; `/api/auth/` keeps login/refresh/me shapes; no registration)

| method + path | notes |
|---|---|
| `GET/POST workflows/` | role-scoped, paginated; POST: employee_id, manager_id, job_title, department, start_date |
| `GET/PATCH workflows/<id>/` | PATCH = reschedule (HR only) |
| `GET workflows/<id>/steps/<key>/` | form, state, overdue; provisioning panel data for `access` |
| `POST workflows/<id>/steps/<key>/complete/` | optional `Idempotency-Key` |
| `POST workflows/<id>/issues/<issue_id>/resolve/` | |
| `GET workflows/<id>/timeline/` | events in array order |
| `POST workflows/<id>/provisioning/<connector>/retry/` and `/override/` | IT only |
| `GET directory/`, `GET /healthz` | |

IDs are ObjectId hex strings. Keep response shapes close to v1.

## 9. Contract

`contracts/provisioning_job.schema.json` describes one `provisioning` element; `contracts/fixtures/` has one example per status.
Python: fixtures and `create_workflow` output validate; the `$jsonSchema` validator rejects a malformed element.
Go: fixtures decode into the job struct and re-encode without losing fields.

## 10. Tests (real MongoDB; no mongomock)

Django: 1) 20 threads complete one step: one completion, one event, identical responses. 2) negative control: patch `guard_filter` to drop
the guards; assert duplicates are detected. 3) N threads, N different steps: all succeed, `version == start + N`, N events. 4) stale snapshot via
`_between_read_and_write` on a REAL cross-step rule (do not invent one). 5) idempotency replay and same-key concurrency. 6) IT transitions:
retry only from dead, override never while running, fence untouched.
Go (`go test -race`; Mongo tests use `MONGO_URI` and skip if unset; mock-saas handler in-process via `httptest`): 7) N goroutines claim one job:
one wins. 8) stale worker cannot finish (`fence_rejected`). 9) a job left `running` is reclaimed after the lease. 10) drop-after-commit: retry
replays, exactly one account, `succeeded` with the original `external_ref`. 11) permanent 4xx -> `dead` without retries; retryable errors back off
within bounds; `MAX_ATTEMPTS` -> `dead`. 12) mock-saas: 50 concurrent same-key requests -> one account; same key + different body -> 422.
13) graceful shutdown. Every guard (step `$elemMatch`, `version`, claim CAS, fence) has a test that fails when the guard is removed; record which.

## 11. Chaos campaign

`scripts/chaos/run.sh` on a fresh compose project `launchops-chaos`: 3 provisioner replicas; N hires created through the API (3 jobs each);
faults on; every K seconds `docker kill` a random provisioner container and `docker start` it again (logs survive); wait until every job is
terminal or a timeout; collect logs and mock-saas stats; then `scripts/chaos/verify.py` checks:
- every job terminal; none left `running`, `pending` or `retry`;
- every `succeeded` job's `external_ref` equals the mock-saas account id for that (connector, username);
- zero jobs dead from a 409 account conflict (that would mean idempotency failed);
- every mock-saas account belongs to a job; dead jobs whose account exists anyway are REPORTED (side effect happened but we gave up; IT override resolves it);
- faults were exercised: replays > 0, fence rejections > 0, kills > 0, drops > 0. A run where a fault never fired proves nothing about it.
Output: `docs/evidence/chaos/<run-id>/{COMMAND.txt, ENV.txt, config.json, verify.json, provisioner.log, mocksaas-stats.json}`.

## 12. Evidence

Every number in a doc comes from `docs/evidence/<name>/` with `COMMAND.txt`, `ENV.txt` (OS, CPU, Docker, MongoDB, Python, Go versions) and raw output.
README boundaries: local demo; simulated accounts, payroll, identity and devices; external systems are a local mock with injected faults; single host;
no production, HA or failover claim; browser walkthrough is manual.
