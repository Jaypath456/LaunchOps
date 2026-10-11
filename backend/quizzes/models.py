import random
import string

from django.conf import settings
from django.db import models

from courses.models import Course

ROOM_CODE_ALPHABET = string.ascii_uppercase + string.digits
ROOM_CODE_LENGTH = 6


def generate_room_code():
    return ''.join(random.choices(ROOM_CODE_ALPHABET, k=ROOM_CODE_LENGTH))


class Quiz(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='quizzes'
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Question(models.Model):
    SINGLE_CHOICE = 'single_choice'
    MULTIPLE_CHOICE = 'multiple_choice'
    SHORT_ANSWER = 'short_answer'

    QUESTION_TYPE_CHOICES = [
        (SINGLE_CHOICE, 'Single Choice'),
        (MULTIPLE_CHOICE, 'Multiple Choice'),
        (SHORT_ANSWER, 'Short Answer'),
    ]

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='questions'
    )
    question_type = models.CharField(
        max_length=20,
        choices=QUESTION_TYPE_CHOICES,
        default=SINGLE_CHOICE
    )
    # Reuses the same JSONField pattern as Chapter.content: a single
    # database-friendly document rather than a separate content model.
    # Expected shape (validated in QuestionSerializer, not at the DB
    # layer, to keep this model as simple as Chapter's):
    #   single_choice / multiple_choice:
    #     {"prompt": <Slate JSON>, "options": [{"id": str, "text": str}, ...],
    #      "correct_option_ids": [str, ...]}
    #   short_answer:
    #     {"prompt": <Slate JSON>, "correct_answer": str}
    # "prompt" uses the same Slate JSON tree shape as Chapter.content, so
    # it can be authored/rendered with the existing PlateEditor component.
    body = models.JSONField(default=dict, blank=True)
    points = models.PositiveIntegerField(default=1)
    order_index = models.IntegerField(default=0)
    # Only meaningful in a live session (quizzes/consumers.py) - drives
    # the countdown shown on the student's screen once this question is
    # revealed. Async quiz-taking (quiz_take/quiz_submit) ignores it
    # entirely; there's no clock there.
    time_limit_seconds = models.PositiveIntegerField(default=20)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order_index', 'created_at']

    def __str__(self):
        return f'{self.quiz.title} - Q{self.order_index}'


class Submission(models.Model):
    """A student's one-and-only graded attempt at a quiz.

    unique_together (quiz, student) is the idempotency anchor - it makes
    "has this student already submitted this quiz" a single indexed
    lookup/insert-or-conflict, the same pattern Enrollment already uses
    for "is this student already enrolled in this course".
    """

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='submissions'
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quiz_submissions'
    )
    # {"<question_id>": <answer>} - answer is a list of option ids for
    # single_choice/multiple_choice, or a string for short_answer. Stored
    # verbatim (not re-derived) so a submission's exact answers remain
    # inspectable/auditable after grading.
    answers = models.JSONField(default=dict, blank=True)
    score = models.PositiveIntegerField(default=0)
    max_score = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['quiz', 'student']
        ordering = ['-submitted_at']

    def __str__(self):
        return f'{self.student.username} - {self.quiz.title}: {self.score}/{self.max_score}'


class LiveSession(models.Model):
    """A live (Mentimeter-style) run of a quiz. Each session gets its own
    unique room_code so multiple sessions - even of the same quiz - run
    fully isolated from each other (the WS consumer in a later milestone
    derives its Channels group name from this code).

    Status here is deliberately coarse (lobby/active/ended) - the fine-
    grained per-question state machine (which question is open, whether
    it's accepting answers) lives in Redis via live_state.py and is
    driven by WebSocket events, not REST calls. This REST-level status
    only tracks the overall session lifecycle: has it started, is it
    running, has it ended.
    """

    LOBBY = 'lobby'
    ACTIVE = 'active'
    ENDED = 'ended'

    STATUS_CHOICES = [
        (LOBBY, 'Lobby'),
        (ACTIVE, 'Active'),
        (ENDED, 'Ended'),
    ]

    ENGINE_CHOICES = [('django', 'Django'), ('go', 'Go')]

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name='live_sessions'
    )
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hosted_live_sessions'
    )
    room_code = models.CharField(max_length=ROOM_CODE_LENGTH, unique=True, db_index=True)
    engine = models.CharField(max_length=10, choices=ENGINE_CHOICES, default='django')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=LOBBY)
    current_question_index = models.IntegerField(default=-1)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.quiz.title} - Room {self.room_code} ({self.status})'
