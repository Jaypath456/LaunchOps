import datetime

from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Enrollment
from schedule.models import Term
from users.models import User

from .models import Message
from .tasks import purge_ended_term_chats


class MessageHistoryAPITests(APITestCase):
    """REST history (GET /api/courses/<id>/messages/)."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='chat_instructor', password='password123', role='instructor'
        )
        self.student = User.objects.create_user(
            username='chat_student', password='password123', role='student'
        )
        self.outsider = User.objects.create_user(
            username='chat_outsider', password='password123', role='student'
        )
        self.course = Course.objects.create(
            title='Chat Course', instructor=self.instructor, is_published=True
        )
        Enrollment.objects.create(student=self.student, course=self.course)

        for i in range(25):
            Message.objects.create(
                course=self.course,
                sender=self.instructor if i % 2 == 0 else self.student,
                body=f'Message {i}',
            )

    def test_enrolled_student_can_read_history(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 20)  # default page_size

    def test_instructor_can_read_history(self):
        self.client.force_authenticate(user=self.instructor)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 25)

    def test_outsider_cannot_read_history(self):
        self.client.force_authenticate(user=self.outsider)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_history_is_newest_first(self):
        self.client.force_authenticate(user=self.student)

        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        self.assertEqual(response.data['results'][0]['body'], 'Message 24')

    def test_a_different_courses_history_is_isolated(self):
        other_instructor = User.objects.create_user(
            username='chat_other_instructor', password='password123', role='instructor'
        )
        other_course = Course.objects.create(
            title='Other Chat Course', instructor=other_instructor, is_published=True
        )
        Message.objects.create(course=other_course, sender=other_instructor, body='Unrelated message')

        self.client.force_authenticate(user=self.instructor)
        response = self.client.get(f'/api/courses/{self.course.id}/messages/')

        bodies = {m['body'] for m in response.data['results']}
        self.assertNotIn('Unrelated message', bodies)


class PurgeEndedTermChatsTaskTests(APITestCase):
    """Covers the Celery task that implements 'chat resets after the
    course tenure has ended' - hard-deleting Message rows for courses
    whose term has ended."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='purge_instructor', password='password123', role='instructor'
        )

    def _make_course(self, term=None):
        return Course.objects.create(
            title=f'Purge Course {term or "no-term"}',
            instructor=self.instructor,
            is_published=True,
            term=term,
        )

    def test_purges_only_messages_from_ended_term_courses(self):
        ended_term = Term.objects.create(
            name='Ended', start_date=datetime.date.today() - datetime.timedelta(days=60),
            end_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        active_term = Term.objects.create(
            name='Active', start_date=datetime.date.today(),
            end_date=datetime.date.today() + datetime.timedelta(days=30),
        )
        ended_course = self._make_course(term=ended_term)
        active_course = self._make_course(term=active_term)
        no_term_course = self._make_course(term=None)

        Message.objects.create(course=ended_course, sender=self.instructor, body='old message')
        Message.objects.create(course=active_course, sender=self.instructor, body='current message')
        Message.objects.create(course=no_term_course, sender=self.instructor, body='untermed message')

        deleted_count = purge_ended_term_chats()

        self.assertEqual(deleted_count, 1)
        self.assertFalse(Message.objects.filter(course=ended_course).exists())
        self.assertTrue(Message.objects.filter(course=active_course).exists())
        self.assertTrue(Message.objects.filter(course=no_term_course).exists())

    def test_purge_is_idempotent(self):
        ended_term = Term.objects.create(
            name='Ended Again', start_date=datetime.date.today() - datetime.timedelta(days=60),
            end_date=datetime.date.today() - datetime.timedelta(days=1),
        )
        course = self._make_course(term=ended_term)
        Message.objects.create(course=course, sender=self.instructor, body='old message')

        first_run = purge_ended_term_chats()
        second_run = purge_ended_term_chats()

        self.assertEqual(first_run, 1)
        self.assertEqual(second_run, 0)
