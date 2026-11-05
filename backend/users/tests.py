
from django.conf import settings
from django.test import TestCase
from rest_framework_simplejwt.tokens import RefreshToken

from lms_project.celery import app as celery_app

from .models import User
from rest_framework.test import APIClient


class RoleLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_each_role_login_is_validated_without_changing_privileges(self):
        for role in ('hr', 'it', 'manager', 'employee'):
            with self.subTest(role=role):
                user = User.objects.create_user(username=role, password='exact password', role=role)
                response = self.client.post('/api/auth/login/', {'username': role, 'password': 'exact password', 'role': role})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data['user']['role'], role)
                wrong = 'employee' if role != 'employee' else 'hr'
                response = self.client.post('/api/auth/login/', {'username': role, 'password': 'exact password', 'role': wrong})
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('access', response.data)
                user.refresh_from_db()
                self.assertEqual(user.role, role)

    def test_invalid_role_and_credentials_issue_no_tokens(self):
        for data, expected in [({'role': 'superuser'}, 400), ({'username': 'missing', 'password': 'bad', 'role': 'hr'}, 401)]:
            response = self.client.post('/api/auth/login/', data)
            self.assertEqual(response.status_code, expected)
            self.assertNotIn('access', response.data)

    def test_registration_and_login_preserve_exact_password(self):
        password = '  whitespace password  '
        response = self.client.post('/api/auth/register/', {'username': 'hire', 'password': password, 'role': 'hr', 'is_staff': True})
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username='hire')
        self.assertTrue(user.check_password(password))
        self.assertEqual(user.role, 'employee')
        self.assertFalse(user.is_staff)
        self.assertEqual(self.client.post('/api/auth/login/', {'username': 'hire', 'password': password}).status_code, 200)
        self.assertEqual(self.client.post('/api/auth/login/', {'username': 'hire', 'password': password.strip()}).status_code, 401)


class TokenRefreshTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='refresh_user', password='test password', role='employee')
        self.refresh = RefreshToken.for_user(self.user)

    def test_expired_access_refreshes_and_new_access_uses_current_server_role(self):
        from datetime import timedelta
        access = self.refresh.access_token
        access.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        self.user.role = 'manager'
        self.user.save()
        response = self.client.post('/api/auth/refresh/', {'refresh': str(self.refresh)})
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['access'])
        self.assertEqual(self.client.get('/api/auth/me/').data['role'], 'manager')

    def test_invalid_expired_and_wrong_token_types_are_rejected(self):
        from datetime import timedelta
        expired = RefreshToken.for_user(self.user)
        expired.set_exp(lifetime=timedelta(seconds=-1))
        for token in ('invalid', str(expired), str(self.refresh.access_token)):
            with self.subTest(token_type=token[:8]):
                response = self.client.post('/api/auth/refresh/', {'refresh': token})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('access', response.data)
        self.assertEqual(self.client.post('/api/auth/refresh/', {}).status_code, 400)

    def test_inactive_or_deleted_account_cannot_refresh(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': str(self.refresh)}).status_code, 401)
        self.user.delete()
        self.assertEqual(self.client.post('/api/auth/refresh/', {'refresh': str(self.refresh)}).status_code, 401)


class CeleryScaffoldTests(TestCase):
    """Celery wiring checks. lms_project is not a Django app, so they live here."""

    def test_beat_schedule_runs_the_onboarding_scan(self):
        entry = settings.CELERY_BEAT_SCHEDULE['scan-onboarding-deadlines']
        self.assertEqual(entry['task'], 'onboarding.tasks.scan_overdue')

    def test_chat_purge_is_not_scheduled_by_default(self):
        self.assertNotIn('purge-ended-term-chats-daily', settings.CELERY_BEAT_SCHEDULE)

    def test_celery_broker_and_result_backend_point_at_redis_url(self):
        self.assertEqual(settings.CELERY_BROKER_URL, settings.REDIS_URL)
        self.assertEqual(settings.CELERY_RESULT_BACKEND, settings.REDIS_URL)
