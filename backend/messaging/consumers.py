"""Course global chat consumer.

Flow per message: validate -> rate-limit -> persist to Postgres ->
broadcast to the room's Channels group. Persist-then-broadcast (not the
other way around) means a message that gets to any client always exists
in REST history too - a client that reconnects or was offline sees the
exact same message set everyone else already saw, fetched via
GET /api/courses/<id>/messages/ (messaging/views.py::message_list).
"""
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache
from django.utils import timezone

from courses.models import Course
from lms_project.ws_auth import negotiated_subprotocol

from .models import MAX_MESSAGE_LENGTH, Message
from .permissions import can_access_course_chat
from .serializers import MessageSerializer

RATE_LIMIT_WINDOW_SECONDS = 10
RATE_LIMIT_MAX_MESSAGES = 10


class CourseChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.course_id = self.scope['url_route']['kwargs']['course_id']
        user = self.scope['user']

        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        course = await self._get_course(self.course_id)
        if course is None:
            await self.close(code=4004)
            return

        if not await self._can_access(user, course):
            await self.close(code=4003)
            return

        # The room IS the course - one Channels group per course id, so
        # two different courses' chats never see each other's traffic
        # regardless of how many rooms are active concurrently.
        self.room_group_name = f'chat_{self.course_id}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept(subprotocol=negotiated_subprotocol(self.scope))

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        user = self.scope['user']

        try:
            data = json.loads(text_data)
            body = data.get('body', '')
        except (TypeError, ValueError):
            await self._send_error('Invalid message format.')
            return

        if not isinstance(body, str) or not body.strip():
            await self._send_error('Message body cannot be empty.')
            return

        body = body.strip()
        if len(body) > MAX_MESSAGE_LENGTH:
            await self._send_error(f'Message too long (max {MAX_MESSAGE_LENGTH} characters).')
            return

        if not await self._check_rate_limit(user.id):
            await self._send_error('You are sending messages too quickly. Please slow down.')
            return

        # Re-checked per message, not just at connect() - a long-lived
        # connection could still be open at the exact moment a course's
        # term ends. Reading history is unaffected; only new writes are
        # refused (see get_chat_open on CourseSerializer for the REST-
        # visible signal the frontend uses to disable the composer
        # proactively, before a send is even attempted).
        if not await self._is_writable():
            await self._send_error('This course has ended. Chat is now read-only.')
            return

        message = await self._create_message(user.id, body)
        payload = await self._serialize_message(message)

        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'chat.message', 'message': payload},
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event['message']))

    async def _send_error(self, detail):
        await self.send(text_data=json.dumps({'error': detail}))

    @database_sync_to_async
    def _get_course(self, course_id):
        return Course.objects.filter(id=course_id).first()

    @database_sync_to_async
    def _can_access(self, user, course):
        return can_access_course_chat(user, course)

    @database_sync_to_async
    def _is_writable(self):
        course = Course.objects.select_related('term').filter(id=self.course_id).first()
        if course is None or course.term_id is None:
            return True
        return course.term.end_date >= timezone.now().date()

    @database_sync_to_async
    def _create_message(self, sender_id, body):
        return Message.objects.create(course_id=self.course_id, sender_id=sender_id, body=body)

    @database_sync_to_async
    def _serialize_message(self, message):
        return MessageSerializer(message).data

    @database_sync_to_async
    def _check_rate_limit(self, user_id):
        # Uses database_sync_to_async purely to run in the same worker
        # thread pool as the DB calls above, even though this only
        # touches Redis - keeps all the blocking I/O in this consumer
        # off the asyncio event loop consistently.
        key = f'chat_rate_limit:{self.course_id}:{user_id}'
        try:
            # cache.add is a no-op (returns False) if the window is
            # already open; either way, incr atomically bumps the count
            # without resetting the TTL set by whichever request opened
            # the window first (Redis INCR never touches expiry).
            cache.add(key, 0, timeout=RATE_LIMIT_WINDOW_SECONDS)
            count = cache.incr(key)
            return count <= RATE_LIMIT_MAX_MESSAGES
        except Exception:
            # Redis unavailable - rate limiting is an abuse-prevention
            # optimization, not a correctness requirement. Degrade to
            # "allowed" rather than blocking chat entirely.
            return True
