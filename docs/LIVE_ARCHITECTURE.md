# Go live quiz and MongoDB: implementation and review notes

This release builds on the audited 20-task onboarding ZIP. It adds a Go live-quiz service and MongoDB for **live answer records only**. PostgreSQL remains the primary database for accounts, workflows, lessons, asynchronous quiz submissions and scheduling. It would be false to describe the whole application as migrated to MongoDB.

## Request path

1. Instructor creates and starts a published quiz session using Django's existing REST API. New Compose sessions select the `go` engine; historical rooms retain `django`. The session stores its engine, and each WebSocket server rejects rooms assigned to the other engine. Enrolled students join by room code, and the browser looks up the engine before connecting.
2. The browser opens `/ws/live/<room>/` on the nginx gateway. `least_conn` routes the connection to one of two Go workers. The existing JWT travels in the WebSocket subprotocol, not the URL.
3. A Go worker calls Django's private bootstrap with that JWT and its separate `LIVE_SERVICE_SECRET`. Django verifies the JWT, course membership, host identity and room, then returns sanitized student questions and a service-only answer key. Students never receive the answer key.
4. Go keeps the current room/question and a per-question answer claim in Redis. A Lua check applies the question deadline and uses `SET NX` for the first answer. The worker grades the answer and stores it in `launchops_live.live_answers` in MongoDB. A unique compound index `(room, question_id, user_id)` prevents duplicate durable answers across replicas. Redis stores chart and leaderboard counters; a single Pub/Sub subscription per active room per worker sends question reveals to everyone and aggregate updates to hosts only. Private correctness is sent only to the answering student's socket.
5. The host ends the room; Go asks Django to mark its `LiveSession` ended. Either role can fetch its allowed saved scores via `/api/sessions/<room>/live-results/`. Students see only their own row.

Repeated live rooms for the same quiz have independent MongoDB answers. The earlier Django consumer still uses `Submission(quiz, student)` for legacy live scores; its unique constraint can combine repeated rooms and must be revised before treating baseline scoring as production grade. The benchmark below creates a **fresh synthetic quiz per run** to avoid that collision.

## Run and compare

```bash
docker compose up --build --wait -d
docker compose exec backend python manage.py seed_demo --students 100
curl -f http://localhost:19090/health
python -m pip install -r loadtests/requirements.txt
python loadtests/compare_live.py --users 10 25 50 100 --repeats 3
```

Keep both engines running. The test creates one `django` room at `ws://localhost:18000` and one `go` room at `ws://localhost:19090`, with the **same Django accounts, course and quiz question**. It creates an isolated published quiz for each run; these benchmark quizzes persist and can be cleaned up separately after inspection. Run AB/BA order alternately. JSON and CSV under `loadtests/reports/` record N, connection failures, accepted answers, missed reveals, host chart completion and p50/p95/p99 latencies. Check failures and accepted counts *before* comparing percentiles. Record machine specifications, Docker version, database volumes, and worker CPU/memory if presenting measurements. Results are not prefilled; do not claim a speedup until they are measured on the same machine under comparable load.

This compares **complete architectures**, not isolated Go versus Python execution speed: the Go path uses MongoDB for answers and two workers, while the Django path uses PostgreSQL and one backend. Authentication and quiz setup still use Django on both. The Go worker sends aggregates only to hosts because the student UI never displays them; that reduces unnecessary network fanout. The browser follows each room’s stored engine. Set `LIVE_QUIZ_ENGINE=django` on the backend to create Django rooms by default; clearing the Go URL alone does not change room ownership. Benchmark engine selection is independent of that default.

## Data and operational boundaries

Never reset Docker volumes to apply this update; the original PostgreSQL volume contains your previous employee workflow. Back up both stores before changing schemas:

```bash
docker compose exec -T postgres pg_dump -U launchops_user -Fc launchops_db > launchops-postgres.backup
docker compose exec -T mongo mongodump --db launchops_live --archive > launchops-live-mongo.archive
```

The MongoDB backend for Django 6.0 does not support the existing app's `BigAutoField`, `select_for_update`, `prefetch_related` or `.extra` query paths; Django's ordinary `transaction.atomic` is not supported either. Moving **all** existing workflows to MongoDB requires redesigned IDs/URLs, migrations and concurrency handling, a verified PostgreSQL-to-MongoDB transfer, and replay of the 259 onboarding/backend regression tests on the new store. This ZIP does **not** switch off PostgreSQL or move existing records. Compose keeps it available. The reviewed ZIP intentionally omits all `.env*` files. MongoDB live answers demonstrate a real Mongo-backed subsystem without risking your historical workflow data. See the [MongoDB feature matrix](https://www.mongodb.com/docs/languages/python/django-mongodb/v6.0/limitations-upcoming/) and [transactions guide](https://www.mongodb.com/docs/languages/python/django-mongodb/v6.0/interact-data/transactions/).

Redis keeps hot room state and chart counters. If Redis is flushed mid-room, MongoDB answers remain, but the current Go room question and counters are not reconstructed automatically: **do not flush Redis during an active quiz**. The Mongo insert precedes the Redis counter update, so a crash in between can leave a saved answer without a corresponding live chart point; saved results remain authoritative. The updated ZIP has now been built and exercised in an isolated Docker project. See [the review report](REVIEW_UPDATED_ZIP.md) for exact test results, failures, changes, measured benchmark data, and remaining limitations.

## Astra acceptance checks

1. `docker compose config`, `docker compose up --build --wait -d`, `docker compose ps`, `docker compose logs --tail 100 go-live-a go-live-b live-gateway backend`.
   Run the Go unit test with `docker run --rm -v "$PWD/go-live:/src" -w /src golang:1.22-alpine sh -c 'go mod tidy && go test ./...'` (a first run fetches modules).
2. `docker compose exec backend python manage.py test --noinput`, `docker compose exec frontend sh -c 'CI=true npm test -- --watchAll=false --runInBand'`, `docker compose exec frontend sh -c 'CI=true npm run build'`.
3. Open a published instructor quiz and join it as two enrolled learners in separate browsers. Reveal multiple questions, submit valid and malformed answers, double submit, reconnect, end the room, and inspect the persisted scores. Check that an outsider, unauthenticated user, and learner attempting host events are denied, and student socket payloads never contain an answer key.
4. Stop `go-live-a` while both learners are connected; reconnect through the gateway and repeat. Verify room isolation across two rooms. Check stored MongoDB documents against chart/score counts, including duplicate and late answer races. Retry with Redis unavailable to ensure the system fails safely.
5. Seed synthetic users and run `compare_live.py` across the concurrency ladder; attach JSON/CSV, system details and observed errors. Review the existing workflow and completed task after update to verify preserved data.

Resume bullet wording after measured verification: *Built a two-replica Go WebSocket quiz service with Redis room coordination and MongoDB answer idempotency; benchmarked p95 latency at N concurrent learners against a Django baseline, observing [measured result] on [hardware].* Do not put a number in the placeholder until a valid run supplies it.
