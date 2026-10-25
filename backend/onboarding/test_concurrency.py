import threading
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from users.models import User
from onboarding.models import Event
from onboarding.services import create_onboarding, complete_step


def make_record(prefix):
    hr = User.objects.create(username=f'{prefix}_hr', role='hr')
    manager = User.objects.create(username=f'{prefix}_manager', role='manager')
    employee = User.objects.create(username=f'{prefix}_employee', role='employee')
    record = create_onboarding(employee=employee, manager=manager, creator=hr,
        job_title='Engineer', department='Ops', start_date=timezone.localdate())
    return hr, record


class CompletionConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_first_completion_writes_once(self):
        hr, record = make_record('parallel')
        step = record.steps.get(key='profile')
        def submit():
            close_old_connections()
            try:
                return complete_step(step.pk, hr, {'review': 'Match demo profile'})[1]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(Event.objects.filter(step=step, action='completed').count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_twenty_barrier_released_requests_complete_once(self):
        workers = 20
        hr, record = make_record('barrier')
        step = record.steps.get(key='profile')
        barrier = threading.Barrier(workers, timeout=30)

        def submit(_):
            try:
                barrier.wait()
                return ('ok', complete_step(step.pk, hr, {'review': 'Match demo profile'})[1])
            except Exception as exc:
                return ('error', repr(exc))
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(submit, range(workers)))

        errors = [r for r in results if r[0] == 'error']
        self.assertEqual(errors, [])
        created = [r[1] for r in results]
        self.assertEqual(created.count(True), 1)
        self.assertEqual(created.count(False), workers - 1)
        self.assertEqual(Event.objects.filter(step=step, action='completed').count(), 1)
        self.assertEqual(Event.objects.filter(onboarding=record, action='completed').count(), 1)
        step.refresh_from_db()
        self.assertIsNotNone(step.completed_at)
        self.assertEqual(step.completed_by_id, hr.pk)
