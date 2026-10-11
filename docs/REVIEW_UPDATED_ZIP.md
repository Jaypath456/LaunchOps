# Updated LaunchOps ZIP review

Review date: September 27–28, 2026 (America/New_York). Source archive: `/home/jay/Documents/LaunchOps-updated.zip`. All 220 extracted files matched the archive before edits. The corrected source is this directory, separate from the original project. Three valid repetitions per engine and size were obtained; Django needed five attempts at 100 students. Two final 100-student Django attempts still failed to admit everyone, so those latency samples do not support a reliable capacity or general speedup claim.

## Isolation and reproduction

All startup, seeding, fault injection and benchmark writes use **`launchops-review-20260927`**. The frontend is at **http://localhost:33000**, API at **http://localhost:38000/api**, and gateway at **ws://localhost:39090**. Direct Go worker ports **39091/39092** bind to loopback for acceptance testing.

The review uses `launchops-review-20260927_postgres_data`, `_redis_data`, and `_mongo_data`. The original PostgreSQL volume remains `launchops_postgres_data`. The already-existing `launchops-audit-20260927` and LMS projects were also left alone. No volume deletion command was used. Review data contains synthetic users, quiz sessions, and benchmark quizzes, not a copy of the original workflow.

```sh
sh scripts/review-compose.sh config --quiet
sh scripts/review-compose.sh build
sh scripts/review-compose.sh up -d --no-build --wait
sh scripts/review-compose.sh exec -T backend python manage.py seed_demo --students 100
sh scripts/review-compose.sh exec -T -e REDIS_URL=redis://redis:6379/15 backend python manage.py test --noinput
sh scripts/review-compose.sh exec -T frontend sh -c 'CI=true npm test -- --watchAll=false --runInBand'
sh scripts/review-compose.sh exec -T frontend sh -c 'CI=true npm run build'
python scripts/live_review.py
NODE_PATH=/tmp/launchops-browser/node_modules node scripts/live_review_browser.cjs
python loadtests/compare_live.py --http-host http://localhost:38000 \
  --django-ws ws://localhost:38000 --go-ws ws://localhost:39090 \
  --users 10 25 50 100 --repeats 3 --max-attempts 5 --output docs/review/live_comparison_verified.json
```

The Python clients require `requests` and `websockets`; this review installed them in `/tmp/launchops-review-venv`. The browser client used the existing Playwright installation and `/usr/bin/google-chrome`. The live test intentionally stops/restarts review worker A and review Redis; run it after the backend suite. `--skip-redis-outage` permits other live checks while regression tests use Redis DB 15. The review wrapper uses Daphne with `DEBUG=False` for comparison and the legacy Docker builder because the machine’s Buildx executable is missing. It fixes the project name and ports and uses `/dev/null` as the environment file, so it does not inherit another stack’s `.env`. All `.env*` files, including example templates, are excluded from the deliverable. A config comparison confirmed this does not change the review configuration.

## Failures and fixes

| Failure | Evidence and disposition |
| --- | --- |
| Plain Compose build could not start: missing `/home/jay/.docker/cli-plugins/docker-buildx`. | `review/initial-build.log`. Used `DOCKER_BUILDKIT=0` in the isolated wrapper. The machine-wide Docker installation was not changed. All six build targets subsequently succeeded, including both Go workers. The supplied Go source compiled successfully; no Go compiler defect was observed. |
| Student reconnect snapshots exposed instructor-only chart/leaderboard fields. | Direct socket probe confirmed the field exposure. `go-live/main.go` now gates snapshot aggregates by host identity; the final live test asserts their absence. Answer keys were already withheld from students and remain withheld. |
| Instructor missed the end event during a disconnect and stayed on the live screen after reconnect. | Reproduced in `review/browser-initial.log` and `review/frontend-reconnect-reproduced.log`. The host page now reads `session.state.status`, restores the ended screen, and loads Mongo scores through an effect. |
| Instructor could reveal a question before its WebSocket opened, silently dropping the command. | `review/browser-connection-race.log`; the affected Redis room remained at index −1. Host controls now stay disabled until connected and disable again on disconnect. Covered by the component test and successful browser rerun. |
| Gateway reconnection timed out during failover after worker recreation. | `review/live-gateway-timeout.log`. nginx had static upstream addresses and a long connection timeout. It now uses Docker DNS refresh and bounded connection attempts to both replicas. `nginx -t` passed on nginx 1.27.5; repeated worker-stop acceptance passed. |
| Benchmark chart timing was sampled only after all student acknowledgments. | `loadtests/ws_load_test.py` now observes host chart completion concurrently and requires all connected students’ responses. Raw latency samples and connection errors are retained. `compare_live.py` flags incomplete runs and exits unsuccessfully rather than silently reporting a successful comparison. Total connection failure now produces a sample instead of aborting the ladder; a focused harness test verifies this. Authentication finishes before socket setup, and clients drain the session-end broadcast before closing. |
| Django joins repeatedly exceeded handshake deadlines under a large connection burst. | `backend/quizzes/consumers.py` combined session/enrollment authorization in one database call and removed the redundant room query. `backend/quizzes/tests.py` adds concurrent enrolled/outsider permission coverage. All 24 affected live-consumer tests passed. N=50 reached 3/3 valid runs; N=100 still had intermittent failures, retained below. Local installed Daphne source confirms a five-second opening-handshake timeout and serialized database authorization calls; this workstation was heavily swapping. This identifies a plausible bottleneck, not proof of one exclusive cause. |
| Documentation suggested clearing the Go URL alone to switch engines. | README and architecture notes now explain the stored room engine and `LIVE_QUIZ_ENGINE=django`. |

Harness/setup failures are retained separately so they are not mistaken for product defects:

- An early startup attempt launched a redundant frontend build while the original build was pending. I explicitly stopped that duplicate build container, producing exit 137 in `review/startup.log`. The original `npm ci` exited successfully; this was not an observed OOM failure.
- A frontend test command initially ran before its container was ready (`service "frontend" is not running`). Rerun after startup passed.
- A live test initially logged in before the seed transaction committed, returning 401 (`review/live-initial.log`). Rerun after seeding passed.
- The added component test initially hit the old Jest resolver’s handling of `react-router-dom` (`review/frontend-reconnect-initial.log`). Its mocked `useParams` module now uses a virtual mock; production routing was unchanged.
- A browser attempt timed out loading the application during concurrent compilation (`review/browser-resource-timeout.log`). It was rerun after compilation with a longer navigation timeout and passed.

Go dependency resolution now has a committed `go.sum` and explicit indirect dependencies. The Docker build consumes that lock data, runs the Go test, and builds with `-mod=readonly` instead of changing dependency metadata during each build.

## Verification

- Compose configuration and all six build targets passed. Both Go worker builds execute the Go unit test before compiling. The final rebuild is `review/all-build-handoff.log` (all six targets); the one Go unit test passed. nginx configuration validation also passed.
- Backend: **261 tests passed** in 836.643 seconds (`review/backend-tests.log`); Django system check and migration-drift check also passed. The logged Redis exception traces were intentional mocked-outage tests. After the final backend join edit, **24 affected live-consumer tests passed** in 128.315 seconds (`review/backend-join-tests.log`), including the added concurrent permission regression. The current suite has 262 tests; the full suite was not rerun after that focused edit.
- Frontend: **4 tests passed** (the 3 supplied tests plus the added regression test), across 3 suites (`review/frontend-handoff-checks.log`). The final production build compiled successfully in that same log. The benchmark harness’s **2 focused tests passed** (`review/benchmark-harness-final-tests.log`).
- Redis outage: new live connections returned HTTP 503, and Mongo-backed saved scores remained readable (`review/live-outage-checks.json`, last two checks). Redis was restarted successfully.
- Original onboarding rows and sequence values matched at finish (`review/original-workflow-preservation-final.json`). Original containers were already stopped when this continuation began. The original PostgreSQL volume was mounted read-only, copied into a project-scoped verifier with no network/host ports, and compared without starting the original stack. The initial comparison differed only in pg_dump random markers and PostgreSQL 16.14/16.15 version comments; all remaining content was identical. The verifier was then stopped. Original dumps and backups remain outside the ZIP.

Live protocol acceptance: **30 assertions passed** in `review/live-final-checks.json`. These cover authentication, enrollment, Origin checks on both Go workers, private service authorization, host-only events, host/student answer-key separation, room isolation, malformed submissions, a simultaneous duplicate from two replicas, first-answer preservation, single/multiple/short-answer grading, late-answer rejection, worker A stop and gateway recovery, saved totals, student-only result visibility, and reconnect after session end.

Browser acceptance: `review/live-browser.json` records a pass with no JavaScript page errors. An instructor creates/starts a room; two enrolled students join the lobby, receive/answer a question, and update the host chart. A student reconnects and retries a changed answer without changing the first score. An instructor deliberately misses the end event, reconnects, and sees saved results. Both students see their own saved score. Screenshots: `review/live-host.png`, `review/live-student.png`.

Prior four-role onboarding browser acceptance remains documented in `docs/AUDIT.md` and `docs/audit/`; it was not restarted in this continuation because no onboarding implementation changed.

Mongo persistence: `review/mongo-durability.json` confirms six unique answers with scores 9 and 0 (three answers each), unchanged after restarting the Mongo container. The unique index on `(room, question_id, user_id)` is recorded in `review/mongo-initial.json`.

## Measured benchmark results

The final phase used one published, one-point single-choice question with a 60-second answer window, cloned into a fresh synthetic quiz for every run. Students authenticated before simultaneous socket setup. Reveal timing starts at host advance; acknowledgment timing starts at the answer spike and requires a correct private acknowledgment for the same question; chart timing starts at that spike and ends when the instructor has all connected responses. Both engines grade and durably save the same correct answer. Clients wait for session-end delivery. A valid run requires N connections, N reveals, N correct acknowledgments, a complete host chart, and confirmed end delivery to every socket.

Each size used alternating Django/Go and Go/Django order, up to five paired attempts to obtain three valid runs per engine. All attempts remain in `review/live_comparison_verified.json` and `.csv`. No passing subset was selected from the final valid runs: all valid runs are summarized, including all five Go runs at N=100. Invalid runs remain in the counts and raw results. The comparison command exits 1 whenever any invalid attempt was recorded, even though all repetition targets were met.

| Students | Engine | Valid / attempts | Connections / requested | Answers / connected |
| --- | --- | --- | --- | --- |
| 10 | django | 3/3 | 30/30 | 30/30 |
| 10 | go | 3/3 | 30/30 | 30/30 |
| 25 | django | 3/3 | 75/75 | 75/75 |
| 25 | go | 3/3 | 75/75 | 75/75 |
| 50 | django | 3/3 | 150/150 | 150/150 |
| 50 | go | 3/3 | 150/150 | 150/150 |
| 100 | django | 3/5 | 362/500 | 362/362 |
| 100 | go | 5/5 | 500/500 | 500/500 |

| Students | Engine | Learner samples | Reveal p50/p95/p99 (ms) | Ack p50/p95/p99 (ms) | Chart median [min–max] (ms); samples |
| --- | --- | --- | --- | --- | --- |
| 10 | django | 30 | 67.2/148.0/148.2 | 323.7/618.1/679.0 | 774.5 [573.6–835.1]; 3 |
| 10 | go | 30 | 3.4/5.5/6.4 | 6.3/24.1/24.2 | 9.8 [4.7–24.9]; 3 |
| 25 | django | 75 | 80.5/91.5/91.7 | 611.0/1506.5/1553.5 | 2227.0 [2151.6–2298.3]; 3 |
| 25 | go | 75 | 2.4/6.9/7.0 | 9.7/27.3/27.4 | 27.0 [9.9–28.4]; 3 |
| 50 | django | 150 | 133.0/285.7/286.4 | 1271.1/2582.3/2725.7 | 3910.1 [3663.9–4389.0]; 3 |
| 50 | go | 150 | 4.8/13.7/13.9 | 16.4/68.9/70.1 | 28.5 [26.1–71.4]; 3 |
| 100 | django | 300 | 100.3/106.0/106.8 | 1810.0/3410.3/3561.9 | 6018.7 [5815.7–6201.3]; 3 |
| 100 | go | 500 | 7.1/11.4/11.9 | 22.1/41.0/44.1 | 43.3 [36.7–240.7]; 5 |

Percentiles for reveals/acknowledgments pool learner observations from every valid run. Observations within a room are correlated; these are descriptive measurements, not independent statistical trials. Charts have one observation per valid run, so median and range are reported; p95/p99 tails from 3–5 chart observations would not be meaningful. The raw per-run fields retain the original single-observation chart percentiles. No 100-user speedup claim is made: its successful-run latency excludes two failed Django connection attempts. These results demonstrate latency in the tested complete deployments; isolated language speed, production capacity, outage-state reconstruction, and a universal speedup remain unproven.

Durable validation: **70/70 recorded attempts** matched their saved grades, unique identities, answer values/points, and ended status (`review/benchmark-saved-record-checks.json`). This covers invalid connection attempts too: the admitted students’ scores match their acknowledgments. The initial unrecorded abort saved zero answers; only that synthetic room was ended afterward (`review/unrecorded-abort-data-check.json`). Total checked benchmark answers: 2714.

## Benchmark failures retained

The first ladder completed 18 samples through N=50: 17 valid and one invalid. Django repetition 2 at N=50 connected **46/50**, with four handshake failures. The first N=100 Django attempt connected **0/100** and the original harness aborted before saving that sample or producing CSV. Its first ten error details and total failed count remain in `review/benchmark-initial.log`; the 18 saved samples remain in `review/live_comparison_initial.json`. The initial harness did not retain individual details for the remaining 90 failed connections.

That setup overlapped password logins with new WebSocket handshakes. The final ladder separates those phases on both engines because the measured workload is a quiz with already-authenticated participants. This is a methodology change, not proof that Django’s login-and-join burst capacity was repaired. The original failures remain relevant to that workload.

The second, preauthenticated phase (`review/live_comparison.json`) still had one invalid N=50 run (46/50) and three invalid N=100 runs (15/100, 0/100, 15/100). It saved every error and continued after total failure.

The final phase after the join fix had two invalid N=100 Django attempts: repetition 1 connected **35/100** (65 failures), repetition 3 connected **27/100** (73 failures). Repetitions 2, 4 and 5 connected/graded all 100. All Go attempts and final N=10/25/50 attempts passed. Across all phases: 70 recorded attempts plus one aborted attempt; 63 valid and 8 invalid attempts; 516 failed connections. Earlier phases use different setup/implementation conditions and are not pooled into final latency metrics.

Django also logged `Attempt to send on a closed protocol` during the initial harness’s immediate socket teardown; the trace points to a chart broadcast after client closure. Initial runtime logs are preserved. The final harness waits for session-end delivery before closing. Resource collectors did not exit on SIGTERM and two older collectors continued sampling overnight; only those read-only collector processes were force-stopped. Raw and normalized resource traces are retained, including the extended tail, without treating it as timed quiz data. Resource normalization initially failed on unavailable `--` values/compact memory units; peaks now skip missing measurements and retain the original observations.

## Benchmark scope and limitations

This compares complete deployed paths: one Django/Daphne backend with PostgreSQL scoring versus two Go workers behind nginx with MongoDB scoring. Both use the same accounts and source question, fresh synthetic quizzes per run, and Redis. It does **not** isolate programming-language execution speed. Host chart completion has only one sample per run; its per-run p50/p95/p99 values are therefore identical.

Hardware: Intel i5-10500H, 6 physical cores / 12 logical CPUs, approximately 7.6 GiB RAM; Docker Engine 27.5.1, Compose 5.1.3. The initial phase left existing stacks running; they were already stopped at continuation. The final phase ran only review containers alongside other desktop/user processes, with **about 13 GiB of 15 GiB swap in use**, approximately 6.5–6.6 GiB RAM used, and load averages recorded before/after. See `review/final-benchmark-system-before.txt` and `-after.txt`. Resource observations cover the whole final ladder, including password authentication/setup and a short verification tail; they are not CPU measurements isolated to quiz handling. Docker CLI observations can repeat values and have no per-row timestamp. Peak resource values and missing-value handling are in `review/resource-summary.json`; ordered raw/normalized traces are included. These are shared-workstation measurements.

Remaining limitations:

- N=100 Django connections remain intermittent despite three completed runs; the two failed final attempts rule out a clean capacity/reliability claim. The final phase had no missed reveals, incorrect acknowledgments, or missing end delivery among admitted users.

- `npm ci` reported **45 dependency vulnerabilities: 10 low, 12 moderate, 23 high**, plus deprecated dependencies. This review did not force a potentially breaking dependency upgrade. Successful builds do not resolve those findings.
- Redis loss/flush during an active quiz does not reconstruct hot question state from Mongo. A crash after Mongo insertion but before Redis counters update can leave the live chart behind saved results. These existing architectural limits remain.
- Legacy Django live scoring is unique per quiz/student, not per room. Fresh benchmark quizzes avoid that collision; repeated real Django rooms still need a data-model change before equivalent durable scoring can be claimed.
- Host result responses cap the returned leaderboard at 100 rows. Review concurrency reaches 100 users.
- The frontend is the existing development server. This review is not production deployment or a full security assessment.

## Exact source changes

Modified existing files:

- `README.md`
- `docs/LIVE_ARCHITECTURE.md`
- `go-live/Dockerfile`
- `go-live/go.mod`
- `go-live/main.go`
- `go-live/nginx.conf`
- `frontend/src/pages/instructor/LiveQuizHost.js`
- `loadtests/ws_load_test.py`
- `loadtests/compare_live.py`
- `backend/quizzes/consumers.py`
- `backend/quizzes/tests.py`

Added source/test/support files:

- `go-live/go.sum`
- `frontend/src/pages/instructor/LiveQuizHost.test.js`
- `docker-compose.review.yml`
- `scripts/review-compose.sh`
- `scripts/live_review.py`
- `scripts/live_review_browser.cjs`
- `loadtests/test_live_benchmark.py`
- `docs/REVIEW_UPDATED_ZIP.md`
- `docs/REVIEW_CHECKPOINT.md`
- `scripts/verify_benchmark_records.py`
- `loadtests/summarize_live.py`

Evidence files under `docs/review/` and generated production build files are enumerated in the final change manifest. The only backend implementation edit is the review copy’s live consumer join authorization, plus its test. No migration, original Compose file, original workflow, or original database file was edited.

## Handoff and safe commands

The clean archive is `/home/jay/Documents/LaunchOps-reviewed.zip`. It contains final source, Compose files, this audit, tests and benchmark evidence. `.env*`, installed dependencies, production builds, caches, database files, volumes and backups are excluded. Source/extraction hashes and the exact included/excluded file list are in `review/change-manifest.json` and `review/zip-verification.json`. Public local-demo placeholders in application source are not production credentials. No actual environment credentials, bearer tokens or dumps are packaged.

To inspect this review without touching the original project:

```sh
cd /home/jay/Documents/LaunchOps/LaunchOps
sh scripts/review-compose.sh config --quiet
sh scripts/review-compose.sh up -d --no-build --wait
# Open http://localhost:33000; read docs/REVIEW_UPDATED_ZIP.md.
```

For a separate extracted preview, unzip into an empty directory and run the same wrapper from its `LaunchOps` folder; the wrapper always selects the review project. No install over the original has been performed.

When you choose to deploy, preserve the original deployment configuration and a durable database backup outside the ZIP. Use an extracted deployment directory; keep project name `launchops` to retain its named PostgreSQL/Redis volumes and existing host-port settings. Substitute the actual paths and existing environment settings before executing these future commands:

```sh
LAUNCHOPS_REVIEWED_COMPOSE=/path/to/extracted/LaunchOps/docker-compose.yml
LAUNCHOPS_DEPLOY_ENV=/path/to/your/existing/deployment.env
DOCKER_BUILDKIT=0 docker compose -p launchops --env-file "$LAUNCHOPS_DEPLOY_ENV" -f "$LAUNCHOPS_REVIEWED_COMPOSE" config --quiet
DOCKER_BUILDKIT=0 docker compose -p launchops --env-file "$LAUNCHOPS_DEPLOY_ENV" -f "$LAUNCHOPS_REVIEWED_COMPOSE" build
# After reviewing the config, retaining rollback source/images, and backing up:
DOCKER_BUILDKIT=0 docker compose -p launchops --env-file "$LAUNCHOPS_DEPLOY_ENV" -f "$LAUNCHOPS_REVIEWED_COMPOSE" up -d --wait
```

Those deployment commands change the original project and were not executed. The original containers were already stopped at continuation and were left in that state. Do not substitute the review project’s synthetic database volumes for the original volumes.
