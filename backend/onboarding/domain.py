"""Pure workflow rules ported from v1 services: no IO and no database imports.

Callers pass the instance document, the actor from the token and the current time.
Identifiers (user ObjectIds, issue ids) are opaque values supplied by the caller.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .blueprint import BLUEPRINT, FORMS, HANDBOOK_VERSION, SIMULATIONS, TRAINING_POLICY_VERSION


STEP_ORDER = [row[0] for row in BLUEPRINT]
STEPS = {
    key: {'key': key, 'title': title, 'description': description, 'owner_role': role, 'kind': kind,
          'offset': offset, 'prerequisites': list(prerequisites)}
    for key, title, description, role, kind, offset, prerequisites in BLUEPRINT
}
PREREQUISITES = {key: definition['prerequisites'] for key, definition in STEPS.items()}
ASSET_STEPS = ('equipment', 'endpoint', 'it_handoff', 'receipt')
# Final and manager reviews expose the actual saved artifacts, not just checkboxes.
REVIEW_KEYS = {'manager_signoff': ['goals', 'calendar'], 'receipt': ['equipment', 'it_handoff'],
               'endpoint': ['equipment'], 'mfa': ['access'], 'it_handoff': ['equipment', 'mfa', 'endpoint']}


def due_at(start_date, offset):
    return datetime.combine(start_date + timedelta(days=offset), time(hour=17), tzinfo=UTC)


def parse_start_date(value):
    return date.fromisoformat(value)


def valid_start_date(value, today):
    return today - timedelta(days=366) <= value <= today + timedelta(days=1095)


def validate_evidence(key, evidence, today):
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
            raise ValidationError({name: 'Enter a value (up to 240 characters).'})
        elif kind == 'select' and value not in spec['options']:
            raise ValidationError({name: 'Choose one of the available options.'})
        elif kind == 'date':
            try:
                parsed = date.fromisoformat(value)
                if parsed < today - timedelta(days=366) or parsed > today + timedelta(days=1095):
                    raise ValueError()
            except ValueError:
                raise ValidationError({name: 'Enter a realistic date (YYYY-MM-DD).'})
        clean[name] = value.strip() if isinstance(value, str) else value
    return clean


# Readiness and overdue are computed on read.

def steps_by_key(doc):
    return {step['key']: step for step in doc['steps']}


def step_state(step, steps):
    if step['status'] == 'done':
        return 'done'
    if any(steps[prerequisite]['status'] != 'done' for prerequisite in PREREQUISITES[step['key']]):
        return 'blocked'
    return 'ready'


def is_overdue(step, steps, now):
    return step_state(step, steps) == 'ready' and step['due_at'] < now


# Permissions for the four roles.

def can_view(actor, doc):
    return actor['role'] in ('hr', 'it') or actor['id'] in (str(doc['employee_id']), str(doc['manager_id']))


def list_scope(actor):
    """Role scope as (field, actor id) or None for every workflow."""
    if actor['role'] in ('hr', 'it'):
        return None
    if actor['role'] == 'manager':
        return 'manager_id'
    return 'employee_id'


def check_can_complete(actor, doc, key):
    if not can_view(actor, doc):
        raise PermissionDenied('This onboarding is not visible to you.')
    if (STEPS[key]['owner_role'] != actor['role']
            or (actor['role'] == 'employee' and actor['id'] != str(doc['employee_id']))
            or (actor['role'] == 'manager' and actor['id'] != str(doc['manager_id']))):
        raise PermissionDenied('This step belongs to another team.')


def require_hr(actor, message=None):
    if actor['role'] != 'hr':
        raise PermissionDenied(message)


# Step completion.

def completion_mode(key, step):
    if step['status'] == 'done':
        return ((step.get('completion') or {}).get('proof') or {}).get('mode', 'legacy_unverified')
    return 'simulation' if key in SIMULATIONS else 'attestation' if key == 'training' else 'local_record'


def build_proof(doc, key, employee_username):
    instance_id = str(doc['_id'])
    if key in SIMULATIONS:
        proof = {'mode': 'simulation', 'reference': f'DEMO-{instance_id}-{key}',
                 'result': 'Demo operation recorded; no external verification performed.'}
        if key in ASSET_STEPS:
            equipment = steps_by_key(doc)['equipment']
            equipment_proof = (equipment.get('completion') or {}).get('proof') or {}
            proof['asset'] = equipment_proof.get('asset', f'DEMO-DEVICE-{instance_id}')
        if key == 'profile':
            proof['profile'] = {'employee': employee_username, 'department': doc['department'],
                                'job_title': doc['job_title'], 'start_date': doc['start_date']}
    else:
        proof = {'mode': 'local_record', 'result': 'Saved in LaunchOps; external actions are self-reported.'}
    if key in ('policy', 'handbook'):
        proof['version'] = HANDBOOK_VERSION
    if key == 'training':
        proof = training_attestation()
    if key == 'ready':
        proof['reviewed_tasks'] = [step['key'] for step in doc['steps'] if step['key'] != key]
    return proof


def training_attestation():
    return {'mode': 'attestation', 'version': TRAINING_POLICY_VERSION,
            'result': 'Acknowledgment recorded; not proof of reading or identity.'}


def raised_issue_detail(key, clean):
    if ((key == 'receipt' and clean['condition'] == 'Needs support')
            or (key == 'feedback' and clean['status'] == 'Needs support')):
        return clean.get('feedback', 'Simulated device needs support')
    return None


@dataclass(frozen=True)
class CompletionDecision:
    completion: dict
    replay: bool = False
    event: dict | None = None
    issue: dict | None = None
    closes_workflow: bool = False


def decide_completion(doc, key, actor, evidence, *, by, request_id, now, employee_username, issue_id):
    """Apply the v1 completion rules in v1 order; permission checks run before the replay check."""
    if key not in STEPS:
        raise NotFound()
    check_can_complete(actor, doc, key)
    steps = steps_by_key(doc)
    step = steps[key]
    if step['status'] == 'done':
        return CompletionDecision(completion=step['completion'], replay=True)
    if any(steps[prerequisite]['status'] != 'done' for prerequisite in PREREQUISITES[key]):
        raise ValidationError({'step': 'Complete prerequisite steps first.'})
    today = now.date()
    clean = validate_evidence(key, evidence, today)
    if key == 'welcome' and date.fromisoformat(clean['session_date']) > today:
        raise ValidationError({'session_date': 'Attendance cannot be recorded for a future session.'})
    if key in ('profile', 'ready') and not valid_start_date(parse_start_date(doc['start_date']), today):
        raise ValidationError({'start_date': 'HR must correct the workflow start date before continuing.'})
    if key == 'ready':
        if any(other['status'] != 'done' for other in doc['steps'] if other['key'] != key):
            raise ValidationError({'step': 'All other tasks must be complete before final signoff.'})
        if any(issue['resolved_at'] is None for issue in doc['issues']):
            raise ValidationError({'issues': 'Resolve all reported blockers before final signoff.'})
    proof = build_proof(doc, key, employee_username)
    detail = raised_issue_detail(key, clean)
    issue = None
    if detail is not None:
        issue = {'id': issue_id, 'step_key': key, 'detail': detail, 'resolution': None,
                 'resolved_at': None, 'resolved_by': None}
    completion = {'by': by, 'at': now, 'evidence': clean, 'proof': proof, 'request_id': request_id}
    event = {'type': 'completed', 'step_key': key, 'actor': actor['id'], 'at': now,
             'detail': f'{STEPS[key]["title"]} [{proof["mode"]}]'}
    return CompletionDecision(completion=completion, event=event, issue=issue, closes_workflow=key == 'ready')


# Reschedule.

@dataclass(frozen=True)
class RescheduleDecision:
    start_date: str
    due_dates: list
    event: dict


def decide_reschedule(doc, actor, start_date, now):
    """Return None when the date is unchanged (no write, no event)."""
    require_hr(actor, 'Only HR can correct a start date.')
    previous = doc['start_date']
    if previous == start_date.isoformat():
        return None
    due_dates = [due_at(start_date, STEPS[step['key']]['offset']) for step in doc['steps']]
    event = {'type': 'rescheduled', 'step_key': None, 'actor': actor['id'], 'at': now,
             'detail': f'Start date changed from {previous} to {start_date.isoformat()}'}
    return RescheduleDecision(start_date=start_date.isoformat(), due_dates=due_dates, event=event)


# Issue resolution.

def validate_resolution(data):
    resolution = data.get('resolution') if hasattr(data, 'get') else None
    if not hasattr(data, 'keys') or set(data.keys()) != {'resolution'} or not isinstance(resolution, str) \
            or not 10 <= len(resolution.strip()) <= 240:
        raise ValidationError({'resolution': 'Describe the resolution in 10–240 characters; do not include sensitive data.'})
    return resolution.strip()


@dataclass(frozen=True)
class ResolutionDecision:
    issue_id: object
    resolution: str
    event: dict


def decide_resolution(doc, issue_id, actor, data, *, by, now):
    """Return None when the issue is already resolved (v1 replied changed=False before validating input)."""
    require_hr(actor, 'HR records blocker resolution.')
    issue = next((item for item in doc['issues'] if str(item['id']) == str(issue_id)), None)
    if issue is None:
        raise NotFound()
    if issue['resolved_at'] is not None:
        return None
    resolution = validate_resolution(data)
    event = {'type': 'issue_resolved', 'step_key': issue['step_key'], 'actor': actor['id'], 'at': now,
             'detail': 'HR recorded blocker resolution (self-reported)'}
    return ResolutionDecision(issue_id=issue['id'], resolution=resolution, event=event)
