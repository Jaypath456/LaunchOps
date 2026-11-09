from django.core.management.base import BaseCommand

from onboarding.indexes import ensure_indexes


class Command(BaseCommand):
    help = 'Ensure MongoDB indexes and the workflow provisioning validator.'

    def handle(self, *args, **options):
        ensure_indexes()
        self.stdout.write(self.style.SUCCESS('LaunchOps indexes and validator ready.'))
