> Historical LMS load tests. These reports are not LaunchOps performance results. See ../README.md.

# Load Testing

Load tests for the LMS backend, covering both tiers:
- **REST** (`locustfile.py`, [Locust](https://locust.io/)): catalog
  browsing, course enrollment, quiz submission.
- **WebSocket** (`ws_load_test.py`, a standalone asyncio + websockets
  script - Locust doesn't have first-class WebSocket support): hundreds
  of students connected to the same live quiz session, all receiving a
  question reveal and all answering within the same instant.

`generate_graphs.py` renders the before/after comparison graphs from
both tiers' raw output. See [`REPORT.md`](REPORT.md) for a real run's
results and findings, including one genuinely surprising one.

## Why this exists

Two things from the earlier architecture review this proves out:

1. **The N+1/caching optimizations actually help.** `settings.LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS`
   lets you run the *exact same code path* with the catalog cache and the
   quiz-submit idempotency fast path switched off, so a before/after
   comparison isolates exactly the Redis contribution instead of
   comparing two different code versions.
2. **The system behaves under realistic concurrent load**, not just in
   unit tests - hundreds/thousands of simulated students hitting the
   catalog, enrolling, and submitting a quiz answer all around the same
   time (the `Promise.all` triple-fetch burst pattern the frontend
   already produces on every course page load is the realistic worst
   case, not a purely synthetic one).

## Setup

### 1. Install dependencies
```bash
pip install -r loadtests/requirements.txt
```

### 2. Start the stack with DEBUG=False
Load-testing `manage.py runserver` with `DEBUG=True` measures nothing
representative - always test against a production-ish ASGI server. If
using the project's `docker-compose.yml`, set `DEBUG=False` in your
`.env` first (see the root `.env.example`).

### 3. Seed load-test-scale data
This is what creates the `loadtest_student_NNNN` accounts the locustfile
logs in as, plus the load-test quiz they submit answers to:
```bash
python manage.py seed_demo --students 1000
```
Re-running this is safe (idempotent) - it won't duplicate students if
you seed a larger number later (e.g. `--students 2000` on top of an
existing `--students 1000` run just adds the extra 1000).

If you plan to test multiple population sizes (100/500/1000), just seed
the largest one once - Locust's `--users` flag controls how many of the
seeded accounts are actually exercised in a given run, not how many
exist.

### 4. Point locustfile.py at the same population size
`NUM_SEEDED_STUDENTS` at the top of `locustfile.py` must match (or be
less than or equal to) whatever `--students` value you seeded with, so
logins are spread across accounts that actually exist.

## Running

### Interactive (web UI)
```bash
locust -f loadtests/locustfile.py --host http://localhost:8000
```
Then open http://localhost:8089 and set the number of users and spawn
rate.

### Headless, for a fixed ladder run
```bash
locust -f loadtests/locustfile.py --host http://localhost:8000 \
  --headless --users 100 --spawn-rate 20 --run-time 2m \
  --csv loadtests/reports/rest_100users
```
Repeat with `--users 500` and `--users 1000` (adjust `--spawn-rate`
proportionally) for the full ladder. Each run writes
`<prefix>_stats.csv`, `<prefix>_stats_history.csv`, and
`<prefix>_failures.csv` - keep these, they're the raw data behind any
before/after comparison.

## WebSocket tier

Simulates N students already connected to the same live quiz room, all
answering the moment a question is revealed - the "everyone answers at
once" spike the REST tier can't reproduce.

```bash
# Uses the same seeded course/students as the REST tier, plus the
# "Load Test Quiz" seed_demo also creates.
python loadtests/ws_load_test.py --host http://localhost:8000 --students 200
```

To build a ladder (for `generate_graphs.py`'s latency-vs-room-size
graph), run it at several sizes, appending each run's summary to the
same file:
```bash
for n in 50 100 200 500; do
  python loadtests/ws_load_test.py --host http://localhost:8000 --students $n \
    --output-json loadtests/reports/ws_ladder.jsonl
done
```

## Generating the graphs

Once you have both tiers' raw output (the REST `before_`/`after_` CSVs
and the WS `ws_ladder.jsonl`):
```bash
python loadtests/generate_graphs.py --user-counts 100 500 1000
```
Each graph is skipped (with a warning, not an error) if its inputs
aren't found - you can generate just the REST graphs, just the WS graph,
or both, depending on what you've run.

## Before/after methodology

To produce a real, credible "with Redis vs without" comparison (not just
asserted numbers):

1. **Baseline ("before")**: set `LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS=True`
   in the backend's environment and restart it. This keeps Redis itself
   running (so this measures the code-path difference, not a Redis
   outage) but skips the catalog cache and the quiz-submit idempotency
   fast path - every request recomputes/re-checks from Postgres alone,
   the way the code worked before those optimizations existed.
2. Run the full user ladder (100/500/1000) headless as above, saving
   each run's CSVs under a `before_` prefix.
3. **Optimized ("after")**: unset `LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS`
   (or set it to `False`) and restart the backend.
4. Re-run the identical ladder, saving CSVs under an `after_` prefix.
5. Compare `<prefix>_stats.csv`'s p50/p95 response time columns for
   `GET /api/courses/` and `POST /api/quizzes/[id]/submit/` between the
   `before_` and `after_` runs at each user count.
6. Separately, compare the `Request Count` / `Failure Count` columns for
   `POST /api/quizzes/[id]/submit/ (concurrent duplicate)` between the
   two runs. `DOUBLE_SUBMIT_FRACTION` in `locustfile.py` makes a slice
   of students fire a genuinely concurrent duplicate submit (two
   requests from separate greenlets, not a sequential retry) - this is
   what the idempotency fast path (`quizzes/views.py::quiz_submit`,
   layer 2) actually exists to absorb, and what the plain submit task
   above (every student submits exactly once) can never exercise. A
   200/201/409 split is expected either way; what you're checking for
   is whether the "before" run (Redis lock off, relying on layer 3's DB
   constraint alone) shows more failures or takes a visibly higher p95
   on this request name specifically under contention.

Plotting these into the actual before/after graph (users on the x-axis,
p95 latency on the y-axis, one line per configuration) and turning the
`after_` numbers into committed regression thresholds is the next
milestone's job - this milestone's responsibility is producing
trustworthy, reproducible raw data for that graph to be built from.

## What to expect to see

Per the original architecture audit, the two hottest paths under load
are:
- `GET /api/courses/` - previously N+1 (`chapter_count`/`enrolled_count`
  computed per row); now served from a cached Redis read when
  `LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS` is left at its default
  (`False`, i.e. optimizations enabled). Expect the biggest before/after
  gap here as user count increases, since Postgres load scales with
  request volume in the "before" configuration but cached responses
  don't.
- `POST /api/quizzes/[id]/submit/` - the plain (single-submit) task
  measures pure first-submission cost, and per REPORT.md's original run
  the idempotency lock is *net overhead* there (two Redis round trips
  bought for a case - a concurrent duplicate - that never occurs when
  every student submits exactly once). The fast path's actual value
  only shows up on `POST /api/quizzes/[id]/submit/ (concurrent
  duplicate)` (see "Before/after methodology" step 6 above) and in the
  WebSocket tier (M21), where the "everyone answers at once" spike is a
  much sharper burst than REST-tier submissions naturally produce.
