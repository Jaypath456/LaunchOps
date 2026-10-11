"""Run with: docker compose exec -T backend python manage.py shell < scripts/create_audit_fixture.py
Creates a disposable-in-purpose (but retained) audit clone; never modifies source data.
"""
import os
from django.db import transaction
from django.utils import timezone
from onboarding.models import Onboarding, Step, Event
from courses.models import Enrollment
from users.models import User

with transaction.atomic():
    name = 'audit_migrated_' + timezone.now().strftime('%Y%m%d%H%M%S')
    employee = User.objects.create_user(username=name, password='LaunchOpsDemo2026!', role='employee')
    manager = User.objects.create_user(username=name + '_manager', password='LaunchOpsDemo2026!', role='manager')
    source = Onboarding.objects.get(pk=int(os.environ.get('AUDIT_SOURCE_WORKFLOW', '2')))
    pending = set(os.environ.get('AUDIT_PENDING_KEYS', '').split(','))
    record = Onboarding.objects.create(employee=employee, manager=manager, created_by=source.created_by,
        job_title='Migrated workflow audit', department='Product audit', start_date=source.start_date, training_course=source.training_course)
    mapping = {}
    for old in source.steps.all():
        mapping[old.pk] = Step.objects.create(onboarding=record, key=old.key, title=old.title, description=old.description,
            owner_role=old.owner_role, kind=old.kind, due_at=old.due_at, completed_at=None if old.key in pending else old.completed_at,
            completed_by=None if old.key in pending else old.completed_by, evidence={} if old.key in pending else old.evidence)
    for old in source.steps.all():
        mapping[old.pk].prerequisites.set([mapping[pk] for pk in old.prerequisites.values_list('pk', flat=True)])
    if record.training_course_id:
        Enrollment.objects.create(student=employee, course=record.training_course)
    Event.objects.create(onboarding=record, actor=source.created_by, action='audit_clone', detail=f'Audit copy of workflow {source.pk}; source untouched; pending {sorted(pending)}'[:240])
    print({'workflow': record.pk, 'employee': employee.username, 'manager': manager.username})
