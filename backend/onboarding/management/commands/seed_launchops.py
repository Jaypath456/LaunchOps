import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from onboarding import repository
from onboarding.db import get_users
from onboarding.graph import ROLES, validate_blueprint
from onboarding.indexes import ensure_indexes


PASSWORD = 'LaunchOpsDemo2026!'
PAGINATION_USERS = [f'demo_page_{number:03}' for number in range(1, 106)]
# Django's username rules (UnicodeUsernameValidator, max_length=150).
USERNAME = re.compile(r'^[\w.@+-]+\Z')


def _actor(user):
    return {'id': str(user['_id']), 'username': user['username'], 'role': user['role']}


class Command(BaseCommand):
    help = 'Idempotently create a four-role onboarding demo with policy attestation.'

    def add_arguments(self, parser):
        parser.add_argument('--pagination-demo', action='store_true',
                            help='Also add named, non-login demo workflows for browsing beyond 100 records.')
        parser.add_argument('--walkthrough-employee',
                            help='Create a fresh employee account without a workflow for the local browser walkthrough.')
        parser.add_argument('--bulk-employees', type=int, default=0,
                            help='Create named chaos employees without workflows for local API campaigns.')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo fixtures are available only with DEBUG=True. No data was changed.')
        validate_blueprint()
        users = get_users()
        walkthrough = options.get('walkthrough_employee')
        pagination = options.get('pagination_demo')
        bulk = options.get('bulk_employees', 0)
        if bulk < 0:
            raise CommandError('Bulk employee count must be nonnegative. No data was changed.')
        bulk_names = [f'chaos-employee-{number:04d}' for number in range(1, bulk + 1)]

        # MongoDB has no multi-document transaction here: preflight every collision before any write.
        if walkthrough is not None:
            if not walkthrough or len(walkthrough) > 150 or not USERNAME.match(walkthrough):
                raise CommandError('Invalid walkthrough employee username.')
            if walkthrough.startswith('demo_') or users.find_one({'username': walkthrough}, {'_id': 1}) is not None:
                raise CommandError('Walkthrough employee must be a fresh, non-demo username. No data was changed.')
        for role in sorted(ROLES):
            username = f'demo_{role}'
            existing = users.find_one({'username': username})
            if existing is not None and existing.get('role') != role:
                raise CommandError(f'{username} already exists with a different role; resolve the demo account collision first.')
        if pagination:
            collision = users.find_one({'username': {'$in': PAGINATION_USERS}, 'role': {'$ne': 'employee'}})
            if collision is not None:
                raise CommandError(f'{collision["username"]} collides with an existing account. No data was changed.')
        if bulk_names:
            collision = users.find_one({'username': {'$in': bulk_names}, 'role': {'$ne': 'employee'}})
            if collision is not None:
                raise CommandError(f'{collision["username"]} collides with an existing account. No data was changed.')
            if walkthrough in bulk_names:
                raise CommandError('Walkthrough employee collides with a bulk employee. No data was changed.')

        ensure_indexes()
        for role in sorted(ROLES):
            username = f'demo_{role}'
            users.update_one({'username': username, 'role': role}, {'$setOnInsert': {
                'username': username,
                'display_name': f'Demo {role.upper() if role in ("hr", "it") else role.title()}',
                'role': role,
                'password': make_password(PASSWORD),
            }}, upsert=True)
        people = {user['role']: user for user in users.find({'username': {'$in': [f'demo_{role}' for role in ROLES]}})}
        hr, manager = _actor(people['hr']), people['manager']['_id']
        self._ensure_workflow(hr, people['employee']['_id'], manager, 'Software Engineer', 'Engineering',
                              timezone.localdate() + timedelta(days=7))
        if walkthrough is not None:
            users.insert_one({'username': walkthrough, 'display_name': walkthrough, 'role': 'employee',
                              'password': make_password(PASSWORD)})
        for username in bulk_names:
            users.update_one({'username': username, 'role': 'employee'}, {'$setOnInsert': {
                'username': username, 'display_name': username, 'role': 'employee',
                'password': make_password(None),
            }}, upsert=True)
        if pagination:
            for username in PAGINATION_USERS:
                users.update_one({'username': username, 'role': 'employee'}, {'$setOnInsert': {
                    'username': username, 'display_name': username.replace('_', ' ').title(), 'role': 'employee',
                    'password': make_password(None),  # unusable: these accounts cannot sign in
                }}, upsert=True)
            for user in users.find({'username': {'$in': PAGINATION_USERS}}).sort('username', 1):
                self._ensure_workflow(hr, user['_id'], manager, 'Pagination demo', 'Demo fixtures', timezone.localdate())
        self.stdout.write(self.style.SUCCESS(
            'LaunchOps local demo ready. Existing passwords and workflows preserved; see README for local sign-in instructions.'))

    def _ensure_workflow(self, hr, employee_id, manager_id, job_title, department, start_date):
        if repository.find_by_employee(employee_id) is not None:
            return
        try:
            repository.create_workflow(actor=hr, employee_id=employee_id, manager_id=manager_id, job_title=job_title,
                                       department=department, start_date=start_date)
        except repository.DuplicateWorkflow:
            pass
