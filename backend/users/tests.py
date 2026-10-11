import json

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TestCase, TransactionTestCase
from rest_framework_simplejwt.tokens import RefreshToken

from lms_project.asgi import application
from lms_project.celery import app as celery_app
from lms_project.tasks import heartbeat

from .models import User


class CeleryScaffoldTests(TestCase):
    """lms_project isn't a Django "app" (no models/views of its own),
    so these infrastructure checks live here rather than needing a
    dedicated test-only app. Covers the M12 Celery scaffold: no real
    scheduled jobs exist yet beyond this heartbeat, which exists to
    prove the worker/beat wiring is correct before anything depends on
    it (the course-chat tenure-reset purge job, in a later milestone).
    """

    def test_heartbeat_task_runs_and_returns_ok(self):
        # Task functions decorated with @app.task remain directly
        # callable without a running worker/broker - this exercises the
        # actual task body, not Celery's dispatch machinery.
        self.assertEqual(heartbeat(), 'ok')

    def test_heartbeat_is_registered_with_the_celery_app(self):
        self.assertIn('lms_project.tasks.heartbeat', celery_app.tasks)

    def test_beat_schedule_includes_the_heartbeat(self):
        self.assertIn('heartbeat-every-minute', settings.CELERY_BEAT_SCHEDULE)
        entry = settings.CELERY_BEAT_SCHEDULE['heartbeat-every-minute']
        self.assertEqual(entry['task'], 'lms_project.tasks.heartbeat')

    def test_celery_broker_and_result_backend_point_at_redis_url(self):
        self.assertEqual(settings.CELERY_BROKER_URL, settings.REDIS_URL)
        self.assertEqual(settings.CELERY_RESULT_BACKEND, settings.REDIS_URL)


class WebSocketJWTAuthTests(TransactionTestCase):
    """Covers the M13 Channels + JWT auth foundation via EchoConsumer,
    the temporary acceptance-test consumer for this milestone. Uses the
    same SimpleJWT access tokens the REST login/register endpoints
    already issue (users/views.py) - one auth system, two transports.

    Uses TransactionTestCase rather than TestCase: async test methods
    that touch the DB via database_sync_to_async run the query in a
    separate thread, which doesn't see TestCase's per-test wrapping
    transaction (a documented Channels/Django testing limitation) and
    fails with "connection already closed". TransactionTestCase resets
    the DB by truncation between tests instead, avoiding that entirely.
    """

    @database_sync_to_async
    def _create_user(self):
        return User.objects.create_user(
            username='ws_auth_user', password='password123', role='student'
        )

    @database_sync_to_async
    def _access_token_for(self, user):
        return str(RefreshToken.for_user(user).access_token)

    async def test_valid_token_connects_and_echoes(self):
        user = await self._create_user()
        token = await self._access_token_for(user)

        communicator = WebsocketCommunicator(application, '/ws/echo/', subprotocols=[token])
        connected, subprotocol = await communicator.connect()
        self.assertTrue(connected)
        # Regression test: a bare accept() (no subprotocol echoed back)
        # passes Channels' own WebsocketCommunicator - it isn't strict
        # about this like real browsers are - but real browsers (Chrome
        # confirmed) treat a handshake response that omits
        # Sec-WebSocket-Protocol as failed when the client offered one,
        # even though the connection looks "CONNECTed" in Daphne's
        # access log right up until the browser tears it down. See
        # lms_project/ws_auth.py::negotiated_subprotocol.
        self.assertEqual(subprotocol, token)

        await communicator.send_to(text_data='hello world')
        response = await communicator.receive_from()
        payload = json.loads(response)

        self.assertEqual(payload['echo'], 'hello world')
        self.assertEqual(payload['user'], 'ws_auth_user')

        await communicator.disconnect()

    async def test_missing_token_is_rejected(self):
        communicator = WebsocketCommunicator(application, '/ws/echo/')
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_garbage_token_is_rejected(self):
        communicator = WebsocketCommunicator(application, '/ws/echo/', subprotocols=['not-a-real-jwt'])
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_expired_or_tampered_token_is_rejected(self):
        user = await self._create_user()
        token = await self._access_token_for(user)
        tampered_token = token[:-4] + 'xxxx'  # corrupt the signature

        communicator = WebsocketCommunicator(application, '/ws/echo/', subprotocols=[tampered_token])
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_two_users_get_independent_connections(self):
        user_a = await self._create_user()
        token_a = await self._access_token_for(user_a)

        @database_sync_to_async
        def create_second_user():
            return User.objects.create_user(
                username='ws_auth_user_2', password='password123', role='student'
            )

        user_b = await create_second_user()
        token_b = await self._access_token_for(user_b)

        comm_a = WebsocketCommunicator(application, '/ws/echo/', subprotocols=[token_a])
        comm_b = WebsocketCommunicator(application, '/ws/echo/', subprotocols=[token_b])

        self.assertTrue((await comm_a.connect())[0])
        self.assertTrue((await comm_b.connect())[0])

        await comm_a.send_to(text_data='from A')
        await comm_b.send_to(text_data='from B')

        response_a = json.loads(await comm_a.receive_from())
        response_b = json.loads(await comm_b.receive_from())

        self.assertEqual(response_a['user'], 'ws_auth_user')
        self.assertEqual(response_b['user'], 'ws_auth_user_2')

        await comm_a.disconnect()
        await comm_b.disconnect()
