from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from users.models import User
from onboarding.models import Onboarding
from onboarding.services import create_onboarding

PASSWORD = 'LaunchOpsDemo2026!'


class Command(BaseCommand):
    help = 'Idempotently create a four-role onboarding demo with policy attestation.'

    def add_arguments(self, parser):
        parser.add_argument('--pagination-demo', action='store_true', help='Also add named, non-login demo workflows for browsing beyond 100 records.')
        parser.add_argument('--walkthrough-employee', help='Create a fresh employee account without a workflow for the local browser walkthrough.')

    @transaction.atomic
    def handle(self, *args, **kwargs):
        if not settings.DEBUG:
            raise CommandError('Demo fixtures are available only with DEBUG=True. No data was changed.')
        walkthrough_username = kwargs.get('walkthrough_employee')
        if walkthrough_username is not None:
            try:
                User._meta.get_field('username').clean(walkthrough_username, None)
            except ValidationError as exc:
                raise CommandError('Invalid walkthrough employee username.') from exc
            if User.objects.filter(username=walkthrough_username).exists() or walkthrough_username.startswith('demo_'):
                raise CommandError('Walkthrough employee must be a fresh, non-demo username. No data was changed.')
        people = {}
        for username, role in [('demo_hr', 'hr'), ('demo_it', 'it'), ('demo_manager', 'manager'), ('demo_employee', 'employee')]:
            user, created = User.objects.get_or_create(username=username, defaults={'role': role, 'email': username + '@example.test'})
            if created:
                user.set_password(PASSWORD)
                user.save(update_fields=['password'])
            elif user.role != role:
                raise CommandError(f'{username} already exists with role {user.role}; resolve the demo account collision first.')
            if user.is_staff or user.is_superuser or not user.is_active:
                raise CommandError(f'{username} collides with a privileged or inactive account. No data was changed.')
            people[role] = user
        if not Onboarding.objects.filter(employee=people['employee']).exists():
            create_onboarding(employee=people['employee'], manager=people['manager'], creator=people['hr'],
                job_title='Software Engineer', department='Engineering', start_date=timezone.localdate() + timedelta(days=7))
        if walkthrough_username is not None:
            User.objects.create_user(username=walkthrough_username, role='employee', password=PASSWORD,
                                     email=walkthrough_username + '@example.test')
        if kwargs['pagination_demo']:
            for number in range(1, 106):
                user, created = User.objects.get_or_create(username=f'demo_page_{number:03}', defaults={'role': 'employee'})
                if user.role != 'employee' or user.is_staff or user.is_superuser:
                    raise CommandError(f'{user.username} collides with an existing account. No data was changed.')
                if created:
                    user.set_unusable_password()
                    user.save(update_fields=['password'])
                if not Onboarding.objects.filter(employee=user).exists():
                    create_onboarding(employee=user, manager=people['manager'], creator=people['hr'],
                        job_title='Pagination demo', department='Demo fixtures', start_date=timezone.localdate())
        self.stdout.write(self.style.SUCCESS('LaunchOps local demo ready. Existing passwords and workflows preserved; see README for local sign-in instructions.'))
