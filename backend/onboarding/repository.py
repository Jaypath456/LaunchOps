"""The only writer of workflow_instances business state (spec section 5).

Every business mutation is a compare-and-set: read the document, build a pure plan, then one
conditional update_one guarded by `version` (and an `$elemMatch` on the touched array element).
Events are $pushed in the same write. Success is `matched_count == 1`; otherwise re-read and retry.
Provisioning job transitions use their own element status guard and never change the workflow version.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from bson import Int64, ObjectId
from pymongo import WriteConcern
from pymongo.errors import DuplicateKeyError
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from . import domain
from .blueprint import BLUEPRINT_VERSION, CONNECTORS
from .db import get_db, get_users


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'This workflow changed concurrently; please retry.'
    default_code = 'conflict'


class DuplicateWorkflow(Exception):
    """The unique employee_id index rejected a second workflow for the same employee."""


def instances():
    return get_db().get_collection('workflow_instances', write_concern=WriteConcern(w='majority'))


def now_ms():
    """UTC now truncated to MongoDB's millisecond precision, so returned values equal stored ones."""
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


# Compare-and-set core.

@dataclass
class Plan:
    result: object = None
    update: dict | None = None
    array_filters: list | None = None
    guard: dict = field(default_factory=dict)
    noop: bool = False


@dataclass(frozen=True)
class Outcome:
    result: object
    attempts: int
    replayed: bool


def _between_read_and_write():
    """No-op; tests patch it to inject a competing write between the read and the conditional write."""


def guard_filter(doc, plan):
    return {'_id': doc['_id'], 'version': doc['version'], **plan.guard}


def mutate(instance_id, build, retries=5):
    collection = instances()
    for attempt in range(1, retries + 1):
        doc = collection.find_one({'_id': instance_id})
        if doc is None:
            raise NotFound()
        _between_read_and_write()
        plan = build(doc)
        if plan.noop:
            return Outcome(plan.result, attempt, True)
        result = collection.update_one(
            guard_filter(doc, plan), {**plan.update, '$inc': {'version': 1}},
            array_filters=plan.array_filters,
        )
        if result.matched_count == 1:
            return Outcome(plan.result, attempt, False)
    raise Conflict()


# Reads.

def get_workflow(instance_id):
    return instances().find_one({'_id': instance_id})


class WorkflowList:
    """Lazy, role-scoped workflow sequence for Django's paginator: count + one sliced find."""

    def __init__(self, query):
        self.query = query

    def count(self):
        return instances().count_documents(self.query)

    def __len__(self):
        return self.count()

    def __getitem__(self, item):
        if not isinstance(item, slice) or item.step not in (None, 1):
            raise TypeError('WorkflowList supports contiguous slices only.')
        start = item.start or 0
        if item.stop is not None and item.stop <= start:
            return []
        # Ordered by the unique _id (not by a timestamp) so pages never overlap or skip.
        cursor = instances().find(self.query).sort('_id', -1).skip(start)
        if item.stop is not None:
            cursor = cursor.limit(item.stop - start)
        return list(cursor)


def employee_filter(employee_id):
    # The unique employee index is partial ($type objectId); the planner only uses it when the query says so.
    return {'employee_id': {'$eq': employee_id, '$type': 'objectId'}}


def find_by_employee(employee_id):
    return instances().find_one(employee_filter(employee_id), {'_id': 1})


def list_workflows(actor):
    scope = domain.list_scope(actor)
    if scope is None:
        return WorkflowList({})
    if scope == 'employee_id':
        return WorkflowList(employee_filter(ObjectId(actor['id'])))
    return WorkflowList({scope: ObjectId(actor['id'])})


def users_by_id(ids):
    """One $in lookup for every referenced user; never one query per document."""
    wanted = set()
    for value in ids:
        if isinstance(value, ObjectId):
            wanted.add(value)
        elif isinstance(value, str) and ObjectId.is_valid(value) and len(value) == 24:
            wanted.add(ObjectId(value))
    if not wanted:
        return {}
    cursor = get_users().find({'_id': {'$in': list(wanted)}}, {'username': 1, 'display_name': 1, 'role': 1})
    return {str(user['_id']): user for user in cursor}


def referenced_user_ids(docs):
    for doc in docs:
        yield doc['employee_id']
        yield doc['manager_id']
        for step in doc['steps']:
            if step.get('completion'):
                yield step['completion'].get('by')
        for event in doc['events']:
            yield event.get('actor')


def directory_users():
    cursor = get_users().find({'role': {'$in': ['employee', 'manager']}}, {'username': 1, 'role': 1}).sort('username', 1)
    return list(cursor)


# Writes.

def create_workflow(*, actor, employee_id, manager_id, job_title, department, start_date):
    found = users_by_id([employee_id, manager_id])
    employee = found.get(str(employee_id))
    manager = found.get(str(manager_id))
    errors = {}
    if employee is None or employee.get('role') != 'employee':
        errors['employee_id'] = [f'Invalid pk "{employee_id}" - object does not exist.']
    if manager is None or manager.get('role') != 'manager':
        errors['manager_id'] = [f'Invalid pk "{manager_id}" - object does not exist.']
    if errors:
        raise ValidationError(errors)
    collection = instances()
    if find_by_employee(employee['_id']) is not None:
        raise ValidationError({'employee_id': ['This employee already has an onboarding workflow.']})

    instance_id = ObjectId()
    now = now_ms()
    payload = {'username': employee['username'], 'display_name': employee.get('display_name') or employee['username'],
               'department': department}
    doc = {
        '_id': instance_id,
        'blueprint_version': BLUEPRINT_VERSION,
        'employee_id': employee['_id'],
        'manager_id': manager['_id'],
        'created_by': ObjectId(actor['id']),
        'job_title': job_title,
        'department': department,
        'start_date': start_date.isoformat(),
        'status': 'active',
        'created_at': now,
        'version': 1,
        'steps': [
            {'key': key, 'status': 'open', 'due_at': domain.due_at(start_date, domain.STEPS[key]['offset']),
             'completion': None}
            for key in domain.STEP_ORDER
        ],
        'issues': [],
        'events': [],
        'provisioning': [
            {'connector': connector, 'idempotency_key': f'{instance_id}:{connector}', 'payload': dict(payload),
             'status': 'pending', 'attempts': 0, 'fence': Int64(0), 'lease_owner': None, 'lease_until': None,
             'next_attempt_at': now, 'last_error': None, 'external_ref': None, 'override_reason': None,
             'updated_at': now}
            for connector in CONNECTORS
        ],
    }
    # The workflow and its pending provisioning jobs are one write: nothing is lost between saved and enqueued.
    try:
        collection.insert_one(doc)
    except DuplicateKeyError as exc:
        raise DuplicateWorkflow() from exc
    return doc


def complete_step(instance_id, key, actor, evidence, request_id=None):
    if key not in domain.STEPS:
        raise NotFound()
    employee_username = None
    if key == 'profile':
        # The profile proof records the employee's username (v1 behavior); look it up once, outside the pure build.
        current = instances().find_one({'_id': instance_id}, {'employee_id': 1})
        if current is None:
            raise NotFound()
        employee = get_users().find_one({'_id': current['employee_id']}, {'username': 1})
        employee_username = employee['username'] if employee else None
    actor_oid = ObjectId(actor['id'])

    def build(doc):
        decision = domain.decide_completion(
            doc, key, actor, evidence, by=actor_oid, request_id=request_id, now=now_ms(),
            employee_username=employee_username, issue_id=ObjectId(),
        )
        if decision.replay:
            return Plan(result=decision.completion, noop=True)
        if key == 'access':
            # Jobs do not bump version. Terminal job states cannot regress, so this
            # snapshot can only reject conservatively while provisioning finishes.
            if any(job['status'] not in ('succeeded', 'overridden') for job in doc['provisioning']):
                raise ValidationError({'provisioning': 'Every job must succeed or be overridden before completing access.'})
            decision.completion['evidence']['provisioning'] = [
                {field: job[field] for field in ('connector', 'status', 'external_ref', 'override_reason')}
                for job in doc['provisioning']
            ]
        update = {
            '$set': {'steps.$[s].status': 'done', 'steps.$[s].completion': decision.completion},
            '$push': {'events': decision.event},
        }
        if decision.issue is not None:
            update['$push']['issues'] = decision.issue
        if decision.closes_workflow:
            update['$set']['status'] = 'ready'
        # arrayFilters alone is not a guard: the $elemMatch in the query filter is what rejects a done step.
        return Plan(result=decision.completion, update=update, array_filters=[{'s.key': key}],
                    guard={'steps': {'$elemMatch': {'key': key, 'status': 'open'}}})

    return mutate(instance_id, build)


def _change_job(instance_id, connector, actor, allowed, changes, event_type, now):
    if actor['role'] != 'it':
        raise PermissionDenied('Only IT can retry or override provisioning jobs.')
    event = {'type': event_type, 'step_key': 'access', 'connector': connector,
             'actor': actor['id'], 'at': now, 'detail': f'{connector}: IT requested a manual retry'}
    if 'override_reason' in changes:
        event['detail'] = f"{connector}: overridden — {changes['override_reason']}"
    collection = instances()
    result = collection.update_one(
        {'_id': instance_id, 'provisioning': {'$elemMatch': {'connector': connector, 'status': allowed}}},
        {'$set': {f'provisioning.$[j].{key}': value for key, value in {**changes, 'updated_at': now}.items()},
         '$push': {'events': event}},
        array_filters=[{'j.connector': connector}],
    )
    if result.matched_count != 1:
        if collection.find_one({'_id': instance_id}, {'_id': 1}) is None:
            raise NotFound()
        raise Conflict('This provisioning job cannot make that transition; refresh and try again.')
    return {'connector': connector, 'status': changes['status']}


def retry_job(instance_id, connector, actor):
    now = now_ms()
    return _change_job(instance_id, connector, actor, 'dead',
                       {'status': 'pending', 'attempts': 0, 'next_attempt_at': now},
                       'provisioning.manual_retry', now)


def override_job(instance_id, connector, actor, reason):
    if actor['role'] != 'it':
        raise PermissionDenied('Only IT can retry or override provisioning jobs.')
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError({'reason': 'A non-empty override reason is required.'})
    return _change_job(instance_id, connector, actor, {'$in': ['pending', 'retry', 'dead']},
                       {'status': 'overridden', 'override_reason': reason.strip()},
                       'provisioning.overridden', now_ms())


def reschedule(instance_id, actor, start_date):
    def build(doc):
        decision = domain.decide_reschedule(doc, actor, start_date, now_ms())
        if decision is None:
            return Plan(result=False, noop=True)
        # Positional paths are safe: the version guard pins this exact snapshot, and steps never reorder.
        changes = {'start_date': decision.start_date}
        changes.update({f'steps.{index}.due_at': due for index, due in enumerate(decision.due_dates)})
        return Plan(result=True, update={'$set': changes, '$push': {'events': decision.event}})

    return mutate(instance_id, build)


def resolve_issue(instance_id, issue_id, actor, data):
    actor_oid = ObjectId(actor['id'])

    def build(doc):
        now = now_ms()
        decision = domain.decide_resolution(doc, issue_id, actor, data, by=actor_oid, now=now)
        if decision is None:
            return Plan(result={'changed': False}, noop=True)
        update = {
            '$set': {'issues.$[i].resolution': decision.resolution, 'issues.$[i].resolved_at': now,
                     'issues.$[i].resolved_by': actor_oid},
            '$push': {'events': decision.event},
        }
        return Plan(result={'changed': True}, update=update, array_filters=[{'i.id': decision.issue_id}],
                    guard={'issues': {'$elemMatch': {'id': decision.issue_id, 'resolved_at': None}}})

    return mutate(instance_id, build)
