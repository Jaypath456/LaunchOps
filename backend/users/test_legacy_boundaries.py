from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from users.models import User
from courses.models import Course, Chapter, Enrollment
from messaging.permissions import can_access_course_chat
from quizzes.models import Quiz, Question, Submission
from schedule.models import Term, Section
from onboarding.models import Event
from onboarding.services import create_onboarding


class LegacyBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = {role: User.objects.create(username=role, role=role) for role in
                     ('hr', 'it', 'manager', 'employee', 'student', 'instructor')}
        cls.course = Course.objects.create(title='Training', instructor=cls.users['instructor'], is_published=True)
        cls.chapter = Chapter.objects.create(course=cls.course, title='Lesson', content=[{'text': 'Enrolled content'}], visibility='public')
        cls.quiz = Quiz.objects.create(course=cls.course, title='Quiz', is_published=True)
        cls.question = Question.objects.create(quiz=cls.quiz, body={'options': [{'id': 'a', 'text': 'Yes'}], 'correct_option_ids': ['a']})
        cls.record = create_onboarding(employee=cls.users['employee'], manager=cls.users['manager'], creator=cls.users['hr'],
                                       job_title='Engineer', department='Demo', start_date=timezone.localdate())
        Enrollment.objects.create(student=cls.users['student'], course=cls.course)
        Enrollment.objects.create(student=cls.users['employee'], course=cls.course)
        term = Term.objects.create(name='Audit term', start_date=timezone.localdate(), end_date=timezone.localdate())
        cls.section = Section.objects.create(course=cls.course, term=term)

    def setUp(self):
        self.client = APIClient()

    def test_onboarding_roles_cannot_author_lms_or_use_legacy_participation(self):
        for role in ('hr', 'it', 'manager', 'employee'):
            self.client.force_authenticate(self.users[role])
            for method, url in [('post', '/api/courses/create/'), ('post', f'/api/courses/{self.course.pk}/quizzes/create/'),
                                ('get', f'/api/quizzes/{self.quiz.pk}/questions/'),
                                ('get', '/api/terms/'), ('get', '/api/breaks/'), ('post', '/api/schedule/generate/'),
                                ('get', '/api/schedule/saved/'),
                                ('get', f'/api/courses/{self.course.pk}/messages/')]:
                with self.subTest(role=role, url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 403)
            self.assertFalse(can_access_course_chat(self.users[role], self.course))
        self.assertEqual(Event.objects.filter(onboarding=self.record).count(), 1)

    def test_employee_training_is_available_but_assignment_cannot_be_removed(self):
        self.client.force_authenticate(self.users['employee'])
        for url in (f'/api/chapters/{self.chapter.pk}/', f'/api/quizzes/{self.quiz.pk}/take/', '/api/my-courses/'):
            self.assertEqual(self.client.get(url).status_code, 200)
        for method in ('post', 'delete'):
            self.assertEqual(getattr(self.client, method)(f'/api/courses/{self.course.pk}/enroll/').status_code, 403)
        self.assertTrue(Enrollment.objects.filter(student=self.users['employee'], course=self.course).exists())
        take = self.client.get(f'/api/quizzes/{self.quiz.pk}/take/')
        self.assertNotIn('correct_option_ids', str(take.data))
        self.assertEqual(self.client.get(f'/api/courses/{self.course.pk}/leaderboard/').status_code, 403)

    def test_legacy_student_still_uses_chat_and_does_not_see_employee_scores(self):
        self.client.force_authenticate(self.users['student'])
        Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=1, max_score=1)
        Submission.objects.create(quiz=self.quiz, student=self.users['student'], score=1, max_score=1)
        self.assertTrue(can_access_course_chat(self.users['student'], self.course))
        self.assertEqual(self.client.get(f'/api/courses/{self.course.pk}/messages/').status_code, 200)
        response = self.client.get(f'/api/courses/{self.course.pk}/leaderboard/')
        self.assertEqual([row['username'] for row in response.data], ['student'])

    def test_draft_metadata_and_unenrolled_lesson_content_do_not_leak(self):
        outsider = User.objects.create(username='outsider', role='student')
        self.client.force_authenticate(outsider)
        response = self.client.get(f'/api/courses/{self.course.pk}/chapters/')
        self.assertNotIn('content', response.data[0])
        self.course.is_published = False
        self.course.save()
        for url in (f'/api/quizzes/{self.quiz.pk}/', f'/api/courses/{self.course.pk}/sections/', f'/api/sections/{self.section.pk}/'):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(self.users['instructor'])
        self.assertEqual(self.client.get(f'/api/sections/{self.section.pk}/').status_code, 200)
