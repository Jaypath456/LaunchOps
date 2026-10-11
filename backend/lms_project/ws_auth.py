"""JWT authentication for WebSocket connections, validating the same
SimpleJWT access tokens the REST API already issues (see
users/views.py::login/register - one token type, two transports).

The token travels as a WebSocket subprotocol - the second argument to
the browser's `new WebSocket(url, [accessToken])` constructor, which
becomes the `Sec-WebSocket-Protocol` request header. This is
deliberately not a query string: query strings routinely end up in
server access logs and browser history, which is exactly what we don't
want for a bearer token. A subprotocol is also readable directly from
`scope` before `accept()` is ever called (see EchoConsumer), so an
invalid/missing token can be rejected before the WebSocket handshake
even completes - unlike waiting for the client's first application-level
message, which ASGI can't deliver until after the connection is already
accepted.
"""
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from users.models import User


@database_sync_to_async
def _get_user_from_token(token):
    try:
        validated = AccessToken(token)
        return User.objects.get(id=validated['user_id'])
    except (TokenError, InvalidToken, User.DoesNotExist, KeyError):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """Populates scope['user'] from a JWT sent as the first
    Sec-WebSocket-Protocol entry. Falls back to AnonymousUser if the
    token is missing or invalid - consumers are responsible for
    rejecting anonymous connections themselves (see EchoConsumer), the
    same way DRF permission classes are applied per-view rather than
    globally for the REST API.
    """

    async def __call__(self, scope, receive, send):
        token = None
        subprotocols = scope.get('subprotocols') or []
        if subprotocols:
            token = subprotocols[0]

        scope['user'] = await _get_user_from_token(token) if token else AnonymousUser()

        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)


def negotiated_subprotocol(scope):
    """The subprotocol every consumer must pass to accept(subprotocol=...).

    The client always offers exactly one WS subprotocol - the JWT itself
    (see the module docstring above and frontend/src/api/ws.js) - never
    to actually negotiate a protocol, just to get the token to this
    middleware before accept(). But per RFC 6455 a server handshake
    response is expected to echo back one of the offered subprotocols
    whenever the client's request included any; several browsers
    (Chrome included) treat a response that omits the header entirely -
    which is what a bare accept() sends - as a failed handshake, not a
    successful connection with no subprotocol selected. This showed up
    as every real-browser WebSocket connection being torn down
    immediately after Daphne's access log already reported CONNECT
    (the server-side handshake genuinely succeeded; the browser then
    aborted on its own before the JS WebSocket ever reached OPEN), while
    both the test client (WebsocketCommunicator) and plain Python
    `websockets` clients used in load testing are lenient about this
    and never surfaced it. Consumers must use this instead of bare
    accept().
    """
    subprotocols = scope.get('subprotocols') or []
    return subprotocols[0] if subprotocols else None
