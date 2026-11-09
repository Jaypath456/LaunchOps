"""t10b workflow regressions against real MongoDB, including guard negative controls."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from bson import ObjectId
from django.utils import timezone
from pymongo.errors import AutoReconnect, ExecutionTimeout, NetworkTimeout, WTimeoutError
from rest_framework.test import APIClient

from . import domain, repository
from .serializers import completion_data
from .test_concurrency import drop_element_guard, patched
from .testing import WorkflowTestCase, actor_of, bearer, events, instances


class RetryExhaustionTests(WorkflowTestCase):
    def test_last_miss_rereads_once_and_returns_the_winners_completion(self):
        """The final CAS miss replays an authorized winner without another write attempt."""
        calls = []
        winner = []
        collection = repository.instances()
        find_one = collection.find_one

        def competing_write():
            calls.append(True)
            if len(calls) < 5:
                instances().update_one({'_id': self.wid}, {'$inc': {'version': 1}})
            else:
                with mock.patch.object(repository, '_between_read_and_write'):
                    winner.append(repository.complete_step(
                        self.wid, 'profile', actor_of(self.users['hr']), self.evidence('profile'), 'winner'))

        self.as_role('hr')
        with mock.patch.object(repository, 'instances', return_value=collection), \
                mock.patch.object(collection, 'find_one', wraps=find_one) as reads, patched(competing_write):
            response = self.complete('profile', HTTP_IDEMPOTENCY_KEY='loser')
        self.assert_replayed(response)
        self.assertEqual(len(calls), 5)
        # Five attempt reads, the competing winner's read, and exactly one final reread.
        self.assertEqual(reads.call_args_list.count(mock.call({'_id': self.wid})), 7)
        self.assertEqual(response.data['completion'], completion_data(winner[0].result))
        self.assertEqual(self.step('profile')['completion'], winner[0].result)
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        self.assertEqual(self.doc()['version'], 6)

    def test_last_miss_without_a_replay_returns_409(self):
        """The final reread does not grant an extra write attempt while the step is open."""
        collection = repository.instances()
        find_one = collection.find_one

        def competing_write():
            instances().update_one({'_id': self.wid}, {'$inc': {'version': 1}})

        self.as_role('hr')
        with mock.patch.object(repository, 'instances', return_value=collection), \
                mock.patch.object(collection, 'find_one', wraps=find_one) as reads, patched(competing_write):
            response = self.complete('profile')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(reads.call_args_list.count(mock.call({'_id': self.wid})), 6)
        self.assert_unchanged('profile')
        self.assertEqual(self.doc()['version'], 6)


class DuplicateCreationTests(WorkflowTestCase):
    def test_racing_unique_index_loser_matches_precheck_status_and_body(self):
        """Both creators pass the precheck before either inserts; the unique index rejects one."""
        barrier = threading.Barrier(2, timeout=30)
        find_by_employee = repository.find_by_employee
        prechecks = []
        payload = {'employee_id': str(self.other['_id']), 'manager_id': str(self.users['manager']['_id']),
                   'job_title': 'Engineer', 'department': 'Engineering',
                   'start_date': timezone.localdate().isoformat()}
        authorization = bearer(self.users['hr'])

        def precheck(employee_id):
            found = find_by_employee(employee_id)
            prechecks.append(found)
            barrier.wait()
            return found

        def create(_):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=authorization)
            response = client.post('/api/onboarding/workflows/', payload, format='json')
            return response.status_code, response.json()

        with mock.patch.object(repository, 'find_by_employee', side_effect=precheck):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(create, range(2)))
        self.assertEqual(prechecks, [None, None])
        self.assertEqual(sorted(code for code, _ in results), [201, 400])
        loser = next(result for result in results if result[0] == 400)
        self.assertEqual(loser, create(None))
        self.assertEqual(loser[1], {'employee_id': ['This employee already has an onboarding workflow.']})
        self.assertEqual(instances().count_documents({'employee_id': self.other['_id']}), 1)


class TimestampOrderTests(WorkflowTestCase):
    def assert_order_and_replay(self, spacing):
        now = repository._now()
        times = [now + spacing * index for index in range(3)]
        self.as_role('hr')
        with mock.patch.object(repository, '_now', return_value=times[0]):
            first = self.complete('profile', HTTP_IDEMPOTENCY_KEY='original')
        self.assertEqual(first.status_code, 200)
        original_completion = self.step('profile')['completion']
        with mock.patch.object(repository, '_now', return_value=times[1]):
            response = self.client.patch(self.url(), {
                'start_date': (timezone.localdate() + timedelta(days=12)).isoformat()}, format='json')
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(repository, '_now', return_value=times[2]):
            self.assertEqual(self.complete('contract').status_code, 200)
        before_replay = self.doc()
        self.assertEqual([event['at'] for event in before_replay['events']], times)
        expected = [('completed', 'profile'), ('rescheduled', None), ('completed', 'contract')]
        self.assertEqual([(event['type'], event['step_key']) for event in before_replay['events']], expected)
        timeline = self.client.get(self.url('timeline/')).data['events']
        self.assertEqual([(event['action'], event['step']) for event in timeline], expected)
        self.assertEqual([event['id'] for event in timeline], [0, 1, 2])
        self.assertEqual(self.client.get(self.url()).data['events'], list(reversed(timeline)))
        with mock.patch.object(repository, '_now', return_value=now + timedelta(days=1)):
            replay = self.complete('profile', {'review': 'different evidence'}, HTTP_IDEMPOTENCY_KEY='retry')
        self.assert_replayed(replay)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(self.step('profile')['completion'], original_completion)
        self.assertEqual(self.doc(), before_replay)

    def test_tied_timestamps_preserve_array_order_and_stored_completion(self):
        self.assert_order_and_replay(timedelta())

    def test_decreasing_timestamps_preserve_array_order_and_stored_completion(self):
        self.assert_order_and_replay(timedelta(seconds=-1))

    def test_creation_timestamps_use_the_patchable_repository_clock(self):
        now = repository._now() - timedelta(seconds=1)
        with mock.patch.object(repository, '_now', return_value=now):
            doc = repository.create_workflow(
                actor=actor_of(self.users['hr']), employee_id=self.other['_id'],
                manager_id=self.users['manager']['_id'], job_title='Engineer', department='Engineering',
                start_date=timezone.localdate())
            self.assertEqual(repository.now_ms(), now)
        self.assertEqual(doc['created_at'], now)
        for job in doc['provisioning']:
            self.assertEqual(job['next_attempt_at'], now)
            self.assertEqual(job['updated_at'], now)
        self.assertEqual(self.doc(doc['_id']), doc)


class ElementGuardTests(WorkflowTestCase):
    def raw_completion(self, guard=None):
        actor = actor_of(self.users['hr'])
        decision = domain.decide_completion(
            self.doc(), 'profile', actor, self.evidence('profile'), by=self.users['hr']['_id'],
            request_id='raw-winner', now=repository._now(), employee_username='employee', issue_id=ObjectId())
        fired = []

        def raw_write_without_version_change():
            if fired:
                return
            fired.append(True)
            result = instances().update_one(
                {'_id': self.wid},
                {'$set': {'steps.$[s].status': 'done', 'steps.$[s].completion': decision.completion},
                 '$push': {'events': decision.event}},
                array_filters=[{'s.key': 'profile'}])
            self.assertEqual(result.matched_count, 1)
            self.assertEqual(self.doc()['version'], self.record['version'])

        self.as_role('hr')
        with patched(raw_write_without_version_change, guard):
            return self.complete('profile', HTTP_IDEMPOTENCY_KEY='stale-writer'), decision.completion

    def test_element_guard_rejects_raw_completion_without_version_bump(self):
        """Real MongoDB: unchanged version plus step $elemMatch rejects the stale write; one event, replay."""
        response, completion = self.raw_completion()
        self.assert_replayed(response)
        self.assertEqual(response.data['completion'], completion_data(completion))
        self.assertEqual(self.step('profile')['completion'], completion)
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        self.assertEqual(self.doc()['version'], 1)

    def test_dropping_only_element_guard_double_completes_despite_version_guard(self):
        """Real MongoDB negative control: keeping version but dropping step $elemMatch writes a second event."""
        response, completion = self.raw_completion(guard=drop_element_guard)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get('Idempotent-Replayed'))
        self.assertEqual(response.data['completion']['request_id'], 'stale-writer')
        self.assertEqual(completion['request_id'], 'raw-winner')
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 2)
        self.assertEqual(self.doc()['version'], 2)

    def test_array_filters_matching_no_element_still_increment_and_push(self):
        """Real MongoDB: matched_count=1, modified_count=1; no step changes, but version and event advance.

        An arrayFilter matching no element does not guard the document's $inc or $push.
        """
        before = self.doc()
        event = {'type': 'completed', 'step_key': 'profile', 'actor': str(self.users['hr']['_id']),
                 'at': repository._now(), 'detail': 'arrayFilters negative control'}
        result = instances().update_one(
            {'_id': self.wid, 'version': before['version']},
            {'$set': {'steps.$[s].status': 'done'}, '$inc': {'version': 1}, '$push': {'events': event}},
            array_filters=[{'s.key': 'profile', 's.status': 'done'}])
        self.assertEqual((result.matched_count, result.modified_count), (1, 1))
        after = self.doc()
        self.assertEqual(after['steps'], before['steps'])
        self.assertEqual(after['version'], before['version'] + 1)
        self.assertEqual(after['events'], [*before['events'], event])


class AmbiguousWriteTests(WorkflowTestCase):
    message = 'the write may or may not have been applied; check and retry'

    def assert_unknown(self, response):
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data, {'detail': self.message})

    def test_complete_applied_then_disconnected_retries_as_stored_replay(self):
        """A lost acknowledgement returns 503; retrying the key replays the sole stored completion."""
        collection = repository.instances()
        update_one = collection.update_one

        def write_then_disconnect(*args, **kwargs):
            self.assertEqual(update_one(*args, **kwargs).matched_count, 1)
            raise AutoReconnect('lost acknowledgement')

        self.as_role('hr')
        with mock.patch.object(repository, 'instances', return_value=collection), \
                mock.patch.object(collection, 'update_one', side_effect=write_then_disconnect) as write:
            response = self.complete('profile', HTTP_IDEMPOTENCY_KEY='uncertain')
        self.assert_unknown(response)
        self.assertEqual(write.call_count, 1)
        stored = self.doc()
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        self.assertEqual(self.step('profile')['completion']['request_id'], 'uncertain')
        retry = self.complete('profile', HTTP_IDEMPOTENCY_KEY='uncertain')
        self.assert_replayed(retry)
        self.assertEqual(retry.data['completion'], completion_data(self.step('profile')['completion']))
        self.assertEqual(self.doc(), stored)

    def test_reschedule_applied_then_disconnected_returns_503(self):
        """Reschedule also reports uncertainty; it has no operation id to identify a later replay."""
        collection = repository.instances()
        update_one = collection.update_one
        new_date = (timezone.localdate() + timedelta(days=12)).isoformat()

        def write_then_disconnect(*args, **kwargs):
            self.assertEqual(update_one(*args, **kwargs).matched_count, 1)
            raise AutoReconnect('lost acknowledgement')

        self.as_role('hr')
        with mock.patch.object(repository, 'instances', return_value=collection), \
                mock.patch.object(collection, 'update_one', side_effect=write_then_disconnect) as write:
            response = self.client.patch(self.url(), {'start_date': new_date}, format='json')
        self.assert_unknown(response)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(self.doc()['start_date'], new_date)
        self.assertEqual(self.doc()['version'], 2)
        self.assertEqual(len(events(self.wid, 'rescheduled')), 1)

    def test_each_conditional_write_connection_or_timeout_error_returns_503_without_retry(self):
        collection = repository.instances()
        self.as_role('hr')
        for error in (AutoReconnect, NetworkTimeout, ExecutionTimeout, WTimeoutError):
            with self.subTest(error=error.__name__), \
                    mock.patch.object(repository, 'instances', return_value=collection), \
                    mock.patch.object(collection, 'update_one', side_effect=error('uncertain')) as write:
                self.assert_unknown(self.complete('profile'))
                self.assertEqual(write.call_count, 1)
            self.assert_unchanged('profile')
            self.assertEqual(self.doc()['version'], 1)
