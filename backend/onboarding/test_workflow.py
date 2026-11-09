"""Port of v1 onboarding/tests.py onto MongoDB and the v2 API."""

from contextlib import contextmanager
from datetime import UTC, timedelta
from io import StringIO
from unittest import mock

from bson import ObjectId
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from jsonschema import Draft7Validator, FormatChecker
from pymongo import MongoClient, monitoring
from pymongo.errors import DuplicateKeyError

from . import db, domain, repository
from .blueprint import BLUEPRINT, CONNECTORS, TRAINING_POLICY_VERSION
from .indexes import load_provisioning_schema
from .testing import (
    WorkflowTestCase, actor_of, events, instances, make_user, mark_done, stored_step, succeed_jobs,
)


class _CommandCounter(monitoring.CommandListener):
    def __init__(self):
        self.commands = []
        self.active = True

    def started(self, event):
        if self.active:
            self.commands.append(event.command_name)

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


@contextmanager
def count_mongo_commands():
    """Replaces v1's CaptureQueriesContext: counts MongoDB commands issued by the request."""
    counter = _CommandCounter()
    client = MongoClient(settings.MONGO_URI, tz_aware=True, tzinfo=UTC, event_listeners=[counter])
    try:
        with mock.patch.object(db, '_client', client):
            yield counter.commands
    finally:
        counter.active = False
        client.close()


def jsonable_job(job):
    result = dict(job)
    for key in ('lease_until', 'next_attempt_at', 'updated_at'):
        if result[key] is not None:
            result[key] = result[key].isoformat().replace('+00:00', 'Z')
    return result


class OnboardingFlowTests(WorkflowTestCase):
    def test_visibility_and_permissions(self):
        self.as_user(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.get(self.url('steps/profile/')).status_code, 404)
        self.assertEqual(self.complete('profile').status_code, 403)
        self.as_role('employee')
        self.assertEqual(self.client.get('/api/onboarding/workflows/').data['count'], 1)
        self.assertEqual(self.client.post('/api/onboarding/workflows/', {}).status_code, 403)
        self.assertEqual(self.complete('profile').status_code, 403)

    def test_dependency_gate_retry_and_training_proof(self):
        self.as_role('it')
        self.assertEqual(self.complete('access').status_code, 400)
        self.as_role('hr')
        self.assertEqual(self.complete('profile', {}).status_code, 400)
        self.assertEqual(self.complete('profile', {**self.evidence('profile'), 'injected': 'yes'}).status_code, 400)
        first = self.complete('profile')
        self.assertEqual(first.status_code, 200)
        self.assertIsNone(first.headers.get('Idempotent-Replayed'))
        replay = self.complete('profile')
        self.assert_replayed(replay)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        self.as_role('it')
        succeed_jobs(self.wid)
        self.assertEqual(self.complete('access').status_code, 200)
        self.as_role('employee')
        self.assertEqual(self.complete('training', {'confirmed': False}).status_code, 400)
        self.assertEqual(self.complete('training').status_code, 200)
        self.assertEqual(self.step('training')['completion']['proof'], {
            'mode': 'attestation', 'version': TRAINING_POLICY_VERSION,
            'result': 'Acknowledgment recorded; not proof of reading or identity.',
        })
        self.assert_replayed(self.complete('training'))
        self.assertEqual(len(events(self.wid, 'completed', 'training')), 1)
        self.as_role('hr')
        self.assertEqual(self.complete('ready').status_code, 400)
        self.assertEqual(self.step('training')['completion']['evidence']['confirmed'], True)

    def test_training_attestation_requires_access_and_its_employee(self):
        self.as_role('employee')
        self.assertEqual(self.client.get(self.url('steps/training/')).data['step']['prerequisites'], ['access'])
        self.assertEqual(self.complete('training').status_code, 400)
        mark_done(self.wid, ['access'])
        for role in ('hr', 'it', 'manager'):
            self.as_role(role)
            self.assertEqual(self.complete('training').status_code, 403)
        self.as_user(self.other)
        self.assertEqual(self.complete('training').status_code, 403)
        self.as_role('employee')
        self.assertEqual(self.complete('training').status_code, 200)

    def test_overdue_is_computed_on_read(self):
        # v1 used a Celery scan that wrote one `overdue` event; v2 computes overdue on every read.
        past = timezone.now() - timedelta(days=1)
        instances().update_one({'_id': self.wid}, {'$set': {'steps.$[s].due_at': past}},
                               array_filters=[{'s.key': {'$in': ['profile', 'contract']}}])
        self.as_role('hr')
        before = self.doc()['events']
        for _ in range(2):
            steps = {step['key']: step for step in self.client.get(self.url()).data['steps']}
            self.assertTrue(steps['profile']['overdue'])
            self.assertFalse(steps['contract']['overdue'])  # blocked by profile
            self.assertFalse(steps['payroll']['overdue'])  # not yet due
        self.assertEqual(self.doc()['events'], before)
        self.assertEqual(self.complete('profile').status_code, 200)
        steps = {step['key']: step for step in self.client.get(self.url()).data['steps']}
        self.assertFalse(steps['profile']['overdue'])  # done
        self.assertTrue(steps['contract']['overdue'])  # now ready and past due

    def test_all_twenty_tasks_and_owner_boundaries(self):
        self.as_role('hr')
        steps = self.client.get(self.url()).data['steps']
        self.assertEqual(len(steps), 20)
        self.assertEqual({role: sum(step['owner_role'] == role for step in steps)
                          for role in ('hr', 'it', 'manager', 'employee')},
                         {'hr': 5, 'it': 5, 'manager': 5, 'employee': 5})
        self.complete('profile')
        self.as_role('it')
        self.assertEqual(self.complete('mentor').status_code, 403)
        self.as_role('manager')
        self.assertEqual(self.complete('mentor', {**self.evidence('mentor'), 'extra': 'true'}).status_code, 400)
        self.assertEqual(self.complete('mentor').status_code, 200)
        succeed_jobs(self.wid)
        for key, _, _, role, _, _, _ in BLUEPRINT:
            if key in ('profile', 'mentor'):
                continue
            self.as_role(role)
            response = self.complete(key)
            self.assertEqual(response.status_code, 200, f'{key}: {response.data}')
        self.assertEqual([step['status'] for step in self.doc()['steps']], ['done'] * 20)
        self.assertEqual(len(events(self.wid, 'completed')), 20)
        self.assertEqual(self.doc()['status'], 'ready')

    def test_dates_are_bounded_and_rescheduling_is_hr_only(self):
        self.as_role('hr')
        url = self.url()
        self.assertEqual(self.client.patch(url, {'start_date': '1111-01-01'}).status_code, 400)
        self.as_role('employee')
        self.assertEqual(self.client.patch(url, {'start_date': timezone.localdate().isoformat()}).status_code, 403)
        self.as_role('hr')
        new_date = timezone.localdate() + timedelta(days=15)
        previous_due = self.step('profile')['due_at']
        response = self.client.patch(url, {'start_date': new_date.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['start_date'], new_date.isoformat())
        self.assertEqual(self.step('profile')['due_at'] - previous_due, timedelta(days=5))
        self.assertEqual(len(events(self.wid, 'rescheduled')), 1)
        self.client.patch(url, {'start_date': new_date.isoformat()})
        self.assertEqual(len(events(self.wid, 'rescheduled')), 1)

    def test_signup_and_task_page_cannot_bypass_role_or_missing_fields(self):
        self.as_role('hr')
        response = self.client.post('/api/onboarding/workflows/', {
            'employee_id': str(self.other['_id']), 'manager_id': str(self.users['manager']['_id']),
            'job_title': 'Engineer', 'department': 'Engineering', 'start_date': '1111-01-01'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(instances().count_documents({}), 1)
        self.assertEqual(sum(step['status'] == 'done' for step in self.doc()['steps']), 0)
        self.assertEqual(self.complete('profile', {'verified': True}).status_code, 400)

    def test_other_manager_and_employee_cannot_complete_or_read_tasks(self):
        manager2 = make_user('other_manager', 'manager')
        other_record = repository.create_workflow(
            actor=actor_of(self.users['hr']), employee_id=self.other['_id'], manager_id=manager2['_id'],
            job_title='Analyst', department='Operations', start_date=timezone.localdate())
        other_id = other_record['_id']
        self.as_role('manager')
        self.assertEqual(self.client.get(self.url('steps/mentor/', other_id)).status_code, 404)
        self.assertEqual(self.complete('mentor', workflow_id=other_id).status_code, 403)
        self.as_role('employee')
        self.assertEqual(self.client.get(self.url('steps/contact/', other_id)).status_code, 404)
        self.assertEqual(self.complete('contact', workflow_id=other_id).status_code, 403)

    def test_rejects_invalid_evidence_without_changing_task(self):
        self.as_role('hr')
        self.assertEqual(self.complete('profile', {'verification': 'Forged', 'verified': True}).status_code, 400)
        self.assertEqual(self.complete('profile', {'verification': 'HR records', 'verified': False}).status_code, 400)
        self.assertEqual(self.complete('profile', {'verification': 'HR records', 'verified': 'true'}).status_code, 400)
        self.assertEqual(self.complete('profile', {'verification': 'HR records', 'verified': True,
                                                   'notes': 'A' * 100000}).status_code, 400)
        self.assertEqual(self.complete('profile', {'review': 'A' * 241}).status_code, 400)
        self.assertEqual(events(self.wid, 'completed'), [])
        self.assertIsNone(self.step('profile')['completion'])
        self.assertEqual(self.doc()['version'], 1)

    def test_training_detail_exposes_attestation_form_and_mode(self):
        self.as_role('employee')
        result = self.client.get(self.url('steps/training/'))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['step']['completion_mode'], 'attestation')
        self.assertEqual(result.data['step']['form_fields'], [{
            'name': 'confirmed', 'label': 'I read and acknowledge the security policy',
            'type': 'checkbox', 'required': True,
        }])
        self.assertEqual({item['key'] for item in result.data['dependencies']}, {'access'})

    def test_workflow_list_batches_user_lookups(self):
        self.as_role('hr')
        for number in range(2):
            employee = make_user(f'new_hire_{number}', 'employee')
            repository.create_workflow(actor=actor_of(self.users['hr']), employee_id=employee['_id'],
                                       manager_id=self.users['manager']['_id'], job_title='Engineer',
                                       department='Ops', start_date=timezone.localdate())
        mark_done(self.wid, ['profile'], by=self.users['hr']['_id'])
        with count_mongo_commands() as commands:
            response = self.client.get('/api/onboarding/workflows/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 3)
        self.assertEqual({row['employee'] for row in response.data['results']}, {'employee', 'new_hire_0', 'new_hire_1'})
        # count + one page find + one $in users lookup; no per-document queries.
        self.assertEqual(commands, ['aggregate', 'find', 'find'])

    def test_simulation_proof_is_server_owned_and_legacy_evidence_preserved(self):
        self.as_role('hr')
        self.assertEqual(self.complete('profile', {'review': 'Match demo profile', '_proof': {'mode': 'verified'}}).status_code, 400)
        self.assertEqual(self.complete('profile').status_code, 200)
        proof = self.step('profile')['completion']['proof']
        self.assertEqual(proof['mode'], 'simulation')
        self.assertEqual(proof['profile']['employee'], 'employee')
        self.as_role('it')
        self.assertEqual(self.complete('equipment').status_code, 200)
        self.assertEqual(self.complete('endpoint').status_code, 200)
        self.assertEqual(self.step('equipment')['completion']['proof']['asset'],
                         self.step('endpoint')['completion']['proof']['asset'])
        old = {'ticket': 'historical-reference'}
        mark_done(self.wid, ['access'], evidence=old, proof=None)
        self.assert_replayed(self.complete('access'))
        self.assertEqual(self.step('access')['completion']['evidence'], old)
        detail = self.client.get(self.url('steps/access/')).data
        self.assertEqual(detail['step']['completion_mode'], 'legacy_unverified')
        self.assertEqual(detail['step']['evidence'], old)

    def test_attestation_rejects_invalid_or_forged_evidence_without_side_effects(self):
        mark_done(self.wid, ['access'])
        self.as_role('employee')
        for evidence in (None, {}, {'confirmed': False}, {'confirmed': 'true'}, {'confirmed': 1},
                         {'confirmed': True, '_proof': {'mode': 'attestation', 'version': 'forged'}},
                         {'confirmed': True, 'version': 'forged'}):
            with self.subTest(evidence=evidence):
                response = self.client.post(self.url('steps/training/complete/'), {'evidence': evidence}, format='json')
                self.assertEqual(response.status_code, 400)
                self.assert_unchanged('training')

    def test_final_signoff_checks_all_tasks_and_open_issues(self):
        # Even an incomplete dependency graph cannot bypass the all-task gate.
        with mock.patch.dict(domain.PREREQUISITES, {'ready': []}):
            self.as_role('hr')
            self.assertEqual(self.complete('ready').status_code, 400)
        mark_done(self.wid, [key for key in domain.STEP_ORDER if key not in ('ready', 'receipt')])
        self.as_role('employee')
        self.assertEqual(self.complete('receipt', {'condition': 'Needs support'}).status_code, 200)
        [issue] = self.doc()['issues']
        self.assertEqual(issue['detail'], 'Simulated device needs support')
        self.as_role('hr')
        self.assertEqual(self.complete('ready').status_code, 400)
        self.as_role('it')
        url = self.url(f'issues/{issue["id"]}/resolve/')
        self.assertEqual(self.client.post(url, {'resolution': 'Replaced demo device'}).status_code, 403)
        self.as_role('hr')
        self.assertEqual(self.client.post(url, {'resolution': 'ok'}).status_code, 400)
        self.assertTrue(self.client.post(url, {'resolution': 'Replaced demo device'}).data['changed'])
        self.assertFalse(self.client.post(url, {'resolution': 'Replaced demo device'}).data['changed'])
        self.assertEqual(len(events(self.wid, 'issue_resolved')), 1)
        self.assertEqual(self.client.post(self.url(f'issues/{ObjectId()}/resolve/'),
                                          {'resolution': 'Replaced demo device'}).status_code, 404)
        self.assertEqual(self.complete('ready').status_code, 200)
        self.assertEqual(len(self.step('ready')['completion']['proof']['reviewed_tasks']), 19)
        self.assertEqual(self.doc()['status'], 'ready')

    def test_year_1111_requires_explicit_hr_correction(self):
        instances().update_one({'_id': self.wid}, {'$set': {'start_date': '1111-11-11'}})
        self.as_role('hr')
        self.assertTrue(self.client.get(self.url()).data['date_needs_correction'])
        self.assertEqual(self.complete('profile').status_code, 400)
        self.client.patch(self.url(), {'start_date': timezone.localdate().isoformat()})
        self.assertEqual(self.complete('profile').status_code, 200)

    @override_settings(DEBUG=True)
    def test_seed_is_repeatable_and_preserves_existing_records(self):
        call_command('seed_launchops', stdout=StringIO())
        users = db.get_users()
        employee = users.find_one({'username': 'demo_employee'})
        record = instances().find_one({'employee_id': employee['_id']})
        mark_done(record['_id'], ['profile'], evidence={'old': 'preserve me'})

        def snapshot():
            return (users.count_documents({}), instances().count_documents({}),
                    instances().find_one({'_id': record['_id']}), users.find_one({'_id': employee['_id']})['password'])

        before = snapshot()
        call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(snapshot(), before)
        profile = stored_step(record['_id'], 'profile')
        self.assertEqual(profile['completion']['evidence'], {'old': 'preserve me'})
        self.assertEqual(profile['status'], 'done')

    def test_task_detail_query_budget(self):
        self.as_role('employee')
        with count_mongo_commands() as commands:
            response = self.client.get(self.url('steps/training/'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(commands, ['find', 'find'])  # the workflow, then one $in users lookup

    def test_workflow_creation_and_directory_need_only_onboarding_data(self):
        self.as_role('hr')
        directory = self.client.get('/api/onboarding/directory/')
        self.assertEqual(directory.status_code, 200)
        self.assertEqual(set(directory.data), {'users'})
        self.assertEqual({row['role'] for row in directory.data['users']}, {'employee', 'manager'})
        self.assertEqual([row['username'] for row in directory.data['users']],
                         sorted(row['username'] for row in directory.data['users']))
        payload = {'employee_id': str(self.other['_id']), 'manager_id': str(self.users['manager']['_id']),
                   'job_title': 'Engineer', 'department': 'Engineering',
                   'start_date': timezone.localdate().isoformat()}
        response = self.client.post('/api/onboarding/workflows/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        record = instances().find_one({'_id': ObjectId(response.data['id'])})
        self.assertEqual(len(record['steps']), len(BLUEPRINT))
        self.assertEqual(stored_step(record['_id'], 'training')['completion'], None)
        self.assertEqual(self.client.get(self.url('steps/training/', record['_id'])).data['step']['evidence'], {})
        duplicate = self.client.post('/api/onboarding/workflows/', payload, format='json')
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.data['employee_id'], ['This employee already has an onboarding workflow.'])
        for role in ('it', 'manager', 'employee'):
            self.as_role(role)
            self.assertEqual(self.client.get('/api/onboarding/directory/').status_code, 403)

    def test_create_rejects_unknown_or_wrong_role_users(self):
        self.as_role('hr')
        base = {'job_title': 'Engineer', 'department': 'Engineering', 'start_date': timezone.localdate().isoformat()}
        for employee_id, manager_id in (
            (str(ObjectId()), str(self.users['manager']['_id'])),
            (str(self.users['manager']['_id']), str(self.users['manager']['_id'])),
            (str(self.other['_id']), str(self.users['hr']['_id'])),
            ('not-an-id', str(self.users['manager']['_id'])),
            (123, str(self.users['manager']['_id'])),
        ):
            with self.subTest(employee_id=employee_id, manager_id=manager_id):
                response = self.client.post('/api/onboarding/workflows/',
                                            {**base, 'employee_id': employee_id, 'manager_id': manager_id}, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertEqual(instances().count_documents({}), 1)

    def test_one_workflow_per_employee_is_enforced_by_the_database(self):
        with self.assertRaises(DuplicateKeyError):
            instances().insert_one({**self.record, '_id': ObjectId()})
        self.as_role('hr')
        with mock.patch.object(repository, 'create_workflow', side_effect=repository.DuplicateWorkflow):
            response = self.client.post('/api/onboarding/workflows/', {
                'employee_id': str(self.other['_id']), 'manager_id': str(self.users['manager']['_id']),
                'job_title': 'Engineer', 'department': 'Engineering',
                'start_date': timezone.localdate().isoformat()}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {'employee_id': ['This employee already has an onboarding workflow.']})

    def test_reschedule_requires_a_start_date(self):
        self.as_role('hr')
        response = self.client.patch(self.url(), {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_attendance_cannot_be_future_dated(self):
        mark_done(self.wid, ['equipment', 'mentor'])
        self.as_role('manager')
        data = self.evidence('welcome')
        data['session_date'] = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self.complete('welcome', data).status_code, 400)

    def test_unknown_ids_and_keys_are_not_found(self):
        self.as_role('hr')
        self.assertEqual(self.client.get(self.url('', ObjectId())).status_code, 404)
        self.assertEqual(self.client.get(self.url('steps/nope/')).status_code, 404)
        self.assertEqual(self.complete('nope', {}).status_code, 404)
        self.assertEqual(self.complete('profile', workflow_id=ObjectId()).status_code, 404)
        self.assertEqual(self.client.get('/api/onboarding/workflows/123/').status_code, 404)

    def test_timeline_is_in_array_order(self):
        self.as_role('hr')
        self.complete('profile')
        self.client.patch(self.url(), {'start_date': (timezone.localdate() + timedelta(days=12)).isoformat()})
        self.complete('contract')
        timeline = self.client.get(self.url('timeline/')).data['events']
        self.assertEqual([(row['action'], row['step']) for row in timeline],
                         [('completed', 'profile'), ('rescheduled', None), ('completed', 'contract')])
        self.assertEqual({row['actor'] for row in timeline}, {'hr'})
        detail_events = self.client.get(self.url()).data['events']
        self.assertEqual(detail_events, list(reversed(timeline)))  # v1 listed events newest first
        self.as_user(self.other)
        self.assertEqual(self.client.get(self.url('timeline/')).status_code, 404)

    def test_idempotency_key_header_is_bounded(self):
        self.as_role('hr')
        for key in ('', 'x' * 256, 'has space', 'tab\tkey'):
            with self.subTest(key=key):
                self.assertEqual(self.complete('profile', HTTP_IDEMPOTENCY_KEY=key).status_code, 400)
        self.assert_unchanged('profile')

    def test_healthz(self):
        self.client.credentials()
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'status': 'ok'})


class CreateWorkflowContractTests(WorkflowTestCase):
    def test_created_document_shape_and_provisioning_contract(self):
        doc = self.doc()
        self.assertEqual(doc, self.record)
        validator = Draft7Validator(load_provisioning_schema(), format_checker=FormatChecker())
        self.assertEqual([job['connector'] for job in doc['provisioning']], CONNECTORS)
        for job in doc['provisioning']:
            with self.subTest(connector=job['connector']):
                validator.validate(jsonable_job(job))
                self.assertEqual(job['idempotency_key'], f'{doc["_id"]}:{job["connector"]}')
                self.assertEqual(job['payload'], {'username': 'employee', 'display_name': 'employee',
                                                  'department': 'Engineering'})
                self.assertEqual((job['status'], job['attempts'], job['fence']), ('pending', 0, 0))
                self.assertIsNotNone(job['next_attempt_at'])
                for key in ('lease_owner', 'lease_until', 'last_error', 'external_ref', 'override_reason'):
                    self.assertIsNone(job[key])
        self.assertEqual((doc['version'], doc['status'], doc['issues'], doc['events']), (1, 'active', [], []))
        self.assertEqual(doc['created_at'].tzinfo, UTC)
        start = domain.parse_start_date(doc['start_date'])
        for step in doc['steps']:
            self.assertEqual(step['due_at'], domain.due_at(start, domain.STEPS[step['key']]['offset']))
            self.assertEqual(step['due_at'].tzinfo, UTC)
            self.assertEqual((step['status'], step['completion']), ('open', None))
