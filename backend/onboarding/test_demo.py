from io import StringIO
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from users.models import User
from courses.models import Course, Enrollment
from .models import Onboarding, Event, AssessmentAttempt
from .blueprint import BLUEPRINT, FORMS


class SeedSafetyTests(TestCase):
    @override_settings(DEBUG=True)
    def test_pagination_demo_is_repeatable_and_has_no_login_passwords(self):
        call_command('seed_launchops', pagination_demo=True, stdout=StringIO())
        self.assertEqual(Onboarding.objects.count(), 106)
        snapshot = list(Onboarding.objects.values_list('id', 'employee_id'))
        call_command('seed_launchops', pagination_demo=True, stdout=StringIO())
        self.assertEqual(list(Onboarding.objects.values_list('id', 'employee_id')), snapshot)
        self.assertTrue(all(not user.has_usable_password() for user in User.objects.filter(username__startswith='demo_page_')))

    @override_settings(DEBUG=False)
    def test_production_seed_refuses_before_any_write(self):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('seed_launchops', stdout=output)
        self.assertFalse(User.objects.exists())
        self.assertNotIn('LaunchOpsDemo2026!', output.getvalue())

    @override_settings(DEBUG=True)
    def test_collision_rolls_back_new_accounts(self):
        original = User.objects.create(username='demo_manager', role='hr')
        with self.assertRaises(CommandError):
            call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(list(User.objects.values_list('id', flat=True)), [original.pk])
        self.assertFalse(Course.objects.exists())


@override_settings(DEBUG=True)
class SeededDemoTests(TestCase):
    def test_seeded_workflow_failure_retry_blocker_and_final_signoff(self):
        call_command('seed_launchops', stdout=StringIO())
        users = {role: User.objects.get(username='demo_' + role) for role in ('hr', 'it', 'manager', 'employee')}
        record = Onboarding.objects.get(employee=users['employee'])
        client = APIClient()
        for chapter in record.training_course.chapters.all():
            client.force_authenticate(users['employee'])
            self.assertEqual(client.post(f'/api/onboarding/lessons/{chapter.pk}/review/', {'acknowledged': True}, format='json').status_code, 200)
        for index, quiz in enumerate(record.training_course.quizzes.all()):
            if index == 0:
                response = client.post(f'/api/quizzes/{quiz.pk}/submit/', {'answers': {}}, format='json')
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data['score'], 0)
                self.assertEqual(client.post(f'/api/onboarding/assessments/{quiz.pk}/retry/').status_code, 200)
                self.assertEqual(AssessmentAttempt.objects.filter(onboarding=record).count(), 1)
            answers = {str(q.pk): q.body['correct_option_ids'] for q in quiz.questions.all()}
            response = client.post(f'/api/quizzes/{quiz.pk}/submit/', {'answers': answers}, format='json')
            self.assertEqual(response.status_code, 201)
            self.assertEqual((response.data['score'], response.data['max_score']), (3, 3))
        for key, _, _, role, _, _, _ in BLUEPRINT:
            if key == 'ready':
                continue
            step = record.steps.get(key=key)
            evidence = {f['name']: True if f['type'] == 'checkbox' else f['options'][0] if f['type'] == 'select'
                        else timezone.localdate().isoformat() if f['type'] == 'date' else 'Local demo evidence'
                        for f in FORMS[key]}
            if key == 'feedback':
                evidence['status'] = 'Needs support'
            client.force_authenticate(users[role])
            response = client.post(f'/api/onboarding/steps/{step.pk}/complete/', {'evidence': evidence}, format='json')
            self.assertEqual(response.status_code, 200, (key, response.data))
        ready = record.steps.get(key='ready')
        evidence = {f['name']: True if f['type'] == 'checkbox' else 'Local demo signoff' for f in FORMS['ready']}
        client.force_authenticate(users['hr'])
        url = f'/api/onboarding/steps/{ready.pk}/complete/'
        self.assertEqual(client.post(url, {'evidence': evidence}, format='json').status_code, 400)
        issue = record.issues.get()
        self.assertEqual(client.post(f'/api/onboarding/issues/{issue.pk}/resolve/', {'resolution': 'Demo support discussion completed'}, format='json').status_code, 200)
        self.assertEqual(client.post(url, {'evidence': evidence}, format='json').status_code, 200)
        self.assertFalse(client.post(url, {'evidence': evidence}, format='json').data['changed'])
        self.assertEqual(record.steps.filter(completed_at__isnull=False).count(), 20)
        self.assertEqual(Event.objects.filter(onboarding=record, action='completed').count(), 20)
        snapshots = (record.steps.values_list('id', 'completed_at', 'evidence'), record.events.count())
        saved = list(snapshots[0])
        call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(list(record.steps.values_list('id', 'completed_at', 'evidence')), saved)
        self.assertEqual(record.events.count(), snapshots[1])
        self.assertTrue(Enrollment.objects.filter(student=users['employee'], course=record.training_course).exists())
