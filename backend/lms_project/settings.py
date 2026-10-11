from pathlib import Path
from datetime import timedelta
import os
from celery.schedules import crontab
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _get_bool_env(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def _get_list_env(name, default=''):
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured(
        'SECRET_KEY environment variable is not set. '
        'Copy backend/.env.example to backend/.env and set a unique SECRET_KEY '
        '(e.g. `python -c "import secrets; print(secrets.token_urlsafe(50))"`).'
    )

DEBUG = _get_bool_env('DEBUG', default=False)

ALLOWED_HOSTS = _get_list_env('ALLOWED_HOSTS', default='localhost,127.0.0.1')

INSTALLED_APPS = [
    # Must be listed before django.contrib.staticfiles so that
    # `manage.py runserver` automatically uses Daphne (ASGI) instead of
    # Django's WSGI dev server - required for WebSocket support locally.
    # Production deployments should invoke daphne directly regardless.
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'users',
    'courses',
    'quizzes',
    'schedule',
    'messaging',
    'onboarding',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'lms_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'lms_project.wsgi.application'
ASGI_APPLICATION = 'lms_project.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3' if os.getenv('DB_ENGINE') == 'sqlite' else 'django.db.backends.postgresql',
        'NAME': str(BASE_DIR / 'db.sqlite3') if os.getenv('DB_ENGINE') == 'sqlite' else os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
    }
}

AUTH_USER_MODEL = 'users.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    # Only applies automatically to generic/class-based views. The
    # function-based views in courses/views.py that need pagination
    # (course_list) apply courses.pagination.StandardResultsPagination
    # explicitly - this default exists so any future generic/class-based
    # views (e.g. upcoming quizzes app) get sane pagination for free.
    'DEFAULT_PAGINATION_CLASS': 'courses.pagination.StandardResultsPagination',
    'PAGE_SIZE': 12,
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=24),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
}

CORS_ALLOWED_ORIGINS = _get_list_env('CORS_ALLOWED_ORIGINS', default='http://localhost:3000')
CORS_ALLOW_CREDENTIALS = True

# ─── Redis (cache + Celery broker) ─────────────────────────────
# Every feature that touches Redis in this project (caching, the quiz
# submission idempotency lock) is written to degrade gracefully if Redis
# is unreachable - see lms_project/safe_cache.py. Redis is an
# optimization here, never a correctness requirement; the real
# guarantees are DB constraints (see quizzes/views.py::quiz_submit).
REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
    }
}

# ─── Celery ─────────────────────────────────────────────────────
# No real jobs yet beyond a heartbeat (see lms_project/tasks.py) - this
# is the scaffolding a later milestone (course chat's tenure-reset purge
# job) will build on.
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

CELERY_BEAT_SCHEDULE = {
    'scan-onboarding-deadlines': {
        'task': 'onboarding.tasks.scan_overdue',
        'schedule': crontab(minute='*/15'),
    },
    'heartbeat-every-minute': {
        'task': 'lms_project.tasks.heartbeat',
        'schedule': 60.0,
    },
    'purge-ended-term-chats-daily': {
        'task': 'messaging.tasks.purge_ended_term_chats',
        'schedule': crontab(hour=3, minute=0),
    },
}

# ─── Channels (WebSockets) ──────────────────────────────────────
# Same Redis instance as the cache/Celery broker - separate logical use,
# same physical server is fine for this project's scale.
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [REDIS_URL],
        },
    },
}

# ─── Load testing: before/after toggle ─────────────────────────
# Flips off the Redis-backed optimizations (catalog caching in
# courses/views.py, the quiz-submit idempotency fast path in
# quizzes/views.py) while leaving Redis itself running - this isolates
# exactly the contribution those optimizations make in the load-test
# comparison (loadtests/), rather than conflating "Redis optimizations
# off" with "Redis unreachable" (which is the *degrade gracefully*
# scenario safe_cache already covers, and a different thing to measure).
# Never set outside of load testing - defaults to False (optimizations
# on) everywhere else, including production.
LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS = (
    os.getenv('LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS', 'False').strip().lower() == 'true'
)
LIVE_SERVICE_SECRET = os.getenv('LIVE_SERVICE_SECRET', '')
LIVE_QUIZ_ENGINE = os.getenv('LIVE_QUIZ_ENGINE', 'django')
