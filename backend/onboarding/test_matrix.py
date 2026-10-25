from collections import Counter, deque
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from courses.models import Chapter, Course
from quizzes.models import Quiz, Submission
from users.models import User
from .blueprint import BLUEPRINT, FORMS
from .models import Event, LessonReview, Step
from .services import create_onboarding


class BlueprintIntegrityTests(SimpleTestCase):
    def test_blueprint_integrity(self):
        roles = {'hr', 'it', 'manager', 'employee'}
        keys = [row[0] for row in BLUEPRINT]
        self.assertEqual(len(BLUEPRINT), 20)
        self.assertEqual(len(set(keys)), len(keys))
        self.assertEqual(Counter(row[3] for row in BLUEPRINT),
                         {role: 5 for role in roles})
        indegree = {}
        dependents = {key: [] for key in keys}
        for key, _, _, role, _, _, dependencies in BLUEPRINT:
            with self.subTest(key=key):
                self.assertIn(role, roles)
                self.assertIn(key, FORMS)
                self.assertNotIn(key, dependencies)
                self.assertTrue(set(dependencies).issubset(keys))
            indegree[key] = len(dependencies)
            for dependency in dependencies:
                dependents[dependency].append(key)
        queue = deque(key for key in keys if indegree[key] == 0)
        visited = []
        while queue:
            key = queue.popleft()
            visited.append(key)
            for dependent in dependents[key]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)
        self.assertEqual(set(visited), set(keys), 'Dependency graph has a cycle')


class MatrixHelpers:
    def setUp(self):
        self.users = {role: User.objects.create_user(
            username=role, password='pass123456', role=role)
            for role in ('hr', 'it', 'manager', 'employee')}
        self.other = User.objects.create_user(
            username='other', password='pass123456', role='employee')
        self.editor = User.objects.create_user(
            username='editor', password='pass123456', role='instructor')
        self.course = Course.objects.create(
            title='Security', instructor=self.editor, is_published=True)
        self.quiz = Quiz.objects.create(
            course=self.course, title='Check', is_published=True)
        self.record = create_onboarding(
            employee=self.users['employee'], manager=self.users['manager'],
            creator=self.users['hr'], job_title='Engineer', department='Engineering',
            start_date=timezone.localdate() + timedelta(days=10), course=self.course)
        self.chapter = Chapter.objects.create(
            course=self.course, title='Lesson', visibility='public',
            content=[{'type': 'paragraph', 'children': [{'text': 'Read this lesson'}]}])
        LessonReview.objects.create(onboarding=self.record, chapter=self.chapter)
        Submission.objects.create(student=self.users['employee'], quiz=self.quiz,
                                  score=1, max_score=1)
        self.client = APIClient()

    def as_role(self, role):
        self.client.force_authenticate(user=self.users[role])

    def step(self, key):
        return self.record.steps.get(key=key)

    def evidence(self, key):
        return {field['name']: (
            True if field['type'] == 'checkbox'
            else field['options'][0] if field['type'] == 'select'
            else timezone.localdate().isoformat() if field['type'] == 'date'
            else 'Local test ticket reference') for field in FORMS[key]}

    def complete(self, key, evidence=None):
        return self.client.post(
            f'/api/onboarding/steps/{self.step(key).pk}/complete/',
            {'evidence': self.evidence(key) if evidence is None else evidence},
            format='json')

    def reset_steps(self):
        Step.objects.filter(onboarding=self.record).update(
            completed_at=None, completed_by=None, evidence={})
        Event.objects.filter(onboarding=self.record, action='completed').delete()

    def complete_others(self, key):
        self.reset_steps()
        Step.objects.filter(onboarding=self.record).exclude(key=key).update(
            completed_at=timezone.now())

    def assert_unchanged(self, key):
        step = self.step(key)
        self.assertIsNone(step.completed_at)
        self.assertIsNone(step.completed_by_id)
        self.assertEqual(step.evidence, {})
        self.assertFalse(Event.objects.filter(step=step, action='completed').exists())


class OwnerMatrixTests(MatrixHelpers, TestCase):
    def test_owner_matrix(self):
        for key, _, _, owner, _, _, _ in BLUEPRINT:
            self.complete_others(key)
            non_owners = [user for role, user in self.users.items() if role != owner]
            for user in non_owners + [self.other, self.editor]:
                with self.subTest(key=key, actor=user.username):
                    self.client.force_authenticate(user=user)
                    response = self.complete(key)
                    # The action/service raises PermissionDenied, including invisible users.
                    self.assertEqual(response.status_code, 403, response.data)
                    self.assert_unchanged(key)
            with self.subTest(key=key, actor=owner):
                self.as_role(owner)
                self.assertEqual(self.complete(key).status_code, 200)
                self.assertIsNotNone(self.step(key).completed_at)
                self.assertTrue(Event.objects.filter(
                    step=self.step(key), action='completed').exists())


class PrerequisiteMatrixTests(MatrixHelpers, TestCase):
    def test_prerequisite_matrix(self):
        for key, _, _, owner, _, _, dependencies in BLUEPRINT:
            if not dependencies:
                continue
            with self.subTest(key=key):
                self.reset_steps()
                self.as_role(owner)
                response = self.complete(key)
                # Incomplete prerequisites raise ValidationError, which maps to 400.
                self.assertEqual(response.status_code, 400, response.data)
                self.assert_unchanged(key)
                Step.objects.filter(onboarding=self.record, key__in=dependencies).update(
                    completed_at=timezone.now())
                if key == 'ready':
                    # Final signoff also needs every other task, not just its listed prerequisites.
                    response = self.complete(key)
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assertIsNone(self.step(key).completed_at)
                    Step.objects.filter(onboarding=self.record).exclude(key=key).update(
                        completed_at=timezone.now())
                response = self.complete(key)
                # Success after the listed prerequisites; ready also needs all other tasks.
                self.assertEqual(response.status_code, 200, response.data)
                self.assertIsNotNone(self.step(key).completed_at)


class EvidenceMatrixTests(MatrixHelpers, TestCase):
    def test_evidence_matrix(self):
        for key, _, _, owner, _, _, _ in BLUEPRINT:
            self.complete_others(key)
            self.as_role(owner)
            valid = self.evidence(key)
            cases = [('missing_' + field['name'], {
                name: value for name, value in valid.items() if name != field['name']})
                for field in FORMS[key] if field['required']]
            cases.extend([('unknown_field', {**valid, 'unknown_field': 'extra'}),
                          ('empty', {})])
            for case, evidence in cases:
                with self.subTest(key=key, case=case):
                    response = self.complete(key, evidence)
                    # validate_evidence requires the exact field set; violations return 400.
                    self.assertEqual(response.status_code, 400, response.data)
                    self.assert_unchanged(key)
