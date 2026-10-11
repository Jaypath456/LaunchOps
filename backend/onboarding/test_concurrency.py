from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from users.models import User
from onboarding.models import Event
from onboarding.services import create_onboarding, complete_step


class CompletionConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_first_completion_writes_once(self):
        hr = User.objects.create(username='parallel_hr', role='hr')
        manager = User.objects.create(username='parallel_manager', role='manager')
        employee = User.objects.create(username='parallel_employee', role='employee')
        record = create_onboarding(employee=employee, manager=manager, creator=hr,
            job_title='Engineer', department='Ops', start_date=timezone.localdate())
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
