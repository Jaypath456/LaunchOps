"""
ASGI config for lms_project project.

It exposes the ASGI callable as a module-level variable named ``application``.

HTTP requests are served by Django exactly as before (REST API + admin,
unchanged); WebSocket connections are routed through JWTAuthMiddleware
(see ws_auth.py) into the URL patterns aggregated in routing.py.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lms_project.settings')

# get_asgi_application() must run first - it triggers django.setup(),
# populating the app registry that routing.py/ws_auth.py depend on
# (both import Django models) before we import them below.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from .routing import websocket_urlpatterns  # noqa: E402
from .ws_auth import JWTAuthMiddlewareStack  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
})
