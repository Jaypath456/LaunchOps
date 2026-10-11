# LMS Architectural Decisions (Implementation Context)

## Core Principles

- **PostgreSQL is the source of truth.** All durable state lives in Postgres. Correctness never depends on Redis.
- **Redis is optimization and ephemeral state only.** Used for caching, idempotency locks, live-session hot state, and rate limits. If Redis is down, features degrade gracefully via `safe_cache.py` — never 500.
- **Enrollment is the universal access gate.** One `Enrollment` model (unique per student+course) gates chapters, async quizzes, live quiz WebSockets, and chat. Schedule confirmation creates enrollments via `get_or_create`.
- **Course is the hub.** Quizzes, messages, and sections all FK to `Course`. No separate room models: `course_id` scopes chat; `room_code` scopes live quiz sessions.
- **Server-driven real-time UI.** WebSocket clients render the last authoritative server event. No client-side session state machines.
- **Persist-then-broadcast.** Chat messages and live answers are written to Postgres before fan-out, so REST history and WS streams stay consistent.
- **Three-layer idempotency:** pre-check existing row → Redis lock → DB `unique_together` + `IntegrityError` catch. Used for quiz submit, live answers, and schedule confirm.

## Domain Model

| Model | Key decisions |
|-------|---------------|
| `User` | Two roles only: `instructor`, `student`. |
| `Course` | FK to instructor; `is_published` flag; optional FK to `Term` (from schedule branch). |
| `Chapter` | `content` JSONField (Slate/Plate); same JSON pattern as quiz questions. |
| `Enrollment` | `unique_together (student, course)`; created by direct enroll or schedule confirm. |
| `Quiz` / `Question` | FK to course; `body` JSONField; types: `single_choice`, `multiple_choice`, `short_answer`. |
| `Submission` | `unique_together (quiz, student)`; stores `answers`, `score`, `max_score`. **One row per student per quiz** — live and async share it; do not use the same quiz in both modes. |
| `LiveSession` | Unique 6-char `room_code`; status `lobby` → `active` → `ended`; `current_question_index`; FK to host. |
| `Message` | FK to course + sender; indexed on `(course, created_at)`; max 1000 chars. Course **is** the chat room. |
| `Term` / `Section` / `Meeting` | Section = one course offering per term with weekly recurring time blocks. |
| `Break` | Student-only personal blocked intervals. |
| `SavedSchedule` | Draft M2M to sections; `confirmed_at` triggers enrollment creation. |

## Quiz Engine

**Two modes, shared grading module:**

1. **Async (REST only):** Student `GET take/` (answers stripped) → `POST submit/` → server-side grading → `Submission` persisted. Returns existing result on re-submit (idempotent).
2. **Live (REST lifecycle + WS runtime):** Host creates session (REST) → starts session (REST) → drives question reveal via WS `question.advance` → students answer via WS `answer.submit` → host ends via WS or REST.

**Grading is always server-side.** Clients never receive correct answers during take or live reveal. Live charts show aggregate option counts, not per-student correctness.

**Permissions:** Quiz CRUD = course instructor. Take/submit = enrolled student + published quiz. Live session create/start/end = host (course instructor). Live WS join = enrolled student or instructor.

## Messaging

- **REST** for paginated history (`GET /courses/{id}/messages/`, newest first, cursor-based).
- **WebSocket** for real-time delivery while connected.
- **Reconnect strategy:** refetch REST history on reconnect; dedupe by message ID; WS delivers only new messages while connected.
- **Access:** course instructor OR enrolled student.
- **Rate limit:** Redis counter, 10 messages per 10-second window per user per course. Degrades to allow-all if Redis down.
- **Term write lock (future):** reject new messages after `Course.term.end_date`; Celery purges messages post-term.

## WebSockets

**Stack:** Daphne ASGI → `ProtocolTypeRouter` (HTTP/WS split) → `JWTAuthMiddleware` → URLRouter → Consumer. Channels groups backed by `channels_redis`.

**Auth:** JWT sent as first `Sec-WebSocket-Protocol` entry (not query string). Same token as REST `Authorization: Bearer`.

**Routes (target, all in unified `routing.py`):**

| Route | Consumer | Group key |
|-------|----------|-----------|
| `ws/echo/` | EchoConsumer | — |
| `ws/live-quiz/{room_code}/` | LiveQuizConsumer | `live_{room_code}` |
| `ws/course-chat/{course_id}/` | CourseChatConsumer | `chat_{course_id}` |

**Live quiz protocol:** Client sends `question.advance`, `answer.submit`, `session.end`. Server sends `session.state`, `question.revealed`, `answer.accepted`, `chart.update`, `leaderboard.update`, `session.ended`, `error`. Close codes: 4001 unauthenticated, 4003 not enrolled, 4004 room not found.

**Consumer design:** Thin — validate → delegate to service → broadcast. Grading and DB writes happen synchronously in the consumer.

## Redis

**Single instance, four concerns:**

| Concern | Client | Keys |
|---------|--------|------|
| Cache / locks / rate limits | django-redis `cache` API | `quiz_submit_lock:*`, `live_submitted:*`, `live_session_state:*`, `chat_rate_limit:*`, catalog cache |
| Live chart + leaderboard | Raw `redis-py` via `cache.client.get_client()` | `live_chart:{room}:{q_id}` (HASH), `live_leaderboard:{room}` (ZSET) |
| WS fan-out | channels_redis | Group pub/sub |
| Celery | broker URL | Async tasks |

Raw client is required for chart/leaderboard because django-redis key prefix breaks `HINCRBY`/`ZINCRBY` atomicity.

**TTLs:** Submit locks 60s; live session keys 6h (+ explicit cleanup on session end); chat rate limit 10s window.

**Degradation:** Submit lock miss → DB constraint catches duplicates. Live idempotency miss → re-check Postgres. Chart/leaderboard miss → skip or fall back to Postgres. Rate limit miss → allow (auth remains).

## Leaderboard

**Two distinct systems — do not conflate:**

| | Live session | Course persistent |
|--|-------------|-------------------|
| Scope | One `room_code` | All quizzes in course |
| Transport | WS `leaderboard.update` | REST `GET /courses/{id}/leaderboard/` |
| Storage | Redis ZSET (top 10) | Postgres `SUM(score)` grouped by student |
| Lifetime | Ephemeral; cleared on session end | Permanent |
| Update | `ZINCRBY` on each live answer | Each submit writes `Submission.score` |

Usernames resolved from Postgres at broadcast time; Redis stores only user IDs + scores. Frontend uses one shared `Leaderboard` component for both.

## Scheduling

- **Generation:** Pure backtracking service (no DB writes during search). Inputs: course IDs, term, student breaks. Constraints: no meeting overlaps, no break conflicts. Output: up to 200 valid section combinations (50k node cap).
- **Instructor blocked intervals:** their own meetings in other courses (not `Break` model).
- **Flow:** select term/courses → define breaks → `POST generate/` → browse candidates → `POST saved/` → `POST confirm/` → creates `Enrollment` rows (idempotent).
- **Calendar UI:** `WeeklyScheduleCalendar` + `ScheduleBuilder` wizard.
- **Term integration:** `Course.term` FK drives chat write-lock and message purge after `term.end_date`.

## Frontend

- React SPA on `:3000`; Django/Daphne on `:8000`.
- Axios + JWT interceptor for REST; `ReconnectingSocket` with subprotocol auth and exponential backoff for WS.
- `AuthContext` restores session via `/auth/me/` on load. Tokens in `sessionStorage`.
- No token refresh flow yet — 24h access token expiry acceptable for MVP.
- Shared components: `Leaderboard.js`, `LiveBarChart.js`, `WeeklyScheduleCalendar.js`, PlateEditor for JSON content.

## Integration & Merge

**Branch merge order:** settings/permissions → Redis/safe_cache → Channels/JWT WS → quiz engine → live quiz/leaderboard → messaging (add chat route to routing) → scheduling (add `Course.term` FK) → load tests → frontend pages.

**Critical merge points:** unified `lms_project/routing.py` (all WS routes), `settings.py` (INSTALLED_APPS, Channels, Redis, Celery), `Course.term` FK, root `urls.py` includes all app URL modules.

## Operational

- Stateless JWT enables horizontal scaling; all Daphne workers share Redis (cache + Channels layer).
- Load testing via Locust (REST) + asyncio WS scripts; `LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS` env flag for benchmarks.
- Production: env-based secrets, `DEBUG=False`, restricted CORS, Daphne ASGI, Celery Beat for periodic tasks, Docker Compose for local dev.
