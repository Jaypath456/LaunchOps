# LMS Repository Summary

**Note:** `main` is the original codebase. Full functionality lives in stacked feature branches (`cursor/*-7233`). The tip branch `cursor/load-testing-ws-graphs-7233` has quizzes, live sessions, leaderboards, Redis, Channels, and load testing. Schedule and messaging are on parallel branches (`cursor/schedule-*`, `cursor/course-chat-*`, `cursor/chat-frontend-*`) and are not yet merged into the load-testing stack.

---

## 1. Folder Structure

```
/
├── backend/                  # Django 5 + DRF
│   ├── lms_project/          # settings, ASGI, Celery, WS auth, safe_cache
│   ├── users/                # custom User + auth endpoints
│   ├── courses/              # courses, chapters, enrollments
│   ├── quizzes/              # quizzes, submissions, live sessions, WS consumer
│   ├── schedule/             # terms, sections, meetings, breaks, schedule gen
│   ├── messaging/            # course chat REST + WS consumer
│   └── courses/management/commands/seed_demo.py
├── frontend/                 # React SPA
│   ├── src/api/              # axios + ReconnectingSocket (ws.js)
│   ├── src/context/          # AuthContext
│   ├── src/components/       # shared UI (chat, charts, leaderboard, calendar)
│   └── src/pages/{auth,instructor,student}/
├── loadtests/                # Locust REST + asyncio WS scripts, graph generation
├── docker-compose.yml        # postgres, redis, backend, frontend, locust (profile)
└── README.md
```

---

## 2. Existing Models

| App | Models |
|-----|--------|
| **users** | `User` (extends `AbstractUser`, `role`: instructor/student, `bio`) |
| **courses** | `Course` (FK instructor, `is_published`; optional FK `term` on schedule branches), `Chapter` (`content` JSONField, Slate/Plate), `Enrollment` (`unique_together`: student+course) |
| **quizzes** | `Quiz`, `Question` (`body` JSONField, types: single/multiple choice, short answer), `Submission` (`unique_together`: quiz+student), `LiveSession` (`room_code`, status lobby/active/ended) |
| **schedule** | `Term`, `Section`, `Meeting`, `Break`, `SavedSchedule` (student M2M sections) |
| **messaging** | `Message` (course-scoped, indexed by course+created_at) |

---

## 3. Existing APIs

All REST under `/api/`. Auth under `/api/auth/`.

- **Auth:** `POST register/`, `login/`; `GET me/`
- **Courses:** catalog (paginated), CRUD, enroll/unenroll, enrollment status, student roster, chapter CRUD, `my-courses/`
- **Quizzes:** instructor CRUD; student `take/`, `submit/`, `my-result/`; live session create/detail/start/end; course `leaderboard/`
- **Schedule:** terms, section CRUD, student breaks, `POST generate-schedule/`, saved schedules + confirm (creates enrollments)
- **Messaging:** paginated `courses/<id>/messages/`
- **WebSockets:** `ws/echo/` (test), `ws/live-quiz/<room_code>/`, `ws/course-chat/<course_id>/` (on respective branches)

---

## 4. Authentication Flow

1. User registers/logs in → DRF returns SimpleJWT `access` + `refresh` tokens and user payload.
2. Frontend stores tokens in `sessionStorage`; Axios interceptor adds `Authorization: Bearer <access>`.
3. On load, `AuthContext` calls `/auth/me/` to restore session.
4. WebSockets reuse the same JWT, sent as the first `Sec-WebSocket-Protocol` entry (not query string). `JWTAuthMiddleware` validates before consumers accept connections.
5. Default DRF permission is `IsAuthenticated`; public endpoints opt out with `AllowAny`.

---

## 5. Permissions

Defined in `courses/permissions.py`:

- **`IsInstructor`** / **`IsStudent`** — role gate at view level
- **`IsCourseInstructor`** — object-level; resolves via `obj.instructor` or `obj.course.instructor`
- **`IsEnrolled`** — student enrolled in course (on permissions-cleanup branch; some views still inline-check)

Additional ad hoc checks: published-course visibility, quiz publish state, live session host-only actions, chat access (instructor or enrolled student), term-based chat write lock.

---

## 6. Important Implementation Details

- **JSONField content pattern:** `Chapter.content` and `Question.body` share Slate JSON; frontend uses PlateEditor.
- **Idempotency:** DB `unique_together` + pre-check + optional Redis SETNX lock (`safe_cache.py` degrades if Redis down). Used for quiz submit, live quiz answers, schedule confirm.
- **N+1 fixes:** `annotate(Count(...))` on course lists; serializers read annotated values.
- **Caching:** Redis-backed catalog cache with invalidation on course mutations; toggle `LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS` for before/after benchmarks.
- **Live quiz:** Postgres `LiveSession` for lifecycle; Redis (`live_state.py`) for ephemeral state — current question, answer charts (hash), leaderboard (sorted set). `LiveQuizConsumer` drives reveal/chart/leaderboard broadcasts.
- **Schedule generation:** Pure backtracking in `schedule/services.py`; role-specific blocked intervals (student breaks vs instructor meetings).
- **Chat:** Persist-then-broadcast; Redis rate limiting; Celery task purges messages after term ends.
- **ASGI:** Daphne + Channels; `ProtocolTypeRouter` splits HTTP/WS.
- **Testing:** 126+ backend tests including `TransactionTestCase` for Channels; Locust + custom WS load scripts in `loadtests/`.

---

## 7. Technical Debt

- Feature branches not merged to `main`; parallel stacks (schedule/chat vs live quiz) need integration.
- Some branches still have hardcoded `SECRET_KEY` / `DEBUG=True` / `CORS_ALLOW_ALL_ORIGINS` despite M1 env-hardening on another branch.
- Almost entirely function-based views — no ViewSets; permission application is manual per view.
- No token refresh flow on frontend; 24h access token expiry only.
- Docker networking unreliable in some sandbox environments.
- README historically over-claimed features before implementation.
- Load-test idempotency lock adds latency on duplicate-free workloads (documented in `loadtests/REPORT.md`).

---

## 8. Architectural Assumptions

- **Decoupled SPA + API:** React on `:3000`, Django/Daphne on `:8000`; CORS configured for local dev.
- **PostgreSQL** is source of truth; **Redis** is optimization/ephemeral state only — correctness never depends on Redis availability.
- **Two roles only:** instructor owns courses; students enroll and consume content.
- **One enrollment model** links students to courses; schedule confirmation reuses it via `get_or_create`.
- **Course = chat room; LiveSession.room_code = quiz room** — isolation without extra room models.
- **Term.end_date** drives course tenure (chat reset), not a separate lifecycle system.
- **Channels groups** keyed by room_code or course_id for fan-out; JWT auth at connect time.
- **Production intent:** Daphne ASGI, Celery Beat for periodic tasks, Docker Compose for local reproducibility.

---

**Branch dependency (simplified):** `main` → settings/permissions/docker/pagination → quizzes → schedule (parallel) / redis → channels → messaging → live quiz stack → load testing. Merge order matters when integrating.
