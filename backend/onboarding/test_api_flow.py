"""The whole 20-step workflow through the HTTP API, signed in as the owning roles."""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from .db import get_users
from .graph import validate_blueprint
from .testing import MongoTestCase, instances, make_user, valid_evidence


PASSWORD = 'flow test password'


class TwentyStepApiFlowTests(MongoTestCase):
    def setUp(self):
        get_users().delete_many({})
        instances().delete_many({})
        self.people = {role: make_user(f'flow_{role}', role, password=PASSWORD)
                       for role in ('hr', 'it', 'manager', 'employee')}
        self.clients = {}
        for role, user in self.people.items():
            client = APIClient()
            login = client.post('/api/auth/login/', {'username': user['username'], 'password': PASSWORD, 'role': role},
                                format='json')
            self.assertEqual(login.status_code, 200, login.data)
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {login.data["access"]}')
            self.clients[role] = client

    def test_hr_launches_and_every_owner_completes_all_twenty_steps(self):
        created = self.clients['hr'].post('/api/onboarding/workflows/', {
            'employee_id': str(self.people['employee']['_id']), 'manager_id': str(self.people['manager']['_id']),
            'job_title': 'Engineer', 'department': 'Engineering',
            'start_date': (timezone.localdate() + timedelta(days=7)).isoformat(),
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual((created.data['employee'], created.data['manager'], created.data['status']),
                         ('flow_employee', 'flow_manager', 'active'))
        base = f'/api/onboarding/workflows/{created.data["id"]}'
        owners = {step['key']: step['owner_role'] for step in created.data['steps']}
        order = validate_blueprint()

        for index, key in enumerate(order):
            role = owners[key]
            for other_role in {'hr', 'it', 'manager', 'employee'} - {role}:
                denied = self.clients[other_role].post(f'{base}/steps/{key}/complete/',
                                                       {'evidence': valid_evidence(key)}, format='json')
                self.assertEqual(denied.status_code, 403, (key, other_role))
            detail = self.clients[role].get(f'{base}/steps/{key}/')
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.data['step']['state'], 'ready', key)
            response = self.clients[role].post(f'{base}/steps/{key}/complete/', {'evidence': valid_evidence(key)},
                                               format='json', HTTP_IDEMPOTENCY_KEY=f'flow-{key}')
            self.assertEqual(response.status_code, 200, (key, response.data))
            self.assertIsNone(response.headers.get('Idempotent-Replayed'))
            self.assertEqual(response.data['completion']['request_id'], f'flow-{key}')
            self.assertEqual(response.data['completion']['by'], str(self.people[role]['_id']))

        workflow = self.clients['employee'].get(f'{base}/')
        self.assertEqual(workflow.status_code, 200)
        self.assertEqual(workflow.data['status'], 'ready')
        self.assertEqual([step['state'] for step in workflow.data['steps']], ['done'] * 20)
        self.assertEqual({step['completed_by'] for step in workflow.data['steps']},
                         {f'flow_{role}' for role in ('hr', 'it', 'manager', 'employee')})
        timeline = self.clients['manager'].get(f'{base}/timeline/').data['events']
        self.assertEqual([(event['action'], event['step']) for event in timeline], [('completed', key) for key in order])
        self.assertEqual([event['actor'] for event in timeline], [f'flow_{owners[key]}' for key in order])
        doc = instances().find_one({})
        self.assertEqual(doc['version'], 21)
        self.assertEqual(doc['issues'], [])
        self.assertEqual([job['status'] for job in doc['provisioning']], ['pending'] * 3)
        ready = self.clients['hr'].get(f'{base}/steps/ready/').data
        self.assertEqual(sorted(ready['step']['evidence']['_proof']['reviewed_tasks']), sorted(set(order) - {'ready'}))
