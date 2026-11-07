"""Port of v1 test_demo.py: seed safety and the seeded end-to-end demo."""

from io import StringIO

from django.contrib.auth.hashers import check_password, is_password_usable
from django.core.management import CommandError, call_command
from django.test import override_settings
from rest_framework.test import APIClient

from .blueprint import BLUEPRINT, FORMS, TRAINING_POLICY_VERSION
from .db import get_users
from .testing import MongoTestCase, bearer, events, instances, valid_evidence


class SeedTestCase(MongoTestCase):
    def setUp(self):
        get_users().delete_many({})
        instances().delete_many({})


class SeedSafetyTests(SeedTestCase):
    @override_settings(DEBUG=True)
    def test_walkthrough_employee_is_fresh_and_ready_for_hr_to_launch(self):
        call_command('seed_launchops', walkthrough_employee='walkthrough_hire', stdout=StringIO())
        user = get_users().find_one({'username': 'walkthrough_hire'})
        self.assertEqual(user['role'], 'employee')
        self.assertTrue(check_password('LaunchOpsDemo2026!', user['password']))
        self.assertIsNone(instances().find_one({'employee_id': user['_id']}))
        password = user['password']
        with self.assertRaises(CommandError):
            call_command('seed_launchops', walkthrough_employee='walkthrough_hire', stdout=StringIO())
        self.assertEqual(get_users().find_one({'_id': user['_id']})['password'], password)

    @override_settings(DEBUG=True)
    def test_walkthrough_employee_rejects_invalid_or_reserved_names_before_writes(self):
        for username in ('', 'invalid name', 'a' * 151, 'demo_hr', 'demo_page_001'):
            with self.subTest(username=username), self.assertRaises(CommandError):
                call_command('seed_launchops', walkthrough_employee=username, stdout=StringIO())
            self.assertEqual(get_users().count_documents({}), 0)
            self.assertEqual(instances().count_documents({}), 0)

    @override_settings(DEBUG=True)
    def test_pagination_demo_is_repeatable_and_has_no_login_passwords(self):
        call_command('seed_launchops', pagination_demo=True, stdout=StringIO())
        self.assertEqual(instances().count_documents({}), 106)

        def snapshot():
            return list(instances().find({}, {'_id': 1, 'employee_id': 1}).sort('_id', 1))

        before = snapshot()
        call_command('seed_launchops', pagination_demo=True, stdout=StringIO())
        self.assertEqual(snapshot(), before)
        page_users = list(get_users().find({'username': {'$regex': '^demo_page_'}}))
        self.assertEqual(len(page_users), 105)
        self.assertTrue(all(not is_password_usable(user['password']) for user in page_users))

    @override_settings(DEBUG=False)
    def test_production_seed_refuses_before_any_write(self):
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('seed_launchops', stdout=output)
        self.assertEqual(get_users().count_documents({}), 0)
        self.assertNotIn('LaunchOpsDemo2026!', output.getvalue())

    @override_settings(DEBUG=True)
    def test_collision_creates_no_accounts_or_workflows(self):
        original = get_users().insert_one({'username': 'demo_manager', 'role': 'hr', 'password': 'x'}).inserted_id
        with self.assertRaises(CommandError):
            call_command('seed_launchops', stdout=StringIO())
        self.assertEqual([user['_id'] for user in get_users().find()], [original])
        self.assertEqual(instances().count_documents({}), 0)

    @override_settings(DEBUG=True)
    def test_pagination_collision_creates_nothing(self):
        get_users().insert_one({'username': 'demo_page_050', 'role': 'manager', 'password': 'x'})
        with self.assertRaises(CommandError):
            call_command('seed_launchops', pagination_demo=True, stdout=StringIO())
        self.assertEqual(get_users().count_documents({}), 1)
        self.assertEqual(instances().count_documents({}), 0)


@override_settings(DEBUG=True)
class SeededDemoTests(SeedTestCase):
    def test_seeded_workflow_failure_retry_blocker_and_final_signoff(self):
        call_command('seed_launchops', stdout=StringIO())
        users = {role: get_users().find_one({'username': 'demo_' + role}) for role in ('hr', 'it', 'manager', 'employee')}
        record = instances().find_one({'employee_id': users['employee']['_id']})
        base = f'/api/onboarding/workflows/{record["_id"]}'
        client = APIClient()
        for key, _, _, role, _, _, _ in BLUEPRINT:
            if key == 'ready':
                continue
            evidence = valid_evidence(key)
            if key == 'feedback':
                evidence['status'] = 'Needs support'
            client.credentials(HTTP_AUTHORIZATION=bearer(users[role]))
            if key == 'training':
                self.assertEqual(client.post(f'{base}/steps/{key}/complete/',
                                             {'evidence': {'confirmed': False}}, format='json').status_code, 400)
            response = client.post(f'{base}/steps/{key}/complete/', {'evidence': evidence}, format='json')
            self.assertEqual(response.status_code, 200, (key, response.data))
        evidence = {f['name']: True if f['type'] == 'checkbox' else 'Local demo signoff' for f in FORMS['ready']}
        client.credentials(HTTP_AUTHORIZATION=bearer(users['hr']))
        url = f'{base}/steps/ready/complete/'
        self.assertEqual(client.post(url, {'evidence': evidence}, format='json').status_code, 400)
        [issue] = instances().find_one({'_id': record['_id']})['issues']
        self.assertEqual(issue['step_key'], 'feedback')
        self.assertEqual(client.post(f'{base}/issues/{issue["id"]}/resolve/',
                                     {'resolution': 'Demo support discussion completed'}, format='json').status_code, 200)
        self.assertEqual(client.post(url, {'evidence': evidence}, format='json').status_code, 200)
        replay = client.post(url, {'evidence': evidence}, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.headers.get('Idempotent-Replayed'), 'true')
        doc = instances().find_one({'_id': record['_id']})
        self.assertEqual([step['status'] for step in doc['steps']], ['done'] * 20)
        self.assertEqual(doc['status'], 'ready')
        self.assertEqual(len(events(record['_id'], 'completed')), 20)
        call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(instances().find_one({'_id': record['_id']}), doc)
        training = next(step for step in doc['steps'] if step['key'] == 'training')
        self.assertEqual(training['completion']['proof'], {
            'mode': 'attestation', 'version': TRAINING_POLICY_VERSION,
            'result': 'Acknowledgment recorded; not proof of reading or identity.',
        })
