from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path('ws/live/<str:room_code>/', consumers.LiveQuizConsumer.as_asgi()),
]
