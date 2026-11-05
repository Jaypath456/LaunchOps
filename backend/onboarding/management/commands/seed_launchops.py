from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError

from onboarding.db import get_users
from onboarding.graph import ROLES, validate_blueprint
from onboarding.indexes import ensure_indexes


PASSWORD = 'LaunchOpsDemo2026!'


class Command(BaseCommand):
    help = 'Idempotently create local demo accounts for the four onboarding roles.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Demo fixtures are available only with DEBUG=True. No data was changed.')
        validate_blueprint()
        ensure_indexes()
        users = get_users()
        # Preflight all existing names before inserting any accounts. Existing
        # credentials and roles must never be overwritten by a demo seed.
        for role in sorted(ROLES):
            username = f'demo_{role}'
            existing = users.find_one({'username': username})
            if existing is not None and existing.get('role') != role:
                raise CommandError(f'{username} already exists with a different role; resolve the demo account collision first.')

        for role in sorted(ROLES):
            username = f'demo_{role}'
            users.update_one({'username': username, 'role': role}, {'$setOnInsert': {
                'username': username,
                'display_name': f'Demo {role.upper() if role in ("hr", "it") else role.title()}',
                'role': role,
                'password': make_password(PASSWORD),
            }}, upsert=True)
        self.stdout.write(self.style.SUCCESS('LaunchOps local demo users ready. Existing passwords preserved.'))
