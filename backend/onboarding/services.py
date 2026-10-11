from datetime import datetime, timedelta, time
from datetime import date as date_type

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError, PermissionDenied
from courses.models import Enrollment, Chapter
from quizzes.models import Quiz, Submission

from .models import Onboarding, Step, Event, Issue
from .blueprint import BLUEPRINT, FORMS, SIMULATIONS, HANDBOOK_VERSION

# Fixed, versioned workflow definition. Relationships are constructed server side
# and cannot be supplied by an untrusted client.

def due_at(start_date, offset):
    return timezone.make_aware(datetime.combine(start_date + timedelta(days=offset), time(hour=17)))


def validate_evidence(key, evidence):
    fields = FORMS[key]
    if not isinstance(evidence, dict):
        raise ValidationError({'evidence': 'Submit the task form.'})
    expected = {field['name'] for field in fields}
    if set(evidence) != expected:
        raise ValidationError({'evidence': 'Complete every field; extra fields are not accepted.'})
    clean = {}
    for spec in fields:
        name, kind = spec['name'], spec['type']
        value = evidence[name]
        if kind == 'checkbox':
            if value is not True:
                raise ValidationError({name: 'This confirmation is required.'})
        elif not isinstance(value, str) or not value.strip() or len(value) > 240 or any(ord(ch) < 32 for ch in value):
            raise ValidationError({name: 'Enter a value (up to 240 characters).'} )
        elif kind == 'select' and value not in spec['options']:
            raise ValidationError({name: 'Choose one of the available options.'})
        elif kind == 'date':
            try:
                if date_type.fromisoformat(value) < timezone.localdate() - timedelta(days=366) or date_type.fromisoformat(value) > timezone.localdate() + timedelta(days=1095):
                    raise ValueError()
            except ValueError:
                raise ValidationError({name: 'Enter a realistic date (YYYY-MM-DD).'} )
        clean[name] = value.strip() if isinstance(value, str) else value
    return clean


def create_onboarding(*, employee, manager, creator, job_title, department, start_date, course=None):
    with transaction.atomic():
        record = Onboarding.objects.create(employee=employee, manager=manager, created_by=creator,
                                            job_title=job_title, department=department,
                                            start_date=start_date, training_course=course)
        steps = {}
        for key, title, description, role, kind, offset, _ in BLUEPRINT:
            due = due_at(start_date, offset)
            steps[key] = Step.objects.create(onboarding=record, key=key, title=title,
                                           description=description, owner_role=role, kind=kind, due_at=due)
        for key, _, _, _, _, _, dependencies in BLUEPRINT:
            steps[key].prerequisites.add(*(steps[dependency] for dependency in dependencies))
        if course:
            Enrollment.objects.get_or_create(student=employee, course=course)
        Event.objects.create(onboarding=record, actor=creator, action='created', detail='Workflow launched')
    return record


def can_view(user, record):
    return user.role in ('hr', 'it') or user.pk in (record.employee_id, record.manager_id)


def step_state(step):
    if step.completed_at:
        return 'completed'
    if any(not prerequisite.completed_at for prerequisite in step.prerequisites.all()):
        return 'blocked'
    return 'ready'


def complete_step(step_id, actor, evidence=None):
    # Lock the parent row first: serializes concurrent transitions, including
    # operations on different steps of the same workflow, on PostgreSQL.
    with transaction.atomic():
        reference = Step.objects.only('onboarding_id').get(pk=step_id)
        record = Onboarding.objects.select_for_update().get(pk=reference.onboarding_id)
        step = Step.objects.select_for_update().get(pk=step_id, onboarding=record)
        if not can_view(actor, record):
            raise PermissionDenied('This onboarding is not visible to you.')
        if step.owner_role != actor.role or (actor.role == 'employee' and actor.pk != record.employee_id) or (actor.role == 'manager' and actor.pk != record.manager_id):
            raise PermissionDenied('This step belongs to another team.')
        if step.completed_at:
            return step, False  # Retry is safe; no duplicate event or side effect.
        if step.prerequisites.filter(completed_at__isnull=True).exists():
            raise ValidationError({'step': 'Complete prerequisite steps first.'})
        clean = validate_evidence(step.key, evidence)
        if step.key == 'welcome' and date_type.fromisoformat(clean['session_date']) > timezone.localdate():
            raise ValidationError({'session_date': 'Attendance cannot be recorded for a future session.'})
        if step.key in ('profile', 'ready') and not valid_start_date(record.start_date):
            raise ValidationError({'start_date': 'HR must correct the workflow start date before continuing.'})
        if step.key == 'ready':
            if record.steps.exclude(pk=step.pk).filter(completed_at__isnull=True).exists():
                raise ValidationError({'step': 'All other tasks must be complete before final signoff.'})
            if record.issues.filter(resolved_at__isnull=True).exists():
                raise ValidationError({'issues': 'Resolve all reported blockers before final signoff.'})
        if step.key in SIMULATIONS:
            clean['_proof'] = {'mode': 'simulation', 'reference': f'DEMO-{record.pk}-{step.key}',
                               'result': 'Demo operation recorded; no external verification performed.'}
            if step.key in ('equipment', 'endpoint', 'it_handoff', 'receipt'):
                equipment = record.steps.get(key='equipment')
                clean['_proof']['asset'] = equipment.evidence.get('_proof', {}).get('asset', f'DEMO-DEVICE-{record.pk}')
            if step.key == 'profile':
                clean['_proof']['profile'] = {'employee': record.employee.username, 'department': record.department,
                    'job_title': record.job_title, 'start_date': record.start_date.isoformat()}
        else:
            clean['_proof'] = {'mode': 'local_record', 'result': 'Saved in LaunchOps; external actions are self-reported.'}
        if step.key in ('policy', 'handbook'):
            clean['_proof']['version'] = HANDBOOK_VERSION
        if ((step.key == 'receipt' and clean['condition'] == 'Needs support') or
                (step.key == 'feedback' and clean['status'] == 'Needs support')):
            Issue.objects.get_or_create(onboarding=record, step=step, defaults={'detail': clean.get('feedback', 'Simulated device needs support')})
        if step.key == 'training':
            if not record.training_course_id:
                raise ValidationError({'training': 'Ask HR to assign a published learning track.'})
            chapters = Chapter.objects.filter(course_id=record.training_course_id, visibility='public')
            if not chapters.exists() or chapters.exclude(pk__in=record.lesson_reviews.values('chapter_id')).exists():
                raise ValidationError({'training': 'Acknowledge every linked lesson before completing training.'})
            quizzes = list(Quiz.objects.filter(course_id=record.training_course_id, is_published=True).values_list('id', flat=True))
            if not quizzes:
                raise ValidationError({'training': 'The training track has no published assessment yet.'})
            passed = set(Submission.objects.filter(student=record.employee, quiz_id__in=quizzes,
                                                   max_score__gt=0).extra(where=['score * 100 >= max_score * 70']).values_list('quiz_id', flat=True))
            if len(passed) != len(quizzes):
                raise ValidationError({'training': 'Pass every published assessment with at least 70% first.'})
        if step.key == 'training':
            clean['_proof'] = {'mode': 'assessment', 'result': 'Lesson acknowledgments and server-graded assessments; not proof of identity or reading.',
                'assessments': list(Submission.objects.filter(student=record.employee, quiz_id__in=quizzes).values('quiz_id', 'score', 'max_score'))}
        if step.key == 'ready':
            clean['_proof']['reviewed_tasks'] = list(record.steps.exclude(pk=step.pk).values_list('id', flat=True))
        step.completed_at = timezone.now()
        step.completed_by = actor
        step.evidence = clean
        step.save(update_fields=['completed_at', 'completed_by', 'evidence'])
        Event.objects.create(onboarding=record, step=step, actor=actor, action='completed', detail=f'{step.title} [{clean["_proof"]["mode"]}]')
        return step, True


def reschedule(record_id, actor, start_date):
    with transaction.atomic():
        record = Onboarding.objects.select_for_update().get(pk=record_id)
        if actor.role != 'hr':
            raise PermissionDenied('Only HR can correct a start date.')
        previous = record.start_date
        if previous == start_date:
            return record
        record.start_date = start_date
        record.save(update_fields=['start_date'])
        offsets = {key: offset for key, _, _, _, _, offset, _ in BLUEPRINT}
        steps = list(record.steps.all())
        for step in steps:
            step.due_at = due_at(start_date, offsets[step.key])
        Step.objects.bulk_update(steps, ['due_at'])
        Event.objects.create(onboarding=record, actor=actor, action='rescheduled',
                             detail=f'Start date changed from {previous} to {start_date}')
        return record


def valid_start_date(value):
    return timezone.localdate() - timedelta(days=366) <= value <= timezone.localdate() + timedelta(days=1095)
