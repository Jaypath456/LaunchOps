from importlib.util import find_spec
from django.apps import apps
from django.conf import settings
from django.core.handlers.asgi import ASGIHandler
from django.urls import Resolver404, resolve
from lms_project.asgi import application

assert isinstance(application, ASGIHandler)
assert not hasattr(settings, 'CHANNEL_LAYERS')
assert all(find_spec(name) is None for name in ('channels', 'channels_redis', 'daphne', 'pymongo'))
assert all(model.__name__ != 'LiveSession' for model in apps.get_app_config('quizzes').get_models())
for path in ('/api/quizzes/1/sessions/', '/api/sessions/ABC123/', '/api/sessions/ABC123/start/', '/api/sessions/ABC123/end/', '/api/sessions/ABC123/live-results/', '/api/_internal/live/ABC123/bootstrap/', '/api/_internal/live/ABC123/finish/'):
    try:
        resolve(path)
    except Resolver404:
        pass
    else:
        raise AssertionError(path)
for path in ('/api/quizzes/1/take/', '/api/quizzes/1/submit/', '/api/courses/1/messages/', '/api/auth/me/', '/api/onboarding/workflows/'):
    resolve(path)
print('PASS: HTTP ASGI handler, dependency removal, model removal, route removal, retained HTTP APIs.')
