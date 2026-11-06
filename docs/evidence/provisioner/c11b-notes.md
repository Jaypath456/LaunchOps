# c11b notes: worker loop, HTTP connector, graceful shutdown

Scope: `provisioner/internal/connectors`, `provisioner/internal/worker`, `provisioner/cmd/provisioner` and their tests.
No changes to `internal/jobs`, `internal/backoff`, `internal/mocksaas`, Dockerfiles, compose or `backend/`.
mock-saas is a simulator; the connector talks to it and is not a real integration.

## Decisions

### Connector (`internal/connectors`)
- One `HTTP` client serves every connector; the job's connector name selects `POST /v1/{connector}/accounts`. Names must match
  `^[a-z0-9_-]+$` (otherwise a permanent error, no request sent).
- `Idempotency-Key` is the job's `idempotency_key`, so it is identical on every attempt and across workers.
- Every request runs under `context.WithTimeout(ctx, Timeout)` inside the connector, even if the caller passed a context without a
  deadline; the worker also applies `CALL_TIMEOUT`.
- Classification: 2xx with `external_ref` = success. 409 whose body is `{"error":"in progress"}`, 429, 5xx, and every failure without a
  complete response (deadline, refused, reset, connection closed without a response, body read error) = retryable. Every other 4xx,
  including mock-saas's 409 "account exists", = permanent. This follows the spec's wording ("409 in progress" retryable, other 4xx permanent).
- 1xx/3xx are permanent and redirects are not followed (`http.ErrUseLastResponse`), so a POST is never re-sent to another location.
- A 2xx without a readable `external_ref` is retryable: the retry replays the stored response under the same key; if it stays bad,
  `MAX_ATTEMPTS` makes the job `dead`, where IT sees it.
- Unknown error types count as retryable in `IsRetryable` (bounded by `MAX_ATTEMPTS`).
- `net/http` treats a request carrying `Idempotency-Key` as replayable and silently re-sends it on a new connection when a reused
  keep-alive connection fails. The connector clears `Request.GetBody`, so the body cannot be rewound and each job attempt is exactly one
  HTTP request; retries always go through the job state machine (attempt count, backoff, events). `TestNoTransportLevelResend` fails
  without this line (checked by removing it during development).

### Worker (`internal/worker`)
- `Pool` validates and copies its config in `Run`; nothing mutates the `Pool`, and there is no package-level mutable state.
  Randomness uses the goroutine-safe top-level `math/rand/v2`.
- Goroutines: `Workers` loop goroutines (`sync.WaitGroup.Go`) plus one closer that waits for them. Nothing else.
- Lease owner and `worker_id` are `WORKER_ID/<n>` per goroutine, so each lease names the exact goroutine.
- Loop: Candidates (batch 8) → shuffle → Claim each until one wins (`ErrClaimLost` → next) → process. Nothing due, every claim lost,
  or a store error → idle sleep: `backoff.Ceiling(idle+1, 1s, 5s)` with ±20% jitter, capped at 5s, reset after a claim; interrupted by shutdown.
- Contexts:
  - Candidates: the shutdown context.
  - Claim: `context.WithoutCancel(shutdown ctx)` + the store's op timeout, and only after a check that shutdown has not begun. A claim
    already sent therefore completes and its result is known (no orphaned claim); no claim starts after cancel.
  - Connector call: `WithTimeout(callCtx, CALL_TIMEOUT)`, where `callCtx` is `WithCancel(context.Background())` owned by `Run` (the abort
    switch). It is not derived from the shutdown context.
  - Succeed / RecordFailure: `WithTimeout(context.WithoutCancel(shutdown ctx), FinishTimeout=5s)` + the store's op timeout, so a call
    that completed during shutdown is still recorded.
- Outcomes logged once per attempt (`msg="job attempt"`, with `worker_id, job_key, connector, fence, attempt, outcome, duration_ms`):
  `succeeded`, `retry`, `dead` (named with `jobs.NextState`), `fence_rejected` (warn; the loop continues and `Run` does not fail),
  `finish_failed` (error; the job stays running and is reclaimed after the lease, and the retry replays under the same key) and
  `abandoned` (warn; see shutdown). The store keeps its own `claimed`/transition lines (`component=store`).
- Shutdown: on cancel, loops stop before any new Candidates/Claim and idle sleepers wake. `Run` waits for the loops up to `DrainTimeout`
  and returns nil if they finish. If the timeout elapses, it cancels `callCtx`. An aborted call writes nothing (outcome `abandoned`), so
  the job stays `running` and is reclaimed after the lease. A call that returned successfully at that moment is still recorded. `Run`
  then waits for every goroutine (bounded: aborted calls return at once, finishes by `FinishTimeout`) and returns `ErrDrainTimeout`.
- `DrainTimeout` is taken literally (0 = abort in-flight calls as soon as shutdown starts); the 20s default is applied in config.
  The other optional fields (Batch, PollMin/Max, FinishTimeout, Now) default when zero.
- `Pool.Now` (default `time.Now`) is the worker clock for candidates, claims and backoff. Tests shift a second pool's clock past the
  lease instead of waiting: `jobs.LeaseMargin` is fixed at 5s, so a real lease cannot be made shorter than CallTimeout + 5s.
- No lease renewal; startup enforces `LEASE > CALL_TIMEOUT + 5s` (`jobs.Policy.Validate`) and fencing covers violations.

### Binary (`cmd/provisioner`)
- Env with defaults: `MONGO_URI=mongodb://localhost:27017`, `MONGO_DB=launchops`, `WORKERS=4`, `WORKER_ID`=hostname, `LEASE=30s`,
  `CALL_TIMEOUT=5s`, `MAX_ATTEMPTS=5`, `BACKOFF_BASE=1s`, `BACKOFF_CAP=60s`, `DRAIN_TIMEOUT=20s`, `MOCKSAAS_URL=http://localhost:8080`,
  `HEALTH_ADDR=:8081`. Collection `workflow_instances`; store op timeout 5s.
- Invalid configuration (unparsable values, `WORKERS<1`, negative drain, bad URLs, no worker id, failing `Policy.Validate`) logs every
  problem and exits 2. A run error, including `ErrDrainTimeout`, exits 1, so an unclean shutdown is visible. A clean drain exits 0.
- `signal.NotifyContext` for SIGTERM and SIGINT. `GET /healthz` pings MongoDB with a 2s deadline: 200 or 503. Health keeps answering
  during the drain and stops after the pool.
- No startup Mongo ping: `mongo.Connect` is lazy, and workers log and back off while MongoDB is unavailable; `/healthz` reports it.
- Index creation is left to Django's `ensure_indexes`; the provisioner creates none.

### Tests
- All Mongo tests use a real MongoDB via `MONGO_URI` and `t.Fatal` when it is unset (same no-skip rule as the c11a jobs tests;
  the spec's "skip if unset" is stricter here so CI cannot pass by skipping).
- mock-saas runs in process behind `httptest.NewServer` (a real server, so drop-after-commit's hijack works). An interceptor in front
  of it provides:
  - a gate that holds the first account POST. It reads the body to EOF first so net/http notices client disconnects, and it is released
    in cleanup.
  - a fixed 409 `{"error":"in progress"}` for the in-progress test. Producing that state for real needs a second request already running
    inside mock-saas.
- Faults use the real mock-saas knobs (`POST /admin/faults`) for drop-after-commit and hang.
- The goroutine leak check is goleak-style without a new dependency: after `Run` returns and idle connections are closed, no stack may
  contain `worker.(*Pool)`, `worker.(*worker)` or `net/http.(*persistConn)`. Test-side admin calls use a client without keep-alive so
  they leave no connection goroutines.
- Mutation checks run during development and then reverted:
  - deriving the call context from the shutdown context makes `TestGracefulShutdownDrainsInFlight` fail;
  - recording a failure for an aborted call makes `TestKilledMidCallReclaimedAfterLease` fail.
  These are not saved as evidence files.

## Left undone
- No Dockerfile or compose wiring for `cmd/provisioner` (out of scope; done later).
- The contract tests against `contracts/` are not part of this task (the directory does not exist yet).
- No measured latency/throughput figures; none are claimed.
- Clock skew between replicas is not handled (spec: single host, stated limitation).
- The `Store` interface in `internal/worker` exists so tests can record claims; `*jobs.Store` satisfies it unchanged.
