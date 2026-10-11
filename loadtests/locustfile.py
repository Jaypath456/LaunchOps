"""Locust load-test scenarios for the LMS REST API.

Run against a seeded backend (see loadtests/README.md for the full
setup):
    python manage.py seed_demo --students 1000

Then, from the repo root:
    locust -f loadtests/locustfile.py --host http://localhost:8000

Each simulated user logs in once as one of the seeded
loadtest_student_NNNN accounts, then repeatedly exercises the exact
request sequence the frontend already makes for the same flows (see
frontend/src/pages/student/Catalog.js and CourseView.js) - this file
replays the real access pattern, it doesn't invent a synthetic one.

NUM_SEEDED_STUDENTS must match whatever --students value was used when
seeding, so logins are spread across the full seeded population instead
of hammering a handful of accounts.
"""
import random

import gevent
from locust import HttpUser, between, task

NUM_SEEDED_STUDENTS = 1000
DEMO_PASSWORD = 'password123'
LOAD_TEST_COURSE_TITLE = 'Introduction to Python'
LOAD_TEST_QUIZ_TITLE = 'Load Test Quiz'

# Fraction of quiz submissions that fire as a genuinely concurrent pair
# instead of a single request - see take_and_submit_quiz. Mirrors a
# flaky mobile connection retrying a request it wrongly believes timed
# out, or an impatient double-tap; REPORT.md's original run had this at
# 0, which is why it only ever measured the idempotency fast path's
# cost and never its benefit.
DOUBLE_SUBMIT_FRACTION = 0.15


class LMSStudent(HttpUser):
    """Mirrors a real student session: log in once, then repeatedly
    browse the catalog, view+enroll in a course (the exact multi-request
    burst CourseView.js's Promise.all triple-fetch produces), and take
    the seeded load-test quiz."""

    wait_time = between(1, 3)

    def on_start(self):
        student_index = random.randint(0, NUM_SEEDED_STUDENTS - 1)
        username = f'loadtest_student_{student_index:04d}'

        response = self.client.post(
            '/api/auth/login/',
            json={'username': username, 'password': DEMO_PASSWORD},
            name='POST /api/auth/login/',
        )
        if response.status_code != 200:
            # A failed login means the account wasn't seeded - fail
            # loudly rather than silently running a user that can never
            # do anything, which would just look like "everything is
            # fast" for all the wrong reasons.
            response.failure(f'Login failed for {username}: {response.status_code} {response.text[:200]}')
            self.environment.runner.quit()
            return

        token = response.json()['access']
        self.client.headers.update({'Authorization': f'Bearer {token}'})

        self.course_id = None
        self.quiz_id = None
        self.question_id = None

    @task(3)
    def browse_catalog(self):
        self.client.get('/api/courses/', name='GET /api/courses/')

    @task(2)
    def view_course_and_enroll(self):
        course_id = self._resolve_course_id()
        if course_id is None:
            return

        # The same three-request parallel burst CourseView.js issues via
        # Promise.all on every course page load.
        self.client.get(f'/api/courses/{course_id}/', name='GET /api/courses/[id]/')
        self.client.get(f'/api/courses/{course_id}/chapters/', name='GET /api/courses/[id]/chapters/')
        self.client.get(
            f'/api/courses/{course_id}/enrollment-status/',
            name='GET /api/courses/[id]/enrollment-status/',
        )
        self.client.post(f'/api/courses/{course_id}/enroll/', name='POST /api/courses/[id]/enroll/')

    @task(1)
    def take_and_submit_quiz(self):
        quiz_id, question_id = self._resolve_quiz()
        if quiz_id is None or question_id is None:
            return

        self.client.get(f'/api/quizzes/{quiz_id}/take/', name='GET /api/quizzes/[id]/take/')

        payload = {'answers': {str(question_id): ['b']}}

        # DOUBLE_SUBMIT_FRACTION of students fire a genuinely concurrent
        # duplicate pair instead of a single submit - the case the Redis
        # idempotency lock (quizzes/views.py::quiz_submit, layer 2)
        # exists for. Everyone else still submits exactly once, so
        # 'POST /api/quizzes/[id]/submit/'s own numbers stay a clean
        # read of first-submission cost; the duplicate pair is tracked
        # under its own request name (see _submit_quiz_twice) precisely
        # so it doesn't get mixed into that baseline.
        if random.random() < DOUBLE_SUBMIT_FRACTION:
            self._submit_quiz_twice(quiz_id, payload)
        else:
            self.client.post(
                f'/api/quizzes/{quiz_id}/submit/',
                json=payload,
                name='POST /api/quizzes/[id]/submit/',
            )

    def _submit_quiz_twice(self, quiz_id, payload):
        """Fires two identical submits from separate greenlets so they
        can genuinely overlap in flight, instead of the second one only
        starting once the first has already committed - a sequential
        retry would never actually contend for the Redis lock or race
        in Postgres, and would silently understate what this is meant
        to measure.

        All three possible outcomes are a *correct* result for a
        duplicate submit, not a failure:
          - 201: this request won the race and created the row.
          - 200: it read back a row a concurrent request already
            committed (layer 1's existence check, or layer 3's
            IntegrityError catch after losing the race).
          - 409: Redis layer 2 rejected it as a concurrent duplicate
            before either request reached Postgres.
        Comparing how often each status appears, and this request
        name's overall failure rate, between LOAD_TEST_DISABLE_REDIS_
        OPTIMIZATIONS on vs. off is what actually tells you whether the
        lock earns its keep - unlike p95 latency on a no-duplicates run.
        """
        def submit():
            return self.client.post(
                f'/api/quizzes/{quiz_id}/submit/',
                json=payload,
                name='POST /api/quizzes/[id]/submit/ (concurrent duplicate)',
                catch_response=True,
            )

        greenlets = [gevent.spawn(submit), gevent.spawn(submit)]
        gevent.joinall(greenlets)

        for g in greenlets:
            response = g.value
            if response.status_code in (200, 201, 409):
                response.success()
            else:
                response.failure(f'Unexpected status for concurrent duplicate submit: {response.status_code}')

    def _resolve_course_id(self):
        if self.course_id is not None:
            return self.course_id

        # response = self.client.get('/api/courses/', name='GET /api/courses/ (resolve fixtures)')
        # courses = response.json() if response.status_code == 200 else []
        # matches = [c for c in courses if c['title'] == LOAD_TEST_COURSE_TITLE]
        response = self.client.get('/api/courses/', name='GET /api/courses/ (resolve fixtures)')
        payload = response.json() if response.status_code == 200 else []
        courses = payload.get("results", payload) if isinstance(payload, dict) else payload
        matches = [c for c in courses if c['title'] == LOAD_TEST_COURSE_TITLE]
        if not matches:
            return None

        self.course_id = matches[0]['id']
        return self.course_id

    def _resolve_quiz(self):
        if self.quiz_id is not None:
            return self.quiz_id, self.question_id

        course_id = self._resolve_course_id()
        if course_id is None:
            return None, None

        response = self.client.get(
            f'/api/courses/{course_id}/quizzes/', name='GET /api/courses/[id]/quizzes/ (resolve fixtures)'
        )
        quizzes = response.json() if response.status_code == 200 else []
        matches = [q for q in quizzes if q['title'] == LOAD_TEST_QUIZ_TITLE]
        if not matches:
            return None, None
        self.quiz_id = matches[0]['id']

        take_response = self.client.get(
            f'/api/quizzes/{self.quiz_id}/take/', name='GET /api/quizzes/[id]/take/ (resolve fixtures)'
        )
        questions = take_response.json().get('questions', []) if take_response.status_code == 200 else []
        self.question_id = questions[0]['id'] if questions else None

        return self.quiz_id, self.question_id
