"""Port of v1 test_pagination.py."""

from django.utils import timezone
from rest_framework.test import APIClient

from . import repository
from .testing import MongoTestCase, actor_of, bearer, instances, make_user, mark_done, stored_step


class WorkflowPaginationTests(MongoTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hr = make_user('hr', 'hr')
        cls.it = make_user('it', 'it')
        cls.manager = make_user('manager', 'manager')
        cls.other_manager = make_user('other_manager', 'manager')
        cls.employee = make_user('old_employee', 'employee')
        hr = actor_of(cls.hr)
        cls.old = repository.create_workflow(actor=hr, employee_id=cls.employee['_id'], manager_id=cls.manager['_id'],
                                             job_title='Old workflow', department='Demo',
                                             start_date=timezone.localdate())
        mark_done(cls.old['_id'], ['profile'], evidence={'historical': 'preserved'})
        for i in range(105):
            employee = make_user(f'page_employee_{i}', 'employee')
            cls.newest = repository.create_workflow(
                actor=hr, employee_id=employee['_id'],
                manager_id=cls.manager['_id'] if i < 104 else cls.other_manager['_id'],
                job_title='Pagination fixture', department='Demo', start_date=timezone.localdate())
        # Tied timestamps: ordering must not depend on created_at.
        instances().update_many({}, {'$set': {'created_at': repository.now_ms()}})

    def setUp(self):
        self.client = APIClient()

    def collect(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=bearer(user))
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
        expected = [str(doc['_id']) for doc in instances().find({}, {'_id': 1}).sort('_id', -1)]
        for user in (self.hr, self.it):
            with self.subTest(role=user['role']):
                rows = self.collect(user)
                self.assertEqual(len(rows), 106)
                self.assertEqual([row['id'] for row in rows], expected)
                self.assertEqual(rows[0]['id'], str(self.newest['_id']))
                self.assertEqual(rows[-1]['id'], str(self.old['_id']))
                detail = self.client.get(f'/api/onboarding/workflows/{self.old["_id"]}/')
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.data['steps'][0]['state'], 'done')
                self.assertEqual(stored_step(self.old['_id'], 'profile')['completion']['evidence'],
                                 {'historical': 'preserved'})
                # v1 had one `created` event here; v2 instances start with no events.
                self.assertEqual(detail.data['events'], [])

    def test_visibility_is_applied_before_pagination(self):
        manager_rows = self.collect(self.manager)
        self.assertEqual(len(manager_rows), 105)
        self.assertNotIn(str(self.newest['_id']), [row['id'] for row in manager_rows])
        self.assertEqual([row['id'] for row in self.collect(self.employee)], [str(self.old['_id'])])
        self.assertEqual([row['id'] for row in self.collect(self.other_manager)], [str(self.newest['_id'])])
        self.client.credentials(HTTP_AUTHORIZATION=bearer(self.manager))
        self.assertEqual(self.client.get(f'/api/onboarding/workflows/{self.newest["_id"]}/').status_code, 404)

    def test_page_bounds_links_and_maximum_size(self):
        self.client.credentials(HTTP_AUTHORIZATION=bearer(self.hr))
        first = self.client.get('/api/onboarding/workflows/?page_size=1000').data
        self.assertEqual(first['count'], 106)
        self.assertEqual(first['total_pages'], 2)
        self.assertEqual(len(first['results']), 100)
        self.assertIsNone(first['previous'])
        second = self.client.get(first['next']).data
        self.assertEqual(len(second['results']), 6)
        self.assertIsNotNone(second['previous'])
        self.assertIsNone(second['next'])
        self.assertEqual({row['id'] for row in first['results']} & {row['id'] for row in second['results']}, set())
        for page in ('0', '-1', 'bad', '999'):
            self.assertEqual(self.client.get('/api/onboarding/workflows/?page=' + page).status_code, 404)
