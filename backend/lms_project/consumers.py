"""Project-level (not feature-app-owned) WebSocket consumers.

EchoConsumer exists solely as the acceptance test for the Channels + JWT
auth foundation built in this milestone - it isn't used by any real
feature. Later milestones (course chat, live quiz sessions) get their
own consumers living in their own apps (messaging/consumers.py,
quizzes/consumers.py), aggregated into lms_project/routing.py the same
way lms_project/urls.py aggregates each app's urls.py.
"""
import json

from channels.generic.websocket import AsyncWebsocketConsumer

from .ws_auth import negotiated_subprotocol


class EchoConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        if not user or not user.is_authenticated:
            # Reject before accept() so the handshake itself fails
            # rather than opening a connection just to immediately close
            # it - 4001 is an application-defined close code (the 4000-
            # 4999 range is reserved for this) meaning "unauthenticated".
            await self.close(code=4001)
            return
        await self.accept(subprotocol=negotiated_subprotocol(self.scope))

    async def receive(self, text_data=None, bytes_data=None):
        user = self.scope['user']
        await self.send(text_data=json.dumps({
            'echo': text_data,
            'user': user.username,
        }))
