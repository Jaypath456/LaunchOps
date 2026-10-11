"""Live (Mentimeter-style) quiz session consumer.

State machine: the coarse lifecycle (lobby/active/ended) is REST-driven
(M16, quizzes/views.py::session_create/start/end); everything within an
active session - which question is revealed, accepting/rejecting
answers, the live chart - is entirely WebSocket-driven here. Clients
never decide what to show; they render whatever state the server last
pushed, which is what makes "locked waiting screen until the host
advances" and safe reconnects both trivial: session.state (sent on
every connect) is the single source of truth for what a client should
be rendering at that instant.

Persistence: answers are graded and written straight into a Submission
row (the exact same model + grading function M6's async quiz-taking
uses) as they arrive, incrementing its score field - there is no
separate "live answers" model or migration. A student who has both an
async attempt and a live-session attempt at the same quiz share one
Submission row (quiz, student unique_together, from M6); this is a
deliberate scope simplification for this milestone, not an oversight -
seehe module-level NOTE below.

NOTE (known simplification): mixing an M6 async attempt and a live
session for the *same* quiz is out of scope here - Submission's
(quiz, student) uniqueness means they'd share one row. In practice a
quiz used for a live session should simply not also be taken async.
Separating the two properly would need either a nullable `session` FK
on Submission (a real migration) or a session-scoped model - deferred
until a concrete need shows up, per the "no new migrations" scope for
this milestone.
"""
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from courses.models import Enrollment
from lms_project.safe_cache import safe_add
from lms_project.ws_auth import negotiated_subprotocol
from users.models import User

from . import live_state
from .grading import is_answer_correct
from .models import LiveSession, Question, Submission
from .serializers import StudentQuestionSerializer

ANSWER_LOCK_TTL_SECONDS = 60 * 60 * 6


def _submitted_key(room_code, question_id, user_id):
    return f'live_submitted:{room_code}:{question_id}:{user_id}'


def _answer_key_for(question):
    """The correct-answer info for a question, sent only to the live
    session's host (see _handle_advance/_send_state_snapshot) so the
    instructor's results screen can mark each bar correct/incorrect -
    never broadcast to the room group, which only ever gets
    StudentQuestionSerializer's sanitized body (see module docstring).
    """
    body = question.body or {}
    if question.question_type in (Question.SINGLE_CHOICE, Question.MULTIPLE_CHOICE):
        return {'correct_option_ids': body.get('correct_option_ids', [])}
    if question.question_type == Question.SHORT_ANSWER:
        return {'correct_answer': body.get('correct_answer', '')}
    return {}


def _chart_buckets_for_answer(question, answer):
    """Which live-chart bucket(s) an accepted answer increments. Choice
    questions get one bucket per selected option (the classic Mentimeter
    bar-per-option chart); short_answer collapses to a simple
    correct/incorrect tally since free text doesn't have discrete
    options to bar-chart.
    """
    if question.question_type in (Question.SINGLE_CHOICE, Question.MULTIPLE_CHOICE):
        if isinstance(answer, list):
            return [str(option_id) for option_id in answer]
        return []
    if question.question_type == Question.SHORT_ANSWER:
        return ['correct'] if is_answer_correct(question, answer) else ['incorrect']
    return []


class LiveQuizConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_code = self.scope['url_route']['kwargs']['room_code']
        user = self.scope['user']

        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        session, enrolled = await self._authorize_room(user.id)
        if session is None or session.engine != 'django':
            await self.close(code=4004)
            return

        self.quiz_id = session.quiz_id
        self.is_host = session.host_id == user.id

        if not self.is_host and not enrolled:
            await self.close(code=4003)
            return

        # One Channels group per room_code - unique per LiveSession
        # (M16), so concurrent sessions (even of the same quiz) never
        # cross traffic, regardless of how many are running at once.
        self.room_group_name = f'live_{self.room_code}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept(subprotocol=negotiated_subprotocol(self.scope))

        # Every connect (fresh join or reconnect) gets the current
        # state pushed immediately - this is what makes a late joiner
        # or a reconnecting client land on the correct screen without
        # any special-case client logic.
        await self._send_state_snapshot()

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            await self._send_error('Invalid message format.')
            return

        event_type = data.get('type')

        if event_type == 'question.advance':
            await self._handle_advance()
        elif event_type == 'answer.submit':
            await self._handle_submit(data)
        elif event_type == 'session.end':
            await self._handle_end()
        else:
            await self._send_error(f'Unknown event type: {event_type!r}')

    # ─── Host-only actions ──────────────────────────────────────

    async def _handle_advance(self):
        if not self.is_host:
            await self._send_error('Only the host can advance questions.')
            return

        result = await self._advance_to_next_question()
        if result is None:
            await self._send_error('Session is not active, or there are no more questions.')
            return

        next_index, revealed_at = result
        question = await self._question_at_index(next_index)
        payload = StudentQuestionSerializer(question).data

        await self.channel_layer.group_send(self.room_group_name, {
            'type': 'broadcast_question_revealed',
            'question': payload,
            'question_index': next_index,
            'revealed_at': revealed_at,
            # Delivered to every consumer in the group, but only merged
            # into the outgoing message for whichever one is the host
            # (see broadcast_question_revealed) - a single broadcast
            # rather than a second host-only message, so message order
            # stays exactly one-event-per-advance for every listener.
            'answer_key': _answer_key_for(question),
        })

    async def _handle_end(self):
        if not self.is_host:
            await self._send_error('Only the host can end the session.')
            return

        await self._finalize_session()

        await self.channel_layer.group_send(self.room_group_name, {
            'type': 'broadcast_session_ended',
        })

    # ─── Student actions ─────────────────────────────────────────

    async def _handle_submit(self, data):
        user = self.scope['user']
        question_id = data.get('question_id')
        answer = data.get('answer')

        if question_id is None:
            await self._send_error('question_id is required.')
            return

        result = await self._record_answer(user.id, question_id, answer)

        if result['status'] == 'invalid':
            await self._send_error('This question is not currently open.')
            return

        # Both a fresh accept and a de-duplicated resubmit tell the
        # submitter "accepted" - from their point of view a retried
        # request should look identical to the original success, the
        # same idempotency contract as M6's async quiz_submit. is_correct
        # is what drives the student's own correct/incorrect result
        # screen (LiveQuiz.js) - unlike the room-wide chart/leaderboard
        # broadcasts below, this is a private message to this connection
        # only, so it's safe to reveal correctness here even though the
        # question's answer key is never broadcast to the room.
        await self.send(text_data=json.dumps({
            'type': 'answer.accepted',
            'question_id': question_id,
            'is_correct': result['is_correct'],
        }))

        if result['status'] == 'duplicate':
            return

        chart_counts = await self._get_chart_counts(question_id)
        await self.channel_layer.group_send(self.room_group_name, {
            'type': 'broadcast_chart_update',
            'question_id': question_id,
            'counts': chart_counts,
        })

        # Redis keeps the sorted set ordered automatically (see
        # live_state.increment_leaderboard_score, called from inside
        # _record_answer) - there's no separate "recompute rankings"
        # step here, just read the already-sorted top N and broadcast
        # it to the whole room, the same way Alice answering correctly
        # and moving to 1st place should update everyone's view in
        # under a second.
        leaderboard = await self._get_leaderboard()
        await self.channel_layer.group_send(self.room_group_name, {
            'type': 'broadcast_leaderboard_update',
            'rankings': leaderboard,
        })

    # ─── Group broadcast handlers ────────────────────────────────
    # (invoked by Channels for every consumer in the group, including
    # the sender - so the submitter's own chart view updates the same
    # way everyone else's does, with no special-cased "local echo".)

    async def broadcast_question_revealed(self, event):
        payload = {
            'type': 'question.revealed',
            'question': event['question'],
            'question_index': event['question_index'],
            'revealed_at': event['revealed_at'],
        }
        # Every consumer in the group runs this same handler with the
        # same event dict - self.is_host is what makes only the host's
        # own copy of the message carry the answer key, never the
        # students'.
        if self.is_host:
            payload.update(event.get('answer_key', {}))
        await self.send(text_data=json.dumps(payload))

    async def broadcast_chart_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'chart.update',
            'question_id': event['question_id'],
            'counts': event['counts'],
        }))

    async def broadcast_session_ended(self, event):
        await self.send(text_data=json.dumps({'type': 'session.ended'}))

    async def broadcast_leaderboard_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'leaderboard.update',
            'rankings': event['rankings'],
        }))

    async def _send_error(self, detail):
        await self.send(text_data=json.dumps({'error': detail}))

    # ─── State snapshot (connect / reconnect) ───────────────────

    async def _send_state_snapshot(self):
        state = await self._current_state()
        payload = {
            'type': 'session.state',
            'status': state['status'],
            'question_index': state['current_question_index'],
            'revealed_at': state.get('question_revealed_at'),
        }

        if state['status'] == LiveSession.ACTIVE:
            # Standings persist across questions (unlike the chart,
            # which is per-question), so a late joiner sees them
            # regardless of whether a question happens to be open right
            # now.
            payload['leaderboard'] = await self._get_leaderboard()

            if state['current_question_index'] >= 0:
                question = await self._question_at_index(state['current_question_index'])
                if question is not None:
                    payload['question'] = StudentQuestionSerializer(question).data
                    payload['chart'] = await self._get_chart_counts(question.id)

                    # Host-only, same reasoning as _handle_advance's
                    # question.answer_key - a reconnecting host needs the
                    # answer key back too, not just a fresh advance.
                    if self.is_host:
                        payload.update(_answer_key_for(question))

        await self.send(text_data=json.dumps(payload))

    async def _current_state(self):
        cached = live_state.get_session_state(self.room_code)
        if cached is not None:
            return cached
        return await self._load_state_from_db()

    # ─── DB/Redis helpers ────────────────────────────────────────

    @database_sync_to_async
    def _authorize_room(self, user_id):
        # Keep authorization in one database thread/connection. Re-fetching
        # the room in a second call queues every learner twice during joins.
        session = LiveSession.objects.select_related('quiz').filter(room_code=self.room_code).first()
        enrolled = bool(session and (session.host_id == user_id or
            Enrollment.objects.filter(student_id=user_id, course_id=session.quiz.course_id).exists()))
        return session, enrolled

    @database_sync_to_async
    def _load_state_from_db(self):
        session = LiveSession.objects.get(room_code=self.room_code)
        # question_revealed_at is Redis-only ephemeral state (see
        # live_state.set_session_state) - if Redis was flushed/restarted,
        # this fallback has no way to know it, so a reconnecting client
        # just gets a fresh full-length countdown instead of a resumed
        # one. Acceptable: this only happens on the Redis-down/flushed
        # edge case, not on every reconnect.
        return {
            'status': session.status,
            'current_question_index': session.current_question_index,
            'question_revealed_at': None,
        }

    @database_sync_to_async
    def _question_at_index(self, index):
        session = LiveSession.objects.select_related('quiz').get(room_code=self.room_code)
        questions = list(session.quiz.questions.order_by('order_index', 'id'))
        if 0 <= index < len(questions):
            return questions[index]
        return None

    @database_sync_to_async
    def _advance_to_next_question(self):
        session = LiveSession.objects.select_related('quiz').get(room_code=self.room_code)
        if session.status != LiveSession.ACTIVE:
            return None

        total_questions = session.quiz.questions.count()
        next_index = session.current_question_index + 1
        if next_index >= total_questions:
            return None

        session.current_question_index = next_index
        session.save(update_fields=['current_question_index'])
        revealed_at = timezone.now().isoformat()
        live_state.set_session_state(session, question_revealed_at=revealed_at)
        return next_index, revealed_at

    @database_sync_to_async
    def _record_answer(self, user_id, question_id, answer):
        question = Question.objects.filter(id=question_id, quiz_id=self.quiz_id).first()
        if question is None:
            return {'status': 'invalid', 'is_correct': None}

        # The submitted question must be the one currently revealed -
        # answers to a question that's already closed (or not yet
        # opened) are rejected rather than silently accepted. Read the
        # index straight from the DB (not the Redis mirror) since this
        # is the correctness-critical check for whether to award points.
        session = LiveSession.objects.filter(room_code=self.room_code).only('current_question_index').first()
        current_index = session.current_question_index if session is not None else -1
        question_ids_in_order = list(
            Question.objects.filter(quiz_id=self.quiz_id).order_by('order_index', 'id').values_list('id', flat=True)
        )
        if not (0 <= current_index < len(question_ids_in_order)) or question_ids_in_order[current_index] != question.id:
            return {'status': 'invalid', 'is_correct': None}

        is_correct = is_answer_correct(question, answer)

        # Idempotency: SETNX-style, the same pattern as M12's quiz_submit
        # lock - absorbs a double-click/retried request without ever
        # double-counting the score or the chart for this question.
        lock_key = _submitted_key(self.room_code, question_id, user_id)
        if not safe_add(lock_key, True, timeout=ANSWER_LOCK_TTL_SECONDS):
            return {'status': 'duplicate', 'is_correct': is_correct}

        points_earned = question.points if is_correct else 0

        with transaction.atomic():
            submission, created = Submission.objects.select_for_update().get_or_create(
                quiz_id=self.quiz_id,
                student_id=user_id,
                defaults={'answers': {}, 'score': 0, 'max_score': 0},
            )
            if created:
                total_points = Question.objects.filter(quiz_id=self.quiz_id).aggregate(
                    total=Sum('points')
                )['total'] or 0
                submission.max_score = total_points

            submission.answers[str(question_id)] = answer
            submission.score = submission.score + points_earned
            submission.save(update_fields=['answers', 'score', 'max_score'])

        for bucket in _chart_buckets_for_answer(question, answer):
            live_state.increment_chart_bucket(self.room_code, question_id, bucket)

        # Every accepted answer updates the leaderboard, even a wrong
        # one worth 0 points - this is what makes a participant appear
        # on the board (at 0) as soon as they've answered anything,
        # rather than only once they get something right.
        live_state.increment_leaderboard_score(self.room_code, user_id, points_earned)

        return {'status': 'ok', 'is_correct': is_correct}

    @database_sync_to_async
    def _get_chart_counts(self, question_id):
        return live_state.get_chart_counts(self.room_code, question_id)

    @database_sync_to_async
    def _get_leaderboard(self):
        raw = live_state.get_leaderboard(self.room_code)
        if not raw:
            return []

        user_ids = [int(user_id_str) for user_id_str, _score in raw]
        usernames_by_id = dict(User.objects.filter(id__in=user_ids).values_list('id', 'username'))

        return [
            {
                'user_id': int(user_id_str),
                'username': usernames_by_id.get(int(user_id_str), 'Unknown'),
                'score': score,
                'rank': index + 1,
            }
            for index, (user_id_str, score) in enumerate(raw)
        ]

    @database_sync_to_async
    def _finalize_session(self):
        session = LiveSession.objects.select_related('quiz').get(room_code=self.room_code)

        session.status = LiveSession.ENDED
        session.ended_at = timezone.now()
        session.save(update_fields=['status', 'ended_at'])

        question_ids = list(session.quiz.questions.values_list('id', flat=True))
        live_state.clear_all_live_state(session.room_code, question_ids)
