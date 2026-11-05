# LaunchOps

A local onboarding workflow app for HR, IT, managers and employees, built with Django REST Framework, PostgreSQL, Celery/Redis and React with TypeScript. Accounts are created by the seed command; public registration is unavailable. Task forms record local simulations and self-reported evidence.

## Quick start

```bash
DEBUG=True docker compose up --build --wait -d
docker compose exec backend python manage.py seed_launchops
```

Use a fresh database for the reset migrations; preserve any existing Docker volumes. Open [LaunchOps](http://localhost:13000) and sign in with `demo_hr`, `demo_it`, `demo_manager` or `demo_employee`, using the local demo password `LaunchOpsDemo2026!`.

## Origins

LaunchOps started as a learning-management system I built (courses, chapters, quizzes, scheduling, course chat history) and that I converted into this onboarding platform. The onboarding workflow engine is the later work in this repository: the 20-task blueprint, the role and prerequisite rules, the evidence forms and checks, transactional completion with row locks, the overdue scan, the onboarding pages, and the tests, CI and frontend tooling described above. The learning modules remain for the training step and are not part of the onboarding walkthrough. This repository's history starts from a snapshot of the combined code, so it does not show that split.

## AI tools used

I built this project with help from AI tools, including GitHub Copilot, Cursor, Claude Code and Codex.
