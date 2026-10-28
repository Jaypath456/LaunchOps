from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from users.models import User
from .models import Onboarding, Event
from .services import create_onboarding


class WorkflowPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.hr = User.objects.create(username='hr', role='hr')
        cls.it = User.objects.create(username='it', role='it')
        cls.manager = User.objects.create(username='manager', role='manager')
        cls.other_manager = User.objects.create(username='other_manager', role='manager')
        cls.employee = User.objects.create(username='old_employee', role='employee')
        cls.old = create_onboarding(employee=cls.employee, manager=cls.manager, creator=cls.hr,
                                    job_title='Old workflow', department='Demo', start_date=timezone.localdate())
        step = cls.old.steps.get(key='profile')
        step.completed_at = timezone.now()
        step.evidence = {'historical': 'preserved'}
        step.save()
        User.objects.bulk_create([User(username=f'page_employee_{i}', role='employee') for i in range(105)])
        employees = list(User.objects.filter(username__startswith='page_employee_').order_by('id'))
        Onboarding.objects.bulk_create([
            Onboarding(employee=user, manager=cls.manager if i < 104 else cls.other_manager, created_by=cls.hr,
                       job_title='Pagination fixture', department='Demo', start_date=timezone.localdate())
            for i, user in enumerate(employees)])
        # Tied timestamps exercise the unique ordering key across every page.
        Onboarding.objects.update(created_at=timezone.now())
        cls.newest = Onboarding.objects.latest('id')

    def setUp(self):
        self.client = APIClient()

    def collect(self, user):
        self.client.force_authenticate(user=user)
        url = '/api/onboarding/workflows/'
        rows = []
        while url:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(response.data['results']), 12)
            rows.extend(response.data['results'])
            url = response.data['next']
        return rows

    def test_old_and_new_workflows_are_accessible_beyond_one_hundred(self):
        expected = list(Onboarding.objects.order_by('-created_at', '-id').values_list('id', flat=True))
        for user in (self.hr, self.it):
            with self.subTest(role=user.role):
                rows = self.collect(user)
                self.assertEqual(len(rows), 106)
                self.assertEqual([row['id'] for row in rows], expected)
                self.assertEqual(rows[0]['id'], self.newest.pk)
                self.assertEqual(rows[-1]['id'], self.old.pk)
                detail = self.client.get(f'/api/onboarding/workflows/{self.old.pk}/')
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.data['steps'][0]['state'], 'completed')
                self.assertEqual(self.old.steps.get(key='profile').evidence, {'historical': 'preserved'})
                self.assertEqual(Event.objects.filter(onboarding=self.old).count(), 1)

    def test_visibility_is_applied_before_pagination(self):
        manager_rows = self.collect(self.manager)
        self.assertEqual(len(manager_rows), 105)
        self.assertNotIn(self.newest.pk, [row['id'] for row in manager_rows])
        self.assertEqual([row['id'] for row in self.collect(self.employee)], [self.old.pk])
        self.client.force_authenticate(user=self.manager)
        self.assertEqual(self.client.get(f'/api/onboarding/workflows/{self.newest.pk}/').status_code, 404)

    def test_page_bounds_links_and_maximum_size(self):
        self.client.force_authenticate(user=self.hr)
        first = self.client.get('/api/onboarding/workflows/?page_size=1000').data
        self.assertEqual(first['count'], 106)
        self.assertEqual(len(first['results']), 100)
        self.assertIsNone(first['previous'])
        second = self.client.get(first['next']).data
        self.assertEqual(len(second['results']), 6)
        self.assertIsNotNone(second['previous'])
        self.assertIsNone(second['next'])
        for page in ('0', '-1', 'bad', '999'):
            self.assertEqual(self.client.get('/api/onboarding/workflows/?page=' + page).status_code, 404)
