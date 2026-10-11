from celery import shared_task
from django.utils import timezone
from django.db import transaction
from .models import Step, Event


@shared_task
def scan_overdue():
    """Record each overdue ready step once; the dashboard surfaces these events."""
    count = 0
    ids = Step.objects.filter(completed_at__isnull=True, due_at__lt=timezone.now()).values_list('id', flat=True)
    for pk in ids.iterator():
        with transaction.atomic():
            step = Step.objects.select_for_update().get(pk=pk)
            if step.completed_at or step.prerequisites.filter(completed_at__isnull=True).exists():
                continue
            _, created = Event.objects.get_or_create(onboarding=step.onboarding, step=step,
                                                     action='overdue', defaults={'detail': step.title})
            count += int(created)
    return count
