from datetime import datetime, timedelta, time
from django.db import migrations
from django.utils import timezone

# Frozen definition: migration must stay stable as new workflows evolve.
ROWS = [('profile', 'Verify employee profile', 'Confirm the employee, start date and team.', 'hr', 'approval', -7, []), ('contract', 'Confirm paperwork', 'Record the signed paperwork reference and review date.', 'hr', 'approval', -6, ['profile']), ('payroll', 'Confirm payroll setup', 'Record the internal payroll ticket without financial details.', 'hr', 'approval', -5, ['contract']), ('policy', 'Assign required policies', 'Choose the policy packet and record its distribution.', 'hr', 'approval', -4, ['profile']), ('equipment', 'Prepare equipment', 'Assign a device and confirm its delivery plan.', 'it', 'provisioning', -3, ['profile']), ('access', 'Provision work access', 'Confirm simulated account provisioning with a ticket reference.', 'it', 'provisioning', -2, ['profile']), ('mfa', 'Verify multi-factor authentication', 'Confirm MFA enrollment without recording recovery codes.', 'it', 'provisioning', -1, ['access']), ('endpoint', 'Verify device security', 'Confirm encryption and endpoint protection.', 'it', 'provisioning', 0, ['equipment']), ('it_handoff', 'Complete IT handoff', 'Confirm the new hire received device and support instructions.', 'it', 'provisioning', 1, ['mfa', 'endpoint']), ('mentor', 'Assign a mentor', 'Introduce the new hire to a team mentor.', 'manager', 'orientation', -1, ['profile']), ('welcome', 'Attend team orientation', 'Confirm when and where the orientation took place.', 'manager', 'orientation', 1, ['equipment', 'mentor']), ('goals', 'Set first-week goals', 'Document a concrete goal and owner.', 'manager', 'orientation', 2, ['welcome']), ('calendar', 'Schedule check-ins', 'Record the date of the first one-to-one meeting.', 'manager', 'orientation', 2, ['welcome']), ('manager_signoff', 'Manager check-in', 'Confirm goals and check-ins have been discussed.', 'manager', 'approval', 4, ['goals', 'calendar']), ('contact', 'Confirm contact preferences', 'Tell the team how to reach you at work.', 'employee', 'orientation', 1, ['profile']), ('handbook', 'Review team handbook', 'Read the handbook and confirm the version.', 'employee', 'training', 2, ['policy']), ('training', 'Complete security training', 'Read the linked track and pass every published assessment.', 'employee', 'training', 3, ['access']), ('receipt', 'Acknowledge equipment receipt', 'Confirm the device arrived and report its condition.', 'employee', 'provisioning', 3, ['it_handoff']), ('feedback', 'Share first-week feedback', 'Report any blockers or confirm you have none.', 'employee', 'orientation', 4, ['handbook', 'training']), ('ready', 'Confirm onboarding readiness', 'Review the completed tasks before closing onboarding.', 'hr', 'approval', 5, ['payroll', 'policy', 'it_handoff', 'manager_signoff', 'contact', 'feedback', 'receipt'])]


def expand(apps, schema_editor):
    Onboarding = apps.get_model('onboarding', 'Onboarding')
    Step = apps.get_model('onboarding', 'Step')
    for record in Onboarding.objects.all().iterator():
        steps = {step.key: step for step in Step.objects.filter(onboarding_id=record.pk)}
        for key, title, description, role, kind, offset, dependencies in ROWS:
            if key not in steps:
                due = timezone.make_aware(datetime.combine(record.start_date + timedelta(days=offset), time(hour=17)))
                steps[key] = Step.objects.create(onboarding_id=record.pk, key=key, title=title,
                    description=description, owner_role=role, kind=kind, due_at=due)
        for key, title, description, role, kind, offset, dependencies in ROWS:
            steps[key].prerequisites.set([steps[dependency] for dependency in dependencies])


def reverse(apps, schema_editor):
    # Removing task history or dependencies would destroy user data.
    pass


class Migration(migrations.Migration):
    dependencies = [('onboarding', '0003_step_evidence')]
    operations = [migrations.RunPython(expand, reverse)]
