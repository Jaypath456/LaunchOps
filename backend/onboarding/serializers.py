"""Plain DRF serializers for input and dict builders for output (shapes kept close to v1)."""

from datetime import timedelta

from bson import ObjectId
from django.utils import timezone
from rest_framework import serializers

from . import domain
from .blueprint import FORMS, HANDBOOK, HANDBOOK_VERSION


_datetime = serializers.DateTimeField()


def iso(value):
    return None if value is None else _datetime.to_representation(value)


def validate_start_date(value):
    today = timezone.localdate()
    if not today - timedelta(days=366) <= value <= today + timedelta(days=1095):
        raise serializers.ValidationError('Choose a start date within the past year or next three years.')
    return value


class ObjectIdField(serializers.Field):
    default_error_messages = {
        'incorrect_type': 'Incorrect type. Expected pk value, received {data_type}.',
        'does_not_exist': 'Invalid pk "{pk_value}" - object does not exist.',
    }

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('incorrect_type', data_type=type(data).__name__)
        if len(data) != 24 or not ObjectId.is_valid(data):
            self.fail('does_not_exist', pk_value=data)
        return ObjectId(data)

    def to_representation(self, value):
        return str(value)


class RescheduleSerializer(serializers.Serializer):
    start_date = serializers.DateField(validators=[validate_start_date], required=False)

    def validate(self, data):
        if not data:
            raise serializers.ValidationError('Supply a start date.')
        return data


class CreateWorkflowSerializer(serializers.Serializer):
    employee_id = ObjectIdField()
    manager_id = ObjectIdField()
    job_title = serializers.CharField(max_length=160)
    department = serializers.CharField(max_length=100)
    start_date = serializers.DateField(validators=[validate_start_date])


IDEMPOTENCY_KEY_MAX = 255


def clean_idempotency_key(value):
    if value is None:
        return None
    if not 1 <= len(value) <= IDEMPOTENCY_KEY_MAX or any(not 32 < ord(ch) < 127 for ch in value):
        raise serializers.ValidationError({'Idempotency-Key': f'Use 1-{IDEMPOTENCY_KEY_MAX} printable ASCII characters.'})
    return value


# Output builders. `users` maps a user id hex string to its user document (one $in lookup per request).

def username(users, user_id):
    if user_id is None:
        return None
    user = users.get(str(user_id))
    return user['username'] if user else None


def actor_name(users, actor):
    # Django events store a user id; provisioner events store "provisioner:<worker>".
    return username(users, actor) or actor


def evidence_with_proof(step):
    completion = step.get('completion')
    if not completion:
        return {}
    evidence = dict(completion.get('evidence') or {})
    if completion.get('proof') is not None:
        evidence['_proof'] = completion['proof']
    return evidence


def step_data(step, steps, users, now):
    key = step['key']
    definition = domain.STEPS[key]
    completion = step.get('completion') or {}
    return {
        'key': key,
        'title': definition['title'],
        'description': definition['description'],
        'owner_role': definition['owner_role'],
        'kind': definition['kind'],
        'due_at': iso(step['due_at']),
        'completed_at': iso(completion.get('at')) if step['status'] == 'done' else None,
        'completed_by': username(users, completion.get('by')) if step['status'] == 'done' else None,
        'prerequisites': list(domain.PREREQUISITES[key]),
        'state': domain.step_state(step, steps),
        'overdue': domain.is_overdue(step, steps, now),
        'completion_mode': domain.completion_mode(key, step),
    }


def task_step_data(step, steps, users, now):
    return {**step_data(step, steps, users, now), 'evidence': evidence_with_proof(step),
            'form_fields': FORMS.get(step['key'], [])}


def event_data(index, event, users):
    return {'id': index, 'actor': actor_name(users, event.get('actor')), 'step': event.get('step_key'),
            'action': event.get('type'), 'detail': event.get('detail'), 'created_at': iso(event.get('at'))}


def events_in_order(doc, users):
    """Array order is event order."""
    return [event_data(index, event, users) for index, event in enumerate(doc['events'])]


def date_needs_correction(doc):
    return not domain.valid_start_date(domain.parse_start_date(doc['start_date']), timezone.localdate())


def workflow_data(doc, users, now):
    steps = domain.steps_by_key(doc)
    return {
        'id': str(doc['_id']),
        'employee': username(users, doc['employee_id']),
        'manager': username(users, doc['manager_id']),
        'job_title': doc['job_title'],
        'department': doc['department'],
        'start_date': doc['start_date'],
        'created_at': iso(doc['created_at']),
        'status': doc['status'],
        'steps': [step_data(step, steps, users, now) for step in doc['steps']],
        # v1 listed events newest first; reverse the array rather than sorting by timestamp.
        'events': list(reversed(events_in_order(doc, users))),
        'date_needs_correction': date_needs_correction(doc),
    }


def completion_data(completion):
    return {'by': None if completion.get('by') is None else str(completion['by']), 'at': iso(completion.get('at')),
            'evidence': completion.get('evidence'), 'proof': completion.get('proof'),
            'request_id': completion.get('request_id')}


def issue_data(issue):
    return {'id': str(issue['id']), 'detail': issue['detail'], 'resolution': issue['resolution'],
            'resolved_at': iso(issue['resolved_at'])}


def provisioning_data(job):
    return {'connector': job['connector'], 'status': job['status'], 'attempts': job['attempts'],
            'last_error': job['last_error'], 'lease_owner': job['lease_owner'],
            'external_ref': job['external_ref'], 'override_reason': job['override_reason'],
            'updated_at': iso(job['updated_at'])}


def step_detail_data(doc, key, users, now):
    steps = domain.steps_by_key(doc)
    step = steps[key]
    if key == 'ready':
        review = [other for other in doc['steps'] if other['key'] != key]
    else:
        wanted = domain.REVIEW_KEYS.get(key, [])
        review = [other for other in doc['steps'] if other['key'] in wanted]
    return {
        'step': task_step_data(step, steps, users, now),
        'dependencies': [{'key': prerequisite, 'title': domain.STEPS[prerequisite]['title'],
                          'completed': steps[prerequisite]['status'] == 'done'}
                         for prerequisite in domain.PREREQUISITES[key]],
        'review': [task_step_data(other, steps, users, now) for other in review],
        'handbook': handbook_data() if key in ('policy', 'handbook') else None,
        'issues': [issue_data(issue) for issue in doc['issues']] if key == 'ready' else [],
        'provisioning': [provisioning_data(job) for job in doc['provisioning']] if key == 'access' else None,
        'workflow': {
            'id': str(doc['_id']), 'employee': username(users, doc['employee_id']),
            'manager': username(users, doc['manager_id']), 'start_date': doc['start_date'],
            'department': doc['department'], 'job_title': doc['job_title'],
            'date_needs_correction': date_needs_correction(doc),
        },
    }


def handbook_data():
    return {'version': HANDBOOK_VERSION, 'sections': HANDBOOK}
