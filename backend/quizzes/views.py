from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from courses.models import Course, Enrollment
from courses.permissions import IsCourseInstructor, IsInstructor, IsStudent
from lms_project.safe_cache import safe_add, safe_delete

from . import live_state
from .grading import grade_quiz
from .models import LiveSession, Question, Quiz, Submission, generate_room_code
from .serializers import (
    LiveSessionSerializer,
    QuestionSerializer,
    QuizSerializer,
    StudentQuestionSerializer,
    SubmissionResultSerializer,
)

SUBMIT_LOCK_TIMEOUT_SECONDS = 60
ROOM_CODE_GENERATION_ATTEMPTS = 10


def _submit_lock_key(quiz_id, user_id):
    return f'quiz_submit_lock:{quiz_id}:{user_id}'


def _is_quiz_owner(request, quiz):
    # IsCourseInstructor.has_object_permission already handles any object
    # exposing a `course` FK - a Quiz has exactly that, so this reuses the
    # existing permission class as-is (see courses/permissions.py).
    return IsCourseInstructor().has_object_permission(request, None, quiz)


def _is_question_owner(request, question):
    # Question only has a `quiz` FK, not `course` directly, so resolve one
    # hop first and then reuse the same IsCourseInstructor check against
    # the parent quiz.
    return IsCourseInstructor().has_object_permission(request, None, question.quiz)


def _is_enrolled(user, course):
    # Mirrors the exact enrollment-membership check already used inline
    # in courses/views.py::chapter_detail - kept local to this app rather
    # than importing a permission class that doesn't exist yet on this
    # branch (courses.permissions.IsEnrolled lands in a separate,
    # not-yet-merged milestone). Once that merges, this can be replaced
    # with IsEnrolled().has_object_permission(...).
    return Enrollment.objects.filter(student=user, course=course).exists()


# ─── Quiz Views ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quiz_list(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    is_owner = IsCourseInstructor().has_object_permission(request, None, course)

    if is_owner:
        quizzes = Quiz.objects.filter(course=course)
    elif course.is_published:
        quizzes = Quiz.objects.filter(course=course, is_published=True)
    else:
        return Response({'error': 'This course is not available'}, status=status.HTTP_403_FORBIDDEN)

    serializer = QuizSerializer(quizzes.order_by('-created_at', 'id'), many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def quiz_create(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    if not IsCourseInstructor().has_object_permission(request, None, course):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    serializer = QuizSerializer(data=request.data)
    if serializer.is_valid():
        quiz = serializer.save(course=course)
        return Response(QuizSerializer(quiz).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def quiz_detail(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    is_owner = _is_quiz_owner(request, quiz)

    if request.method == 'GET':
        if not is_owner and not quiz.is_published:
            return Response({'error': 'This quiz is not available'}, status=status.HTTP_403_FORBIDDEN)
        return Response(QuizSerializer(quiz).data)

    if not is_owner:
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'PUT':
        serializer = QuizSerializer(quiz, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    quiz.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Question Views (instructor-only) ────────────
# Student-facing question access (with correct answers stripped) lives in
# the "Student Quiz-Taking Views" section below.

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsInstructor])
def question_list(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)

    if not _is_quiz_owner(request, quiz):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    questions = quiz.questions.order_by('order_index', 'id')
    return Response(QuestionSerializer(questions, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def question_create(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)

    if not _is_quiz_owner(request, quiz):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    serializer = QuestionSerializer(data=request.data)
    if serializer.is_valid():
        question = serializer.save(quiz=quiz)
        return Response(QuestionSerializer(question).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated, IsInstructor])
def question_detail(request, question_id):
    question = get_object_or_404(Question, id=question_id)

    if not _is_question_owner(request, question):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        return Response(QuestionSerializer(question).data)

    if request.method == 'PUT':
        serializer = QuestionSerializer(question, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    question.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Student Quiz-Taking Views ─────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def quiz_take(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id, is_published=True)

    if not _is_enrolled(request.user, quiz.course):
        return Response(
            {'error': 'You must be enrolled in this course to take this quiz'},
            status=status.HTTP_403_FORBIDDEN,
        )

    questions = quiz.questions.order_by('order_index', 'id')
    return Response({
        'quiz': QuizSerializer(quiz).data,
        'questions': StudentQuestionSerializer(questions, many=True).data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def quiz_submit(request, quiz_id):
    from django.db import transaction
    from onboarding.models import Onboarding, Event
    with transaction.atomic():
        record = None
        if request.user.role == 'employee':
            # Same lock order as training completion and retry.
            record = Onboarding.objects.select_for_update().filter(employee=request.user, training_course__quizzes__id=quiz_id).first()
        response = _quiz_submit(request, quiz_id)
        if record and response.status_code == 201:
            Event.objects.create(onboarding=record, actor=request.user, step=record.steps.get(key='training'),
                action='assessment_submitted', detail=f'Assessment {quiz_id}: {response.data["score"]}/{response.data["max_score"]} (server graded)')
        return response


def _quiz_submit(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id, is_published=True)

    if not _is_enrolled(request.user, quiz.course):
        return Response(
            {'error': 'You must be enrolled in this course to submit this quiz'},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Idempotency has three layers, cheapest/fastest first:
    #
    # 1. Existence check: unique_together (quiz, student) means a second
    #    submit can never create a second row, so if a Submission
    #    already exists (an earlier request, possibly seconds/minutes
    #    ago), this is a cheap read instead of re-grading.
    existing = Submission.objects.filter(quiz=quiz, student=request.user).first()
    if existing is not None:
        return Response(SubmissionResultSerializer(existing).data, status=status.HTTP_200_OK)

    # 2. Redis SETNX-style lock: absorbs a *concurrent* duplicate
    #    (double-click, retried in-flight request) without ever hitting
    #    Postgres for the loser. safe_add degrades to "lock acquired"
    #    (True) if Redis itself is unreachable, so an outage here never
    #    blocks a legitimate first-time submission - layer 3 below is
    #    the real guarantee regardless of whether this layer worked.
    #    See settings.LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS - skipping
    #    straight to layer 3 is exactly the "before" configuration
    #    loadtests/ compares against.
    lock_key = _submit_lock_key(quiz.id, request.user.id)
    if not settings.LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS:
        if not safe_add(lock_key, True, timeout=SUBMIT_LOCK_TIMEOUT_SECONDS):
            return Response(
                {'error': 'A submission for this quiz is already being processed. Please retry.'},
                status=status.HTTP_409_CONFLICT,
            )

    try:
        if set(request.data) != {'answers'}:
            return Response({'error': 'Submit answers only; scores are computed by the server.'}, status=400)
        answers = request.data.get('answers', {})
        if not isinstance(answers, dict):
            return Response({'error': 'answers must be an object mapping question id to answer'},
                             status=status.HTTP_400_BAD_REQUEST)

        score, max_score = grade_quiz(quiz, answers)

        # 3. DB unique_together + IntegrityError catch: the actual
        #    correctness guarantee, covering the rare case where layer 2
        #    didn't apply (Redis was down) or two lock keys somehow both
        #    got created (e.g. after a Redis failover).
        try:
            with transaction.atomic():
                submission = Submission.objects.create(
                    quiz=quiz,
                student=request.user,
                answers=answers,
                score=score,
                max_score=max_score,
            )
        except IntegrityError:
            submission = Submission.objects.get(quiz=quiz, student=request.user)
            return Response(SubmissionResultSerializer(submission).data, status=status.HTTP_200_OK)

        return Response(SubmissionResultSerializer(submission).data, status=status.HTTP_201_CREATED)
    finally:
        safe_delete(lock_key)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def quiz_my_result(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)

    submission = Submission.objects.filter(quiz=quiz, student=request.user).first()
    if submission is None:
        return Response({'error': 'No submission found for this quiz'}, status=status.HTTP_404_NOT_FOUND)

    return Response(SubmissionResultSerializer(submission).data)


# ─── Live Sessions (room lifecycle) ─────────────────────────────
# REST here only covers the coarse session lifecycle (create/start/end).
# The fine-grained per-question state machine (revealing a question,
# accepting answers, broadcasting results) is WebSocket-driven and lives
# in a later milestone's consumer - this is deliberately just the
# lobby/create/start/end scaffolding around it.

def _generate_unique_room_code():
    for _ in range(ROOM_CODE_GENERATION_ATTEMPTS):
        code = generate_room_code()
        if not LiveSession.objects.filter(room_code=code).exists():
            return code
    # Astronomically unlikely with a 36^6 keyspace, but fail loudly
    # rather than silently reusing/colliding a room code.
    raise RuntimeError('Could not generate a unique room code.')


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def session_create(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)

    if not _is_quiz_owner(request, quiz):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    engine = request.data.get('engine', settings.LIVE_QUIZ_ENGINE)
    if engine not in ('django', 'go'):
        return Response({'engine': 'Choose django or go.'}, status=400)

    session = LiveSession.objects.create(
        quiz=quiz, host=request.user, room_code=_generate_unique_room_code(), engine=engine
    )
    live_state.set_session_state(session)
    return Response(LiveSessionSerializer(session).data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def session_detail(request, room_code):
    # Deliberately open to any authenticated user, not just the host or
    # quiz owner: this is how a student "joins" a live session - by
    # looking up the room code they were given. The consumer (a later
    # milestone) is what actually enforces course enrollment before
    # letting them participate over the WebSocket.
    session = get_object_or_404(LiveSession, room_code=room_code)
    return Response(LiveSessionSerializer(session).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def session_start(request, room_code):
    session = get_object_or_404(LiveSession, room_code=room_code)

    if session.host_id != request.user.id:
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    if session.status != LiveSession.LOBBY:
        return Response(
            {'error': f'Cannot start a session with status "{session.status}".'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    session.status = LiveSession.ACTIVE
    # -1 = active, but no question has been revealed yet. The live quiz
    # consumer's question.advance handler (a later milestone) moves this
    # to 0 for the *first* reveal - starting a session and revealing its
    # first question are deliberately separate host actions (REST vs
    # WebSocket), matching the Mentimeter-style "waiting room" UX.
    session.current_question_index = -1
    session.started_at = timezone.now()
    session.save(update_fields=['status', 'current_question_index', 'started_at'])
    live_state.set_session_state(session)

    return Response(LiveSessionSerializer(session).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def session_end(request, room_code):
    session = get_object_or_404(LiveSession, room_code=room_code)

    if session.host_id != request.user.id:
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    if session.engine == 'go':
        return Response({'error': 'End this Go room through its host WebSocket.'}, status=409)

    if session.status == LiveSession.ENDED:
        # Idempotent: ending an already-ended session just returns its
        # current (unchanged) state instead of erroring.
        return Response(LiveSessionSerializer(session).data)

    session.status = LiveSession.ENDED
    session.ended_at = timezone.now()
    session.save(update_fields=['status', 'ended_at'])

    # Full teardown regardless of whether the host ended the session via
    # this REST escape hatch or the WebSocket session.end event
    # (consumers.py) - either path must leave no ephemeral state behind.
    question_ids = Question.objects.filter(quiz_id=session.quiz_id).values_list('id', flat=True)
    live_state.clear_all_live_state(session.room_code, question_ids)

    return Response(LiveSessionSerializer(session).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def course_leaderboard(request, course_id):
    if request.user.role == 'employee':
        return Response({'error': 'Leaderboards are unavailable for employee training.'}, status=403)
    """The persistent counterpart to the live leaderboard: total score
    across every quiz Submission in this course, for every student who
    has submitted anything. Computed via annotation (not the Redis
    sorted set, which is per-session and ephemeral) since this spans
    every quiz/session a course has ever had, indefinitely.
    """
    course = get_object_or_404(Course, id=course_id)

    is_owner = course.instructor_id == request.user.id
    if not is_owner and not Enrollment.objects.filter(student=request.user, course=course).exists():
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    rows = (
        Submission.objects.filter(quiz__course=course)
        .values('student_id', 'student__username')
        .annotate(total_score=Sum('score'))
        .order_by('-total_score', 'student__username')
    )

    rankings = [
        {
            'user_id': row['student_id'],
            'username': row['student__username'],
            'score': row['total_score'],
            'rank': index + 1,
        }
        for index, row in enumerate(rows)
    ]

    return Response(rankings)
