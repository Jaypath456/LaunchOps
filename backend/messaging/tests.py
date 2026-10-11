import datetime
import json

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Enrollment
from lms_project.asgi import application
from schedule.models import Term
from users.models import User

from .consumers import RATE_LIMIT_MAX_MESSAGES
from .models import MAX_MESSAGE_LENGTH, Message
from .tasks import purge_ended_term_chats


class MessageHistoryAPITests(APITestCase):
    """REST history (GET /api/courses/<id>/messages/)."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='chat_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='chat_student', password='password123', role='student'
        )
        self.outsider = User.objects.create_user(
            username='chat_outsider', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Chat Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.student, course=self.course)

        for i in range(25):
            Message.objects.create(
                course=self.course,
                sender=self.instructor if i % 2 == 0 else self.student,
                body=f'Message {i}',
            )

    def test_enrolled_student_can_read_history(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 20)  # default page_size

    def test_instructor_can_read_history(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 25)

    def test_outsider_cannot_read_history(self):
        self.client.force_authenticate(user=self.outsider)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_history_is_newest_first(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.data['results'][0]['body'], 'Message 24')

    def test_a_different_courses_history_is_isolated(self):
        other_instructor = User.objects.create_user(
            username='chat_other_instructor', password='password123', role='instructor'
        )
        other_course = Course.objects.create(
            title='Other Chat Course', instructor=other_instructor, is_published=True
        )
        Message.objects.create(course=other_course, sender=other_instructor, body='Unrelated message')

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        bodies = {m['body'] for m in response.data['results']}
        self.assertNotIn('Unrelated message', bodies)


class CourseChatConsumerTests(TransactionTestCase):
    """WebSocket chat behavior. Uses TransactionTestCase rather than
    TestCase - see M13's WebSocketJWTAuthTests for why async DB access
    via database_sync_to_async needs it (a documented Channels/Django
    testing limitation with TestCase's wrapping transaction)."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @database_sync_to_async
    def _make_course_with_members(self):
        instructor = User.objects.create_user(
            username='ws_chat_instructor', password='password123', role='instructor'
        )
        student = User.objects.create_user(
            username='ws_chat_student', password='password123', role='student'
        )
        outsider = User.objects.create_user(
            username='ws_chat_outsider', password='password123', role='student'
        )
        course = Course.objects.create(title='WS Chat Course', instructor=instructor, is_published=True)
        Enrollment.objects.create(student=student, course=course)
        return instructor, student, outsider, course

    @database_sync_to_async
    def _access_token(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        return str(RefreshToken.for_user(user).access_token)

    @database_sync_to_async
    def _message_count(self, course_id):
        return Message.objects.filter(course_id=course_id).count()

    async def _connect(self, user, course_id):
        token = await self._access_token(user)
        communicator = WebsocketCommunicator(
            application, f'/ws/chat/{course_id}/', subprotocols=[token]
        )
        connected, _ = await communicator.connect()
        return communicator, connected

    async def test_enrolled_student_and_instructor_share_the_room(self):
        instructor, student, _outsider, course = await self._make_course_with_members()

        instructor_comm, instructor_connected = await self._connect(instructor, course.id)
        student_comm, student_connected = await self._connect(student, course.id)

        self.assertTrue(instructor_connected)
        self.assertTrue(student_connected)

        await student_comm.send_to(text_data=json.dumps({'body': 'Hello, class!'}))

        instructor_received = json.loads(await instructor_comm.receive_from())
        student_received = json.loads(await student_comm.receive_from())

        self.assertEqual(instructor_received['body'], 'Hello, class!')
        self.assertEqual(instructor_received['sender_username'], 'ws_chat_student')
        self.assertEqual(student_received['body'], 'Hello, class!')

        await instructor_comm.disconnect()
        await student_comm.disconnect()

    async def test_non_enrolled_non_instructor_is_rejected(self):
        _instructor, _student, outsider, course = await self._make_course_with_members()

        _comm, connected = await self._connect(outsider, course.id)

        self.assertFalse(connected)

    async def test_accept_echoes_the_token_subprotocol(self):
        # Regression test: real browsers (Chrome confirmed) fail a
        # WebSocket handshake outright if the server's response omits
        # Sec-WebSocket-Protocol after the client offered one - a bare
        # accept() does exactly that. WebsocketCommunicator doesn't
        # enforce this the way a real browser does, so this has to be
        # checked explicitly via connect()'s returned subprotocol rather
        # than relying on `connected` being True. See
        # lms_project/ws_auth.py::negotiated_subprotocol.
        instructor, _student, _outsider, course = await self._make_course_with_members()
        token = await self._access_token(instructor)

        communicator = WebsocketCommunicator(
            application, f'/ws/chat/{course.id}/', subprotocols=[token]
        )
        connected, subprotocol = await communicator.connect()

        self.assertTrue(connected)
        self.assertEqual(subprotocol, token)

        await communicator.disconnect()

    async def test_anonymous_connection_is_rejected(self):
        _instructor, _student, _outsider, course = await self._make_course_with_members()

        communicator = WebsocketCommunicator(application, f'/ws/chat/{course.id}/')
        connected, _ = await communicator.connect()

        self.assertFalse(connected)

    async def test_nonexistent_course_is_rejected(self):
        instructor, _student, _outsider, _course = await self._make_course_with_members()

        _comm, connected = await self._connect(instructor, 999999)

        self.assertFalse(connected)

    async def test_message_is_persisted_before_broadcast(self):
        instructor, student, _outsider, course = await self._make_course_with_members()
        comm, _ = await self._connect(student, course.id)

        await comm.send_to(text_data=json.dumps({'body': 'Persisted?'}))
        await comm.receive_from()  # wait for the broadcast to arrive

        count = await self._message_count(course.id)
        self.assertEqual(count, 1)

        await comm.disconnect()

    async def test_empty_message_is_rejected_and_not_persisted(self):
        _instructor, student, _outsider, course = await self._make_course_with_members()
        comm, _ = await self._connect(student, course.id)

        await comm.send_to(text_data=json.dumps({'body': '   '}))
        response = json.loads(await comm.receive_from())

        self.assertIn('error', response)
        count = await self._message_count(course.id)
        self.assertEqual(count, 0)

        await comm.disconnect()

    async def test_message_over_length_cap_is_rejected(self):
        _instructor, student, _outsider, course = await self._make_course_with_members()
        comm, _ = await self._connect(student, course.id)

        too_long = 'x' * (MAX_MESSAGE_LENGTH + 1)
        await comm.send_to(text_data=json.dumps({'body': too_long}))
        response = json.loads(await comm.receive_from())

        self.assertIn('error', response)
        count = await self._message_count(course.id)
        self.assertEqual(count, 0)

        await comm.disconnect()

    async def test_rate_limit_triggers_after_max_messages(self):
        _instructor, student, _outsider, course = await self._make_course_with_members()
        comm, _ = await self._connect(student, course.id)

        for i in range(RATE_LIMIT_MAX_MESSAGES):
            await comm.send_to(text_data=json.dumps({'body': f'msg {i}'}))
            response = json.loads(await comm.receive_from())
            self.assertNotIn('error', response)

        # One more than the limit within the same window must be refused.
        await comm.send_to(text_data=json.dumps({'body': 'one too many'}))
        response = json.loads(await comm.receive_from())
        self.assertIn('error', response)

        count = await self._message_count(course.id)
        self.assertEqual(count, RATE_LIMIT_MAX_MESSAGES)

        await comm.disconnect()

    async def test_two_course_rooms_are_fully_isolated(self):
        instructor, student, _outsider, course_a = await self._make_course_with_members()

        @database_sync_to_async
        def make_second_course():
            course_b = Course.objects.create(title='WS Chat Course B', instructor=instructor, is_published=True)
            Enrollment.objects.create(student=student, course=course_b)
            return course_b

        course_b = await make_second_course()

        comm_a, connected_a = await self._connect(student, course_a.id)
        comm_b, connected_b = await self._connect(student, course_b.id)
        self.assertTrue(connected_a)
        self.assertTrue(connected_b)

        await comm_a.send_to(text_data=json.dumps({'body': 'only for room A'}))
        received_a = json.loads(await comm_a.receive_from())
        self.assertEqual(received_a['body'], 'only for room A')

        # Room B's communicator must receive nothing from room A's traffic.
        got_nothing = await comm_b.receive_nothing(timeout=0.5)
        self.assertTrue(got_nothing)

        await comm_a.disconnect()
        await comm_b.disconnect()

    @database_sync_to_async
    def _make_course_with_ended_term(self):
        instructor = User.objects.create_user(
            username='ws_chat_ended_instructor', password='password123', role='instructor'
        )
        student = User.objects.create_user(
            username='ws_chat_ended_student', password='password123', role='student'
        )
        term = Term.objects.create(
            name='Ended Term',
            start_date=datetime.date.today() - datetime.timedelta(days=60),
            end_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        course = Course.objects.create(
            title='Ended Term Chat Course', instructor=instructor, is_published=True, term=term
        )
        Enrollment.objects.create(student=student, course=course)
        return instructor, student, course

    async def test_write_is_refused_once_term_has_ended(self):
        _instructor, student, course = await self._make_course_with_ended_term()
        comm, connected = await self._connect(student, course.id)

        # Connecting to read/observe is still allowed - only the write
        # itself is refused.
        self.assertTrue(connected)

        await comm.send_to(text_data=json.dumps({'body': 'too late'}))
        response = json.loads(await comm.receive_from())

        self.assertIn('error', response)
        count = await self._message_count(course.id)
        self.assertEqual(count, 0)

        await comm.disconnect()

    async def test_write_succeeds_for_a_course_with_no_term(self):
        _instructor, student, _outsider, course = await self._make_course_with_members()
        comm, _ = await self._connect(student, course.id)

        await comm.send_to(text_data=json.dumps({'body': 'still open'}))
        response = json.loads(await comm.receive_from())

        self.assertNotIn('error', response)
        await comm.disconnect()


class PurgeEndedTermChatsTaskTests(APITestCase):
    """Covers the Celery task that implements 'chat resets after the
    course tenure has ended' - hard-deleting Message rows for courses
    whose term has ended."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='purge_instructor', password='password123', role='instructor'
        )

    def _make_course(self, term=None):
        return Course.objects.create(
            title=f'Purge Course {term or "no-term"}',
            instructor=self.instructor,
            is_published=True,
            term=term,
        )

    def test_purges_only_messages_from_ended_term_courses(self):
        ended_term = Term.objects.create(
            name='Ended', start_date=datetime.date.today() - datetime.timedelta(days=60),
            end_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        active_term = Term.objects.create(
            name='Active', start_date=datetime.date.today(),
            end_date=datetime.date.today() + datetime.timedelta(days=30),
        )
        ended_course = self._make_course(term=ended_term)
        active_course = self._make_course(term=active_term)
        no_term_course = self._make_course(term=None)

        Message.objects.create(course=ended_course, sender=self.instructor, body='old message')
        Message.objects.create(course=active_course, sender=self.instructor, body='current message')
        Message.objects.create(course=no_term_course, sender=self.instructor, body='untermed message')

        deleted_count = purge_ended_term_chats()

        self.assertEqual(deleted_count, 1)
        self.assertFalse(Message.objects.filter(course=ended_course).exists())
        self.assertTrue(Message.objects.filter(course=active_course).exists())
        self.assertTrue(Message.objects.filter(course=no_term_course).exists())

    def test_purge_is_idempotent(self):
        ended_term = Term.objects.create(
            name='Ended Again', start_date=datetime.date.today() - datetime.timedelta(days=60),
            end_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        course = self._make_course(term=ended_term)
        Message.objects.create(course=course, sender=self.instructor, body='old message')

        first_run = purge_ended_term_chats()
        second_run = purge_ended_term_chats()

        self.assertEqual(first_run, 1)
        self.assertEqual(second_run, 0)
