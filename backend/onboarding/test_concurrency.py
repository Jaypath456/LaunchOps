"""Compare-and-set tests (spec section 10, items 1-5) against real MongoDB.

Threads are synchronised inside `repository._between_read_and_write`: every thread's FIRST read is held at
a barrier, so all of them build from the same snapshot before any write lands. That makes the race
deterministic instead of hoping the scheduler interleaves it.
"""

import json
import threading
from contextlib import ExitStack, contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import mock

from django.utils import timezone
from rest_framework.test import APIClient

from . import repository
from .testing import WorkflowTestCase, actor_of, bearer, events, instances, mark_done, valid_evidence


def drop_all_guards(doc, plan):
    return {'_id': doc['_id']}


def drop_version_guard(doc, plan):
    return {'_id': doc['_id'], **plan.guard}


def drop_element_guard(doc, plan):
    return {'_id': doc['_id'], 'version': doc['version']}


@contextmanager
def patched(between, guard=None):
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(repository, '_between_read_and_write', between))
        if guard is not None:
            stack.enter_context(mock.patch.object(repository, 'guard_filter', guard))
        yield


class FirstReadBarrier:
    def __init__(self, parties):
        self.barrier = threading.Barrier(parties, timeout=30)
        self.local = threading.local()

    def __call__(self):
        if not getattr(self.local, 'waited', False):
            self.local.waited = True
            self.barrier.wait()


class ConcurrencyTestCase(WorkflowTestCase):
    def post_concurrently(self, requests, guard=None):
        """requests: list of (user, key, idempotency_key). Returns (status, body, replayed) per request."""
        def submit(request):
            user, key, idempotency_key = request
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=bearer(user))
            headers = {'HTTP_IDEMPOTENCY_KEY': idempotency_key} if idempotency_key else {}
            response = client.post(self.url(f'steps/{key}/complete/'), {'evidence': valid_evidence(key)},
                                   format='json', **headers)
            return response.status_code, response.json(), response.headers.get('Idempotent-Replayed') == 'true'

        with patched(FirstReadBarrier(len(requests)), guard):
            with ThreadPoolExecutor(max_workers=len(requests)) as pool:
                return list(pool.map(submit, requests))

    def twenty_on_profile(self, guard=None):
        return self.post_concurrently([(self.users['hr'], 'profile', None)] * 20, guard=guard)


class CompareAndSetTests(ConcurrencyTestCase):
    # 1) 20 threads complete one step.
    def test_twenty_threads_one_step_complete_once_with_identical_responses(self):
        results = self.twenty_on_profile()
        self.assertEqual([status for status, _, _ in results], [200] * 20)
        self.assertEqual(len({json.dumps(body, sort_keys=True) for _, body, _ in results}), 1)
        self.assertEqual(sum(not replayed for _, _, replayed in results), 1)
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        doc = self.doc()
        self.assertEqual(doc['version'], 2)
        self.assertEqual(results[0][1]['completion']['by'], str(self.users['hr']['_id']))
        self.assertEqual(self.step('profile')['status'], 'done')

    # 2) Negative control: with every guard removed, the same race writes duplicates.
    def test_negative_control_without_guards_duplicates_are_detected(self):
        results = self.twenty_on_profile(guard=drop_all_guards)
        self.assertEqual([status for status, _, _ in results], [200] * 20)
        duplicates = events(self.wid, 'completed', 'profile')
        self.assertGreater(len(duplicates), 1, 'removing the guards must let duplicate completions through')
        self.assertGreater(self.doc()['version'], 2)

    def test_step_element_guard_alone_gives_exactly_once(self):
        # Spec layering: open -> done is monotone, so the $elemMatch guard alone prevents duplicates.
        self.twenty_on_profile(guard=drop_version_guard)
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        self.assertEqual(self.doc()['version'], 2)

    def test_version_guard_alone_gives_exactly_once(self):
        self.twenty_on_profile(guard=drop_element_guard)
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        self.assertEqual(self.doc()['version'], 2)

    # 3) N threads, N different steps.
    def test_threads_on_different_steps_all_succeed(self):
        mark_done(self.wid, ['profile'])
        start = self.doc()['version']
        owners = {'contract': 'hr', 'policy': 'hr', 'equipment': 'it', 'access': 'it', 'mentor': 'manager'}
        # N <= retries (5): each failed attempt of a thread consumes a distinct success of another thread.
        results = self.post_concurrently([(self.users[role], key, None) for key, role in owners.items()])
        self.assertEqual([(status, replayed) for status, _, replayed in results], [(200, False)] * len(owners))
        doc = self.doc()
        self.assertEqual(doc['version'], start + len(owners))
        self.assertEqual(sorted(event['step_key'] for event in doc['events']), sorted(owners))
        for key in owners:
            self.assertEqual(self.step(key)['status'], 'done')

    # 4) Stale snapshot on a real cross-step rule.
    def stale_reschedule(self, guard=None):
        hr = actor_of(self.users['hr'])
        new_date = timezone.localdate() + timedelta(days=15)
        fired = []

        def competing_write():
            if not fired:
                fired.append(True)
                repository.reschedule(self.wid, hr, new_date)  # a real reschedule lands after our read

        with patched(competing_write, guard):
            return repository.reschedule(self.wid, hr, new_date), new_date

    def test_stale_snapshot_reschedule_to_same_date_is_a_noop_after_retry(self):
        # Rule (v1): rescheduling to the current start date writes nothing and records no event.
        original = self.doc()['start_date']
        outcome, new_date = self.stale_reschedule()
        self.assertEqual((outcome.attempts, outcome.replayed, outcome.result), (2, True, False))
        [event] = events(self.wid, 'rescheduled')
        self.assertEqual(event['detail'], f'Start date changed from {original} to {new_date.isoformat()}')
        self.assertEqual(self.doc()['version'], 2)

    def test_stale_snapshot_without_version_guard_records_a_duplicate(self):
        original = self.doc()['start_date']
        outcome, new_date = self.stale_reschedule(guard=drop_version_guard)
        self.assertEqual((outcome.attempts, outcome.replayed), (1, False))
        rescheduled = events(self.wid, 'rescheduled')
        self.assertEqual(len(rescheduled), 2, 'removing the version guard must let the stale write through')
        self.assertEqual({event['detail'] for event in rescheduled},
                         {f'Start date changed from {original} to {new_date.isoformat()}'})

    def test_stale_profile_proof_is_rebuilt_from_the_new_start_date(self):
        # Rule (v1): the profile proof records the workflow's start date at completion time.
        hr = actor_of(self.users['hr'])
        new_date = timezone.localdate() + timedelta(days=20)
        fired = []

        def competing_write():
            if not fired:
                fired.append(True)
                repository.reschedule(self.wid, hr, new_date)

        with patched(competing_write):
            outcome = repository.complete_step(self.wid, 'profile', hr, valid_evidence('profile'))
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(self.step('profile')['completion']['proof']['profile']['start_date'], new_date.isoformat())

    def test_retry_exhaustion_returns_409_without_changes(self):
        calls = []

        def always_competing():
            calls.append(True)
            instances().update_one({'_id': self.wid}, {'$inc': {'version': 1}})

        self.as_role('hr')
        with patched(always_competing):
            response = self.complete('profile')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(calls), 5)
        self.assert_unchanged('profile')


class IdempotencyTests(ConcurrencyTestCase):
    # 5) Idempotency replay and same-key concurrency.
    def test_replay_returns_the_original_completion_whatever_key_arrives(self):
        self.as_role('hr')
        first = self.complete('profile', HTTP_IDEMPOTENCY_KEY='request-1')
        self.assertEqual(first.status_code, 200)
        self.assertIsNone(first.headers.get('Idempotent-Replayed'))
        self.assertEqual(first.data['completion']['request_id'], 'request-1')
        self.assertEqual(self.step('profile')['completion']['request_id'], 'request-1')
        for headers in ({'HTTP_IDEMPOTENCY_KEY': 'request-1'}, {'HTTP_IDEMPOTENCY_KEY': 'request-2'}, {}):
            with self.subTest(headers=headers):
                replay = self.complete('profile', **headers)
                self.assert_replayed(replay)
                self.assertEqual(replay.data, first.data)
        # A replay ignores the submitted evidence, as in v1.
        self.assert_replayed(self.complete('profile', {'review': 'anything'}))
        self.assertEqual(len(events(self.wid, 'completed', 'profile')), 1)
        self.assertEqual(self.doc()['version'], 2)

    def test_permission_checks_run_before_the_replay_check(self):
        self.as_role('hr')
        self.assertEqual(self.complete('profile', HTTP_IDEMPOTENCY_KEY='request-1').status_code, 200)
        for user in (self.users['it'], self.users['manager'], self.users['employee'], self.other):
            with self.subTest(user=user['username']):
                self.as_user(user)
                self.assertEqual(self.complete('profile', HTTP_IDEMPOTENCY_KEY='request-1').status_code, 403)

    def test_same_key_concurrency_completes_once(self):
        mark_done(self.wid, ['profile'])
        results = self.post_concurrently([(self.users['hr'], 'contract', 'same-key')] * 10)
        self.assertEqual([status for status, _, _ in results], [200] * 10)
        self.assertEqual(len({json.dumps(body, sort_keys=True) for _, body, _ in results}), 1)
        self.assertEqual(sum(not replayed for _, _, replayed in results), 1)
        self.assertEqual(results[0][1]['completion']['request_id'], 'same-key')
        self.assertEqual(len(events(self.wid, 'completed', 'contract')), 1)
