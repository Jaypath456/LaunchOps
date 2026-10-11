from datetime import timedelta
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from users.models import User
from courses.models import Course, Enrollment, Chapter
from quizzes.models import Quiz, Question, Submission
from .models import Event, Step, LessonReview, AssessmentAttempt, Issue, Onboarding
from .services import create_onboarding
from .blueprint import BLUEPRINT, FORMS
from .tasks import scan_overdue


class OnboardingFlowTests(TestCase):
    def setUp(self):
        self.users = {role: User.objects.create_user(username=role, password='pass123456', role=role)
                      for role in ('hr', 'it', 'manager', 'employee')}
        self.other = User.objects.create_user(username='other', password='pass123456', role='employee')
        self.editor = User.objects.create_user(username='editor', password='pass123456', role='instructor')
        self.course = Course.objects.create(title='Security', instructor=self.editor, is_published=True)
        self.quiz = Quiz.objects.create(course=self.course, title='Check', is_published=True)
        self.record = create_onboarding(employee=self.users['employee'], manager=self.users['manager'],
            creator=self.users['hr'], job_title='Engineer', department='Engineering',
            start_date=timezone.localdate() + timedelta(days=10), course=self.course)
        self.chapter = Chapter.objects.create(course=self.course, title='Lesson', visibility='public', content=[{'type': 'paragraph', 'children': [{'text': 'Read this lesson'}]}])
        LessonReview.objects.create(onboarding=self.record, chapter=self.chapter)
        self.client = APIClient()

    def as_role(self, role):
        self.client.force_authenticate(user=self.users[role])

    def step(self, key):
        return self.record.steps.get(key=key)

    def evidence(self, key):
        return {field['name']: (True if field['type'] == 'checkbox' else field['options'][0] if field['type'] == 'select'
                else timezone.localdate().isoformat() if field['type'] == 'date' else 'Local test ticket reference')
                for field in FORMS[key]}

    def complete(self, key, evidence=None):
        return self.client.post(f'/api/onboarding/steps/{self.step(key).pk}/complete/',
                                {'evidence': self.evidence(key) if evidence is None else evidence}, format='json')

    def test_visibility_and_permissions(self):
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.get(f'/api/onboarding/workflows/{self.record.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/onboarding/steps/{self.step("profile").pk}/').status_code, 404)
        self.assertEqual(self.complete('profile').status_code, 403)
        self.as_role('employee')
        self.assertEqual(len(self.client.get('/api/onboarding/workflows/').data), 1)
        self.assertEqual(self.client.post('/api/onboarding/workflows/', {}).status_code, 403)
        self.assertEqual(self.complete('profile').status_code, 403)

    def test_dependency_gate_retry_and_training_proof(self):
        self.as_role('it')
        self.assertEqual(self.complete('access').status_code, 400)
        self.as_role('hr')
        self.assertEqual(self.complete('profile', {}).status_code, 400)
        self.assertEqual(self.complete('profile', {**self.evidence('profile'), 'injected': 'yes'}).status_code, 400)
        self.assertEqual(self.complete('profile').status_code, 200)
        self.assertFalse(self.complete('profile').data['changed'])
        self.assertEqual(Event.objects.filter(step=self.step('profile'), action='completed').count(), 1)
        self.as_role('it')
        self.assertEqual(self.complete('access').status_code, 200)
        self.as_role('employee')
        self.assertEqual(self.complete('training').status_code, 400)
        Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=6, max_score=10)
        self.assertEqual(self.complete('training').status_code, 400)
        submission = Submission.objects.get(quiz=self.quiz)
        submission.score = 8
        submission.save()
        self.assertEqual(self.complete('training').status_code, 200)
        self.assertTrue(Enrollment.objects.filter(course=self.course, student=self.users['employee']).exists())
        self.as_role('hr')
        self.assertEqual(self.complete('ready').status_code, 400)
        self.assertEqual(self.record.steps.get(key='training').evidence['confirmed'], True)

    def test_public_registration_cannot_claim_staff_role(self):
        self.client.force_authenticate(user=None)
        response = self.client.post('/api/auth/register/', {'username': 'selfregister', 'password': 'pass123456', 'role': 'hr'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(username='selfregister').role, 'employee')

    def test_failed_assessment_can_be_retried_with_audit_record(self):
        self.as_role('employee')
        Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=0, max_score=10)
        url = f'/api/onboarding/assessments/{self.quiz.pk}/retry/'
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertFalse(Submission.objects.filter(quiz=self.quiz, student=self.users['employee']).exists())
        self.assertEqual(Event.objects.filter(action='assessment_retry').count(), 1)
        self.assertEqual(self.client.post(url).status_code, 409)

    def test_employee_completes_training_through_quiz_api(self):
        question = Question.objects.create(quiz=self.quiz, question_type=Question.SINGLE_CHOICE, points=1,
            body={'prompt': [{'type': 'paragraph', 'children': [{'text': 'Question'}]}],
                  'options': [{'id': 'a', 'text': 'Yes'}, {'id': 'b', 'text': 'No'}], 'correct_option_ids': ['a']})
        self.as_role('hr')
        self.complete('profile')
        self.as_role('it')
        self.complete('access')
        self.as_role('employee')
        take = self.client.get(f'/api/quizzes/{self.quiz.pk}/take/')
        self.assertEqual(take.status_code, 200)
        self.assertNotIn('correct_option_ids', str(take.data))
        result = self.client.post(f'/api/quizzes/{self.quiz.pk}/submit/',
                                  {'answers': {str(question.pk): ['a']}}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertEqual(self.complete('training').status_code, 200)

    def test_overdue_scan_is_idempotent(self):
        Step.objects.filter(pk=self.step('profile').pk).update(due_at=timezone.now() - timedelta(days=1))
        self.assertEqual(scan_overdue(), 1)
        self.assertEqual(scan_overdue(), 0)
        self.assertEqual(Event.objects.filter(action='overdue').count(), 1)

    def test_all_twenty_tasks_and_owner_boundaries(self):
        self.assertEqual(self.record.steps.count(), 20)
        self.assertEqual({role: self.record.steps.filter(owner_role=role).count()
                          for role in ('hr', 'it', 'manager', 'employee')},
                         {'hr': 5, 'it': 5, 'manager': 5, 'employee': 5})
        self.as_role('hr')
        self.complete('profile')
        self.as_role('it')
        self.assertEqual(self.complete('mentor').status_code, 403)
        self.as_role('manager')
        self.assertEqual(self.complete('mentor', {**self.evidence('mentor'), 'extra': 'true'}).status_code, 400)
        self.assertEqual(self.complete('mentor').status_code, 200)
        for key, _, _, role, _, _, _ in BLUEPRINT:
            if key in ('profile', 'mentor'):
                continue
            if key == 'training':
                Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=3, max_score=3)
            self.as_role(role)
            response = self.complete(key)
            self.assertEqual(response.status_code, 200, f'{key}: {response.data}')
        self.assertEqual(self.record.steps.filter(completed_at__isnull=True).count(), 0)
        self.assertEqual(Event.objects.filter(action='completed').count(), 20)

    def test_dates_are_bounded_and_rescheduling_is_hr_only(self):
        self.as_role('hr')
        url = f'/api/onboarding/workflows/{self.record.pk}/'
        self.assertEqual(self.client.patch(url, {'start_date': '1111-01-01'}).status_code, 400)
        self.as_role('employee')
        self.assertEqual(self.client.patch(url, {'start_date': timezone.localdate().isoformat()}).status_code, 403)
        self.as_role('hr')
        new_date = timezone.localdate() + timedelta(days=15)
        previous_due = self.step('profile').due_at
        self.assertEqual(self.client.patch(url, {'start_date': new_date.isoformat()}).status_code, 200)
        self.assertEqual(self.record.steps.get(key='profile').due_at - previous_due, timedelta(days=5))
        self.assertEqual(Event.objects.filter(action='rescheduled').count(), 1)
        self.client.patch(url, {'start_date': new_date.isoformat()})
        self.assertEqual(Event.objects.filter(action='rescheduled').count(), 1)

    def test_signup_and_task_page_cannot_bypass_role_or_missing_fields(self):
        self.as_role('hr')
        response = self.client.post('/api/onboarding/workflows/', {
            'employee_id': self.other.pk, 'manager_id': self.users['manager'].pk,
            'job_title': 'Engineer', 'department': 'Engineering', 'start_date': '1111-01-01',
            'training_course_id': self.course.pk})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.record.steps.filter(completed_at__isnull=False).count(), 0)
        self.assertEqual(self.complete('profile', {'verified': True}).status_code, 400)

    def test_other_manager_and_employee_cannot_complete_or_read_tasks(self):
        manager2 = User.objects.create_user(username='other_manager', password='pass123456', role='manager')
        other_record = create_onboarding(employee=self.other, manager=manager2, creator=self.users['hr'],
            job_title='Analyst', department='Operations', start_date=timezone.localdate(), course=self.course)
        other_step = other_record.steps.get(key='mentor')
        other_employee_step = other_record.steps.get(key='contact')
        self.as_role('manager')
        self.assertEqual(self.client.get(f'/api/onboarding/steps/{other_step.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/onboarding/steps/{other_step.pk}/complete/',
                             {'evidence': self.evidence('mentor')}, format='json').status_code, 403)
        self.as_role('employee')
        self.assertEqual(self.client.get(f'/api/onboarding/steps/{other_employee_step.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/api/onboarding/steps/{other_employee_step.pk}/complete/',
                             {'evidence': self.evidence('contact')}, format='json').status_code, 403)

    def test_rejects_invalid_evidence_without_changing_task(self):
        self.as_role('hr')
        self.assertEqual(self.complete('profile', {'verification': 'Forged', 'verified': True}).status_code, 400)
        self.assertEqual(self.complete('profile', {'verification': 'HR records', 'verified': False}).status_code, 400)
        self.assertEqual(self.complete('profile', {'verification': 'HR records', 'verified': 'true'}).status_code, 400)
        self.assertEqual(self.complete('profile', {'verification': 'HR records', 'verified': True,
                                                   'notes': 'A' * 100000}).status_code, 400)
        self.assertEqual(Event.objects.filter(action='completed').count(), 0)
        self.assertIsNone(self.record.steps.get(key='profile').completed_at)

    def test_training_progress_is_visible_to_its_employee_only(self):
        second_quiz = Quiz.objects.create(course=self.course, title='Another check', is_published=True)
        Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=8, max_score=10)
        url = f'/api/onboarding/steps/{self.step("training").pk}/'
        self.as_role('employee')
        result = self.client.get(url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.data['assessments']), 2)
        self.assertTrue(next(row for row in result.data['assessments'] if row['id'] == self.quiz.pk)['passed'])
        self.assertIsNone(next(row for row in result.data['assessments'] if row['id'] == second_quiz.pk)['score'])
        self.as_role('it')
        self.assertEqual(self.client.get(url).data['assessments'], [])

    def test_workflow_list_prefetches_related_tasks(self):
        self.as_role('hr')
        for number in range(2):
            employee = User.objects.create_user(username=f'new_hire_{number}', password='pass123456', role='employee')
            create_onboarding(employee=employee, manager=self.users['manager'], creator=self.users['hr'],
                              job_title='Engineer', department='Ops', start_date=timezone.localdate(), course=self.course)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/onboarding/workflows/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertLessEqual(len(queries), 15)

    def test_simulation_proof_is_server_owned_and_legacy_evidence_preserved(self):
        self.as_role('hr')
        self.assertEqual(self.complete('profile', {'review': 'Match demo profile', '_proof': {'mode': 'verified'}}).status_code, 400)
        self.assertEqual(self.complete('profile').status_code, 200)
        proof = self.step('profile').evidence['_proof']
        self.assertEqual(proof['mode'], 'simulation')
        self.assertEqual(proof['profile']['employee'], 'employee')
        self.as_role('it')
        self.assertEqual(self.complete('equipment').status_code, 200)
        self.assertEqual(self.complete('endpoint').status_code, 200)
        self.assertEqual(self.step('equipment').evidence['_proof']['asset'], self.step('endpoint').evidence['_proof']['asset'])
        old = {'ticket': 'historical-reference'}
        Step.objects.filter(pk=self.step('access').pk).update(completed_at=timezone.now(), evidence=old)
        self.assertFalse(self.complete('access').data['changed'])
        self.assertEqual(self.step('access').evidence, old)
        self.assertEqual(self.client.get(f'/api/onboarding/steps/{self.step("access").pk}/').data['step']['completion_mode'], 'legacy_unverified')

    def test_lesson_acknowledgments_required_and_idempotent(self):
        self.as_role('hr'); self.complete('profile')
        self.as_role('it'); self.complete('access')
        Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=3, max_score=3)
        self.record.lesson_reviews.all().delete()
        self.as_role('employee')
        self.assertEqual(self.complete('training').status_code, 400)
        url = f'/api/onboarding/lessons/{self.chapter.pk}/review/'
        self.assertEqual(self.client.post(url, {'acknowledged': 'true'}, format='json').status_code, 400)
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.post(url, {'acknowledged': True}, format='json').status_code, 404)
        self.as_role('employee')
        for _ in range(2):
            self.assertEqual(self.client.post(url, {'acknowledged': True}, format='json').status_code, 200)
        self.assertEqual(Event.objects.filter(action='lesson_acknowledged').count(), 1)
        self.assertEqual(self.complete('training').status_code, 200)

    def test_failed_attempt_retained_and_pass_is_exact(self):
        self.as_role('employee')
        submission = Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=139, max_score=200, answers={'1': ['a']})
        result = self.client.get(f'/api/quizzes/{self.quiz.pk}/my-result/').data
        self.assertFalse(result['passed'])  # 69.5% must not be rounded into a pass.
        self.assertEqual(self.client.post(f'/api/onboarding/assessments/{self.quiz.pk}/retry/').status_code, 200)
        archived = AssessmentAttempt.objects.get()
        self.assertEqual(archived.answers, submission.answers)
        self.assertEqual(archived.score, 139)
        Submission.objects.create(quiz=self.quiz, student=self.users['employee'], score=140, max_score=200)
        self.assertEqual(self.client.post(f'/api/onboarding/assessments/{self.quiz.pk}/retry/').status_code, 409)

    def test_forged_quiz_score_and_malformed_answer(self):
        q = Question.objects.create(quiz=self.quiz, question_type=Question.SINGLE_CHOICE, points=1,
            body={'options': [{'id': 'a', 'text': 'Answer'}], 'correct_option_ids': ['a']})
        self.as_role('employee')
        url = f'/api/quizzes/{self.quiz.pk}/submit/'
        self.assertEqual(self.client.post(url, {'answers': {}, 'score': 100}, format='json').status_code, 400)
        response = self.client.post(url, {'answers': {str(q.pk): [{}]}}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['score'], 0)
        self.assertEqual(self.client.post(url, {'answers': {str(q.pk): ['a']}}, format='json').data['score'], 0)
        self.assertEqual(Event.objects.filter(action='assessment_submitted').count(), 1)

    def test_final_signoff_checks_all_tasks_and_open_issues(self):
        # Even legacy/incomplete dependency graphs cannot bypass the all-task gate.
        self.step('ready').prerequisites.clear()
        self.as_role('hr')
        self.assertEqual(self.complete('ready').status_code, 400)
        self.record.steps.exclude(key__in=['ready', 'receipt']).update(completed_at=timezone.now())
        self.as_role('employee')
        self.assertEqual(self.complete('receipt', {'condition': 'Needs support'}).status_code, 200)
        issue = Issue.objects.get()
        self.as_role('hr')
        self.assertEqual(self.complete('ready').status_code, 400)
        self.as_role('it')
        url = f'/api/onboarding/issues/{issue.pk}/resolve/'
        self.assertEqual(self.client.post(url, {'resolution': 'Replaced demo device'}).status_code, 403)
        self.as_role('hr')
        self.assertEqual(self.client.post(url, {'resolution': 'ok'}).status_code, 400)
        self.assertTrue(self.client.post(url, {'resolution': 'Replaced demo device'}).data['changed'])
        self.assertFalse(self.client.post(url, {'resolution': 'Replaced demo device'}).data['changed'])
        self.assertEqual(self.complete('ready').status_code, 200)
        self.assertEqual(len(self.step('ready').evidence['_proof']['reviewed_tasks']), 19)

    def test_year_1111_requires_explicit_hr_correction(self):
        from datetime import date
        Onboarding.objects.filter(pk=self.record.pk).update(start_date=date(1111, 11, 11))
        self.as_role('hr')
        self.assertTrue(self.client.get(f'/api/onboarding/workflows/{self.record.pk}/').data['date_needs_correction'])
        self.assertEqual(self.complete('profile').status_code, 400)
        self.client.patch(f'/api/onboarding/workflows/{self.record.pk}/', {'start_date': timezone.localdate().isoformat()})
        self.assertEqual(self.complete('profile').status_code, 200)

    def test_seed_is_repeatable_and_preserves_existing_records(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_launchops', stdout=StringIO())
        employee = User.objects.get(username='demo_employee')
        record = Onboarding.objects.get(employee=employee)
        profile = record.steps.get(key='profile')
        profile.evidence = {'old': 'preserve me'}
        profile.completed_at = timezone.now()
        profile.save()
        snapshots = (User.objects.count(), Course.objects.count(), Chapter.objects.count(), Quiz.objects.count(), Question.objects.count(), Onboarding.objects.count(), Step.objects.count(), Event.objects.count(), employee.password)
        call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(snapshots, (User.objects.count(), Course.objects.count(), Chapter.objects.count(), Quiz.objects.count(), Question.objects.count(), Onboarding.objects.count(), Step.objects.count(), Event.objects.count(), User.objects.get(pk=employee.pk).password))
        profile.refresh_from_db()
        self.assertEqual(profile.evidence, {'old': 'preserve me'})
        self.assertIsNotNone(profile.completed_at)

    def test_task_detail_query_budget(self):
        self.as_role('employee')
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f'/api/onboarding/steps/{self.step("training").pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 12)

    def test_seed_incident_answers_are_safe_and_existing_publication_is_preserved(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_launchops', stdout=StringIO())
        quiz = Quiz.objects.get(title='Incident response check')
        for q in quiz.questions.all():
            correct = [o['text'] for o in q.body['options'] if o['id'] in q.body['correct_option_ids']]
            self.assertNotIn('Your password', correct)
            self.assertNotIn('A public social post', correct)
        quiz.is_published = False
        quiz.save()
        call_command('seed_launchops', stdout=StringIO())
        quiz.refresh_from_db()
        self.assertFalse(quiz.is_published)

    def test_training_track_repair_is_hr_only_and_preserves_completed_training(self):
        self.record.training_course = None
        self.record.save()
        url = f'/api/onboarding/workflows/{self.record.pk}/'
        self.as_role('employee')
        self.assertEqual(self.client.patch(url, {'training_course_id': self.course.pk}).status_code, 403)
        self.as_role('hr')
        self.assertEqual(self.client.patch(url, {'training_course_id': self.course.pk}).status_code, 200)
        self.assertEqual(Event.objects.filter(action='track_assigned').count(), 1)
        self.client.patch(url, {'training_course_id': self.course.pk})
        self.assertEqual(Event.objects.filter(action='track_assigned').count(), 1)
        second = Course.objects.create(title='Another track', instructor=self.editor, is_published=True)
        self.assertEqual(self.client.patch(url, {'training_course_id': second.pk}).status_code, 400)
        self.record.steps.filter(key='training').update(completed_at=timezone.now())
        self.assertEqual(self.client.patch(url, {'training_course_id': second.pk}).status_code, 400)

    def test_attendance_cannot_be_future_dated(self):
        self.record.steps.filter(key__in=['equipment', 'mentor']).update(completed_at=timezone.now())
        self.as_role('manager')
        data = self.evidence('welcome')
        data['session_date'] = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.complete('welcome', data).status_code, 400)
