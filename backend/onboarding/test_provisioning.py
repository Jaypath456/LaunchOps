"""Django IT transitions and the access gate against real MongoDB."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta
from unittest import mock

from bson import Int64, ObjectId
from rest_framework.exceptions import PermissionDenied, ValidationError

from . import repository
from .testing import WorkflowTestCase, actor_of, events, instances, mark_done, succeed_jobs


class ProvisioningTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.as_role('it')
        self.it = actor_of(self.users['it'])

    def job(self, connector='workspace'):
        return next(job for job in self.doc()['provisioning'] if job['connector'] == connector)

    def set_job(self, **changes):
        instances().update_one({'_id': self.wid}, {'$set': {
            f'provisioning.$[j].{field}': value for field, value in changes.items()
        }}, array_filters=[{'j.connector': 'workspace'}])

    def action(self, action, data=None, connector='workspace', workflow_id=None):
        return self.client.post(self.url(f'provisioning/{connector}/{action}/', workflow_id),
                                {} if data is None else data, format='json')

    def test_manual_retry_only_from_dead_and_never_resets_fence_or_version(self):
        for state in ('pending', 'running', 'retry', 'succeeded', 'overridden', 'dead'):
            with self.subTest(state=state):
                self.set_job(status=state, attempts=5, fence=Int64(37), last_error='simulated failure',
                             next_attempt_at=repository.now_ms() + timedelta(days=1))
                before = self.doc()
                response = self.action('retry')
                if state != 'dead':
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(self.doc(), before)
                    continue
                self.assertEqual(response.status_code, 200, response.data)
                expected = {**before['provisioning'][0], 'status': 'pending', 'attempts': 0,
                            'updated_at': self.job()['updated_at'], 'next_attempt_at': self.job()['next_attempt_at']}
                self.assertEqual(self.job(), expected)
                self.assertEqual(self.job()['next_attempt_at'], self.job()['updated_at'])
                self.assertEqual(self.job()['updated_at'].tzinfo, UTC)
                after = self.doc()
                self.assertEqual({k: v for k, v in after.items() if k not in ('provisioning', 'events')},
                                 {k: v for k, v in before.items() if k not in ('provisioning', 'events')})
                self.assertEqual(after['provisioning'][1:], before['provisioning'][1:])
                [event] = after['events']
                self.assertEqual(event['type'], 'provisioning.manual_retry')
                self.assertEqual(event['actor'], self.it['id'])
                self.assertEqual(event['connector'], 'workspace')
                self.assertEqual(event['at'], self.job()['updated_at'])

    def test_override_state_table_preserves_fence_attempts_and_business_state(self):
        for state in ('pending', 'retry', 'dead', 'running', 'succeeded', 'overridden'):
            with self.subTest(state=state):
                self.set_job(status=state, attempts=4, fence=Int64(41))
                before = self.doc()
                response = self.action('override', {'reason': '  Simulator unavailable; IT approved access  '})
                if state not in ('pending', 'retry', 'dead'):
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(self.doc(), before)
                    continue
                self.assertEqual(response.status_code, 200, response.data)
                after = self.doc()
                self.assertEqual(self.job(), {**before['provisioning'][0], 'status': 'overridden',
                                             'override_reason': 'Simulator unavailable; IT approved access',
                                             'updated_at': self.job()['updated_at']})
                self.assertEqual({k: v for k, v in after.items() if k not in ('provisioning', 'events')},
                                 {k: v for k, v in before.items() if k not in ('provisioning', 'events')})
                self.assertEqual(after['provisioning'][1:], before['provisioning'][1:])
                self.assertEqual(after['events'][:-1], before['events'])
                event = after['events'][-1]
                self.assertEqual(event['type'], 'provisioning.overridden')
                self.assertIn(self.job()['override_reason'], event['detail'])

    def test_override_requires_a_nonempty_string_reason_without_writing(self):
        before = self.doc()
        for data in ({}, {'reason': None}, {'reason': ''}, {'reason': ' \t\n '}, {'reason': False},
                     {'reason': []}, []):
            with self.subTest(data=data):
                self.assertEqual(self.action('override', data).status_code, 400)
                self.assertEqual(self.doc(), before)
        with self.assertRaises(ValidationError):
            repository.override_job(self.wid, 'workspace', self.it, ' ')

    def test_endpoints_and_repository_are_it_only(self):
        for role in ('hr', 'manager', 'employee'):
            self.as_role(role)
            before = self.doc()
            for action in ('retry', 'override'):
                with self.subTest(role=role, action=action):
                    self.assertEqual(self.action(action, {'reason': 'Approved by IT'}).status_code, 403)
            actor = actor_of(self.users[role])
            with self.assertRaises(PermissionDenied):
                repository.retry_job(self.wid, 'workspace', actor)
            with self.assertRaises(PermissionDenied):
                repository.override_job(self.wid, 'workspace', actor, '')
            self.assertEqual(self.doc(), before)
        self.client.credentials()
        for action in ('retry', 'override'):
            self.assertEqual(self.action(action, {'reason': 'Approved by IT'}).status_code, 401)

    def test_missing_workflow_and_unmatched_connector(self):
        for action in ('retry', 'override'):
            with self.subTest(action=action):
                self.assertEqual(self.action(action, {'reason': 'IT approved'}, workflow_id=ObjectId()).status_code, 404)
                self.assertEqual(self.action(action, {'reason': 'IT approved'}, connector='unknown').status_code, 409)
        self.assertEqual(self.doc(), self.record)

    def test_competing_claim_prevents_override_atomically(self):
        collection = instances()
        update = collection.update_one

        def claim_then_override(*args, **kwargs):
            update({'_id': self.wid}, {'$set': {'provisioning.0.status': 'running',
                                             'provisioning.0.fence': Int64(1)}})
            return update(*args, **kwargs)

        with mock.patch.object(repository, 'instances', return_value=collection), \
                mock.patch.object(collection, 'update_one', side_effect=claim_then_override):
            response = self.action('override', {'reason': 'IT approved'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.job()['status'], self.job()['fence']), ('running', 1))
        self.assertEqual(self.doc()['events'], [])
        self.assertEqual(self.doc()['version'], self.record['version'])

    def test_concurrent_manual_retries_only_write_one_event(self):
        self.set_job(status='dead', attempts=5, fence=Int64(13))

        def retry(_):
            try:
                repository.retry_job(self.wid, 'workspace', self.it)
                return 200
            except repository.Conflict:
                return 409

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(retry, range(2))), [200, 409])
        self.assertEqual(len(self.doc()['events']), 1)
        self.assertEqual((self.job()['status'], self.job()['fence'], self.doc()['version']), ('pending', 13, 1))

    def test_job_actions_are_not_invalidated_by_business_version_changes(self):
        self.set_job(status='dead', fence=Int64(8))
        repository.reschedule(self.wid, actor_of(self.users['hr']),
                              repository.domain.parse_start_date(self.record['start_date']) + timedelta(days=1))
        version = self.doc()['version']
        self.assertEqual(self.action('retry').status_code, 200)
        self.assertEqual(self.action('override', {'reason': 'IT approved'}).status_code, 200)
        self.assertEqual(self.doc()['version'], version)
        self.assertEqual(self.job()['fence'], 8)

    def test_gate_blocks_each_nonterminal_status_even_when_other_jobs_succeeded(self):
        mark_done(self.wid, ['profile'])
        succeed_jobs(self.wid)
        for state in ('pending', 'retry', 'dead', 'running'):
            with self.subTest(state=state):
                self.set_job(status=state)
                before = self.doc()
                response = self.complete('access')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('provisioning', response.data)
                self.assertEqual(self.doc(), before)
                self.assert_unchanged('access')

    def test_gate_allows_succeeded_and_overridden_and_copies_server_results(self):
        mark_done(self.wid, ['profile'])
        succeed_jobs(self.wid)
        self.set_job(status='dead', external_ref=None, last_error='Simulator unavailable')
        self.assertEqual(self.action('override', {'reason': 'IT approved simulated access'}).status_code, 200)
        before = self.doc()
        response = self.complete('access', HTTP_IDEMPOTENCY_KEY='access-result')
        self.assertEqual(response.status_code, 200, response.data)
        expected = [{field: job[field] for field in ('connector', 'status', 'external_ref', 'override_reason')}
                    for job in before['provisioning']]
        evidence = self.step('access')['completion']['evidence']
        self.assertEqual(evidence, {**self.evidence('access'), 'provisioning': expected})
        self.assertEqual(response.data['completion']['evidence'], evidence)
        self.assertEqual(self.doc()['version'], before['version'] + 1)
        self.assertEqual(self.doc()['provisioning'], before['provisioning'])
        self.assertEqual(len(events(self.wid, 'completed', 'access')), 1)
        replay = self.complete('access', HTTP_IDEMPOTENCY_KEY='access-result')
        self.assert_replayed(replay)
        self.assertEqual(replay.data, response.data)

    def test_access_cannot_supply_its_own_provisioning_evidence(self):
        mark_done(self.wid, ['profile'])
        succeed_jobs(self.wid)
        response = self.complete('access', {**self.evidence('access'), 'provisioning': []})
        self.assertEqual(response.status_code, 400)
        self.assert_unchanged('access')

    def test_gate_uses_mutation_snapshot_and_can_conservatively_reject(self):
        mark_done(self.wid, ['profile'])
        with mock.patch.object(repository, '_between_read_and_write', side_effect=lambda: succeed_jobs(self.wid)):
            self.assertEqual(self.complete('access').status_code, 400)
        self.assert_unchanged('access')
        self.assertEqual(self.complete('access').status_code, 200)

    def test_access_panel_contains_job_details_and_other_steps_do_not(self):
        self.set_job(status='running', attempts=2, lease_owner='simulated-worker', last_error='Simulated timeout',
                     external_ref='simulated-account')
        for role in ('hr', 'it', 'manager', 'employee'):
            with self.subTest(role=role):
                self.as_role(role)
                response = self.client.get(self.url('steps/access/'))
                self.assertEqual(response.status_code, 200)
                panel = response.data['provisioning']
                self.assertEqual([job['connector'] for job in panel], ['workspace', 'slack', 'github'])
                self.assertEqual({key: panel[0][key] for key in ('status', 'attempts', 'lease_owner', 'last_error',
                                                              'external_ref', 'override_reason')},
                                 {'status': 'running', 'attempts': 2, 'lease_owner': 'simulated-worker',
                                  'last_error': 'Simulated timeout', 'external_ref': 'simulated-account',
                                  'override_reason': None})
                self.assertIsNone(self.client.get(self.url('steps/profile/')).data['provisioning'])
