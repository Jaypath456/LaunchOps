# AGENTS.md (LaunchOps v2)

Onboarding workflow app (roles hr, it, manager, employee). Work happens on `v2-*` branches; `main` stays v1 until v2 is merged
(v1 is tagged `v1-postgres`). Spec: `docs/V2_SPEC.md` — read only the sections your task names.

**Status line (owner edits):** Phase 1 (tasks t01-t03): still PostgreSQL + Django ORM + Celery/Redis while the LMS is stripped.
[mongo] and [go] rules apply from t04.

Target: Django 6 + DRF + simplejwt over PyMongo (no ORM) and MongoDB 8 standalone; Go `provisioner` (3 replicas) and Go `mock-saas`;
React + TypeScript + Vite. No Celery, Redis, PostgreSQL or broker.

## Rules
- [mongo] Business state changes only in `backend/onboarding/repository.py`: conditional `update_one` guarded by `version` plus an
  `$elemMatch` on the step; events `$push`ed in the same write. `arrayFilters` alone is not a guard. Use `matched_count`.
- [mongo] No Django ORM, no `django.db.transaction`, no raw SQL, no mongomock. Tests use `MongoTestCase` (real MongoDB, unique DB per class).
- [mongo] Datetimes UTC and tz-aware at boundaries. Never order by timestamp; array order is event order.
- [go] Go writes only inside one `provisioning` element and pushes only `provisioning.*` events; never `version`, `steps` or `issues`.
  Every job transition is a compare-and-set. `fence` is never reset. Every network/DB call has a context deadline. No package-level
  mutable state. Logs via `log/slog` JSON. mock-saas is a simulator; never call it a real integration.
- Contract changes update `contracts/` plus both the Python and Go contract tests in the same task.
- No invented numbers: every figure in a doc comes from `docs/evidence/<name>/` (COMMAND.txt, ENV.txt, raw output).
- Never `docker compose down -v`; never delete volumes; never push.
- Do NOT commit, amend, rebase or create branches; never add `Co-authored-by` or "Generated with" lines anywhere. Leave changes in the working tree.
- Do not read `frontend/node_modules/`, `frontend/dist/`, `.git/`.
- Unattended runs: nobody can answer questions. If something is ambiguous, choose the most conservative option consistent with the spec,
  write the decision to `.night/notes.md`, and continue. If truly blocked, write why in `.night/notes.md` and stop.
- Stop when the task's "done when" holds. Do not improve unrelated code.
- Do not create files under `docs/evidence/` unless the task explicitly asks for evidence. Put scratch output in `/tmp`, never in the repo.

## Commands
- Phase 1 backend: `docker compose exec -e REDIS_URL=redis://redis:6379/15 backend python manage.py test --noinput`
- From t04: `docker compose exec backend python manage.py test --noinput`
- Go: `cd provisioner && go vet ./... && MONGO_URI=mongodb://localhost:27017 go test -race ./...`
- Frontend: `cd frontend && npm run typecheck && npm test && npm run build`
- If you cannot run Docker in your sandbox, say so; an outside script runs the checks and sends you failures.

## Finish with
1. Files changed. 2. Commands run with exact pass/fail counts (or "not runnable here"). 3. Decisions recorded in `.night/notes.md`.
