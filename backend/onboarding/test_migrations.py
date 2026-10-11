"""Real migration executor on an isolated test database, never the live volume."""
from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class WorkflowMigrationTests(TransactionTestCase):
    def test_seven_step_history_survives_expansion_and_repeat(self):
        old = [('onboarding', '0003_step_evidence')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        executor.migrate(old)
        try:
            apps = executor.loader.project_state(old).apps
            User = apps.get_model('users', 'User')
            people = {r: User.objects.create(username='migration_' + r, role=r) for r in ['hr', 'manager', 'employee']}
            Workflow = apps.get_model('onboarding', 'Onboarding')
            Step = apps.get_model('onboarding', 'Step')
            Event = apps.get_model('onboarding', 'Event')
            record = Workflow.objects.create(employee=people['employee'], manager=people['manager'], created_by=people['hr'], start_date=date(1111, 11, 11), job_title='Existing', department='Ops')
            stamp = timezone.now()
            for key in ['profile', 'equipment', 'access', 'welcome', 'training', 'manager_signoff', 'ready']:
                Step.objects.create(onboarding=record, key=key, title=key, owner_role='hr', kind='approval', due_at=stamp,
                    completed_at=stamp, completed_by=people['hr'], evidence={'legacy': key})
            Event.objects.create(onboarding=record, actor=people['hr'], action='completed', detail='Existing signoff')
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            from onboarding.models import Onboarding, Step as CurrentStep, Event as CurrentEvent
            self.assertEqual(CurrentStep.objects.filter(onboarding_id=record.pk).count(), 20)
            self.assertEqual(CurrentStep.objects.filter(onboarding_id=record.pk, completed_at=stamp).count(), 7)
            self.assertEqual(CurrentStep.objects.get(onboarding_id=record.pk, key='ready').evidence, {'legacy': 'ready'})
            self.assertEqual(Onboarding.objects.get(pk=record.pk).start_date, date(1111, 11, 11))
            self.assertEqual(CurrentEvent.objects.filter(onboarding_id=record.pk).count(), 1)
            # The data operation itself, in addition to migration tracking, is repeatable.
            import importlib
            expand = importlib.import_module('onboarding.migrations.0004_expand_workflow').expand
            expand(executor.loader.project_state(latest).apps, None)
            self.assertEqual(CurrentStep.objects.filter(onboarding_id=record.pk).count(), 20)
            self.assertEqual(CurrentStep.objects.get(onboarding_id=record.pk, key='ready').evidence, {'legacy': 'ready'})
        finally:
            MigrationExecutor(connection).migrate(latest)
