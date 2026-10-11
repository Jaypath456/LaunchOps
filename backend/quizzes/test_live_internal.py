from django.test import override_settings
from rest_framework.test import APITestCase

from courses.models import Course, Enrollment
from users.models import User
from .models import LiveSession, Quiz, Question


@override_settings(LIVE_SERVICE_SECRET='local-internal-test-secret-long-enough')
class LiveServicePermissionsTests(APITestCase):
    def setUp(self):
        self.host = User.objects.create_user(username='host', password='password123', role='instructor')
        self.student = User.objects.create_user(username='learner', password='password123', role='student')
        self.outsider = User.objects.create_user(username='outsider', password='password123', role='student')
        course = Course.objects.create(title='Internal test', instructor=self.host, is_published=True)
        Enrollment.objects.create(course=course, student=self.student)
        quiz = Quiz.objects.create(course=course, title='Internal quiz', is_published=True)
        Question.objects.create(quiz=quiz, body={'prompt': [], 'options': [
            {'id': 'a', 'text': 'A'}, {'id': 'b', 'text': 'B'}], 'correct_option_ids': ['b']})
        self.room = LiveSession.objects.create(quiz=quiz, host=self.host, room_code='INTR01', engine='go')
        self.url = '/api/_internal/live/INTR01/bootstrap/'
        self.service = {'HTTP_X_LIVE_SERVICE': 'local-internal-test-secret-long-enough'}

    def test_service_secret_and_enrollment_required(self):
        self.client.force_authenticate(user=self.student)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_authenticate(user=self.outsider)
        self.assertEqual(self.client.get(self.url, **self.service).status_code, 403)
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url, **self.service)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['host'])
        self.assertNotIn('correct_option_ids', response.data['questions'][0]['public']['body'])

    def test_only_host_can_finish(self):
        url = '/api/_internal/live/INTR01/finish/'
        self.client.force_authenticate(user=self.student)
        self.assertEqual(self.client.post(url, **self.service).status_code, 403)
        self.client.force_authenticate(user=self.host)
        self.assertEqual(self.client.post(url, **self.service).status_code, 200)
        self.room.refresh_from_db()
        self.assertEqual(self.room.status, 'ended')
