# AGENTS.md

LaunchOps: onboarding workflow app (20 tasks, 4 roles). Django 6 + DRF + simplejwt, PostgreSQL, Celery/Redis, React (Create React App).

- Scope: backend/onboarding, backend/users, frontend/src unless the task says otherwise.
- Never read or grep: docs/review/, frontend/node_modules/, frontend/build/, go-live/ unless the task names it.
- Do not run `docker compose down -v`. Never delete volumes or data.
- Backend tests: `docker compose exec -e REDIS_URL=redis://redis:6379/15 backend python manage.py test --noinput > /tmp/t.log 2>&1; tail -30 /tmp/t.log`. Report exact counts.
- Frontend checks: `npx tsc --noEmit` and `CI=true npm test -- --watchAll=false`, tail the output.
- One task per session. One commit per tested increment. No squash, rebase or force-push.
- No invented numbers: every figure needs its command and environment.
