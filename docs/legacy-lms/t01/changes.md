# t01 files changed

Modified:
- docker-compose.yml
- .env.example
- backend/.env.example
- frontend/.env.example
- backend/requirements.txt
- backend/lms_project/settings.py
- backend/lms_project/asgi.py
- backend/quizzes/models.py
- backend/quizzes/serializers.py
- backend/quizzes/views.py
- backend/quizzes/urls.py
- backend/quizzes/tests.py
- backend/courses/views.py
- backend/courses/tests.py
- backend/courses/management/commands/seed_demo.py
- backend/messaging/tests.py
- backend/users/tests.py
- backend/onboarding/test_legacy_boundaries.py
- frontend/src/App.tsx
- frontend/src/components/Navbar.tsx
- frontend/src/components/CourseChat.js
- frontend/src/pages/instructor/QuizManage.js
- frontend/src/pages/instructor/QuestionForm.js
- frontend/src/pages/instructor/instructor.css
- frontend/src/pages/student/CourseView.js
- frontend/src/pages/student/student.css
- README.md

Added:
- backend/quizzes/migrations/0006_remove_retired_sessions.py
- .night/notes.md
- .night/changes.md
- docs/evidence/t01/ (COMMAND.txt, ENV.txt, backend-tests.txt, frontend-checks.txt, system-check.txt, migration-check.txt, compose-services.txt, compose-status.txt, runtime-check.py, runtime-checks.txt, runtime-checks-initial.txt)

Deleted:
- go-live/ (entire directory)
- loadtests/ (entire directory)
- docker-compose.review.yml
- scripts/live_review.py
- scripts/live_review_browser.cjs
- scripts/review-compose.sh
- scripts/verify_benchmark_records.py
- scripts/create_audit_fixture.py
- backend/lms_project/consumers.py
- backend/lms_project/routing.py
- backend/lms_project/ws_auth.py
- backend/quizzes/consumers.py
- backend/quizzes/routing.py
- backend/quizzes/live_internal.py
- backend/quizzes/live_state.py
- backend/quizzes/test_live_internal.py
- backend/messaging/consumers.py
- backend/messaging/routing.py
- frontend/src/api/ws.ts
- frontend/src/pages/instructor/LiveQuizHost.js
- frontend/src/pages/instructor/LiveQuizHost.test.js
- frontend/src/pages/student/LiveQuiz.js
- frontend/src/components/LiveBarChart.js
- frontend/src/components/LiveBarChart.css
- frontend/src/components/Leaderboard.js
- frontend/src/components/Leaderboard.css
- docs/LIVE_ARCHITECTURE.md

Archived without altering historical contents:
- docs/AUDIT.md -> docs/legacy-lms/AUDIT.md
- docs/DEMO_DELIVERY.md -> docs/legacy-lms/DEMO_DELIVERY.md
- docs/REVIEW_UPDATED_ZIP.md -> docs/legacy-lms/REVIEW_UPDATED_ZIP.md
- docs/REVIEW_CHECKPOINT.md -> docs/legacy-lms/REVIEW_CHECKPOINT.md
- docs/audit/ -> docs/legacy-lms/audit/
