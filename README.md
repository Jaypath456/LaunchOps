# LaunchOps

A local onboarding workflow app for HR, IT, managers and employees, built with Django REST Framework, MongoDB and React with TypeScript. Public registration is unavailable. Task forms record local simulations and self-reported evidence.

## Quick start

```bash
DEBUG=True docker compose up mongo backend --build --wait -d
docker compose exec backend python manage.py test --noinput
```

The backend exposes `/api/auth/login/`, `/api/auth/refresh/` and `/api/auth/me/` using MongoDB users and JWT sessions, and the onboarding workflow API under `/api/onboarding/` (see `docs/V2_SPEC.md` section 8). The frontend workflow returns in a later task. Preserve existing Docker volumes.

## Origins

LaunchOps started as a learning-management system I built (courses, chapters, quizzes, scheduling, course chat history) and that I converted into this onboarding platform. The onboarding workflow engine was later work in this repository. The learning modules have been removed as the onboarding backend moves to MongoDB. This repository's history starts from a snapshot of the combined code, so it does not show that split.

## AI tools used

I built this project with help from AI tools, including GitHub Copilot, Cursor, Claude Code and Codex.
