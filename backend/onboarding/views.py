from datetime import UTC, datetime

from pymongo.errors import PyMongoError
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from . import domain, repository
from .auth import get_actor
from .db import get_db
from .pagination import WorkflowPagination
from .serializers import (
    CreateWorkflowSerializer, RescheduleSerializer, clean_idempotency_key, completion_data, events_in_order,
    step_detail_data, workflow_data,
)


def _now():
    return datetime.now(UTC)


def _visible_workflow(actor, workflow_id):
    doc = repository.get_workflow(workflow_id)
    if doc is None or not domain.can_view(actor, doc):
        raise NotFound()
    return doc


def _workflow_response(doc, status=200):
    users = repository.users_by_id(repository.referenced_user_ids([doc]))
    return Response(workflow_data(doc, users, _now()), status=status)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def workflows(request):
    actor = get_actor(request)
    if request.method == 'GET':
        paginator = WorkflowPagination()
        page = paginator.paginate_queryset(repository.list_workflows(actor), request)
        users = repository.users_by_id(repository.referenced_user_ids(page))
        now = _now()
        return paginator.get_paginated_response([workflow_data(doc, users, now) for doc in page])
    domain.require_hr(actor, 'Only HR can start onboarding.')
    serializer = CreateWorkflowSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    try:
        doc = repository.create_workflow(actor=actor, employee_id=data['employee_id'], manager_id=data['manager_id'],
                                         job_title=data['job_title'], department=data['department'],
                                         start_date=data['start_date'])
    except repository.DuplicateWorkflow:
        return Response({'employee_id': 'This employee already has an onboarding workflow.'}, status=409)
    return _workflow_response(doc, status=201)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def detail(request, workflow_id):
    actor = get_actor(request)
    doc = _visible_workflow(actor, workflow_id)
    if request.method == 'PATCH':
        domain.require_hr(actor, 'Only HR can correct a start date.')
        serializer = RescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        repository.reschedule(workflow_id, actor, serializer.validated_data['start_date'])
        doc = _visible_workflow(actor, workflow_id)
    return _workflow_response(doc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def step_detail(request, workflow_id, key):
    actor = get_actor(request)
    if key not in domain.STEPS:
        raise NotFound()
    doc = _visible_workflow(actor, workflow_id)
    users = repository.users_by_id(repository.referenced_user_ids([doc]))
    return Response(step_detail_data(doc, key, users, _now()))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete(request, workflow_id, key):
    actor = get_actor(request)
    request_id = clean_idempotency_key(request.headers.get('Idempotency-Key'))
    evidence = request.data.get('evidence') if hasattr(request.data, 'get') else None
    outcome = repository.complete_step(workflow_id, key, actor, evidence, request_id)
    # Winner and replays get identical bodies; a replay is flagged only by this header.
    headers = {'Idempotent-Replayed': 'true'} if outcome.replayed else None
    return Response({'key': key, 'state': 'done', 'completion': completion_data(outcome.result)}, headers=headers)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resolve_issue(request, workflow_id, issue_id):
    actor = get_actor(request)
    domain.require_hr(actor, 'HR records blocker resolution.')
    outcome = repository.resolve_issue(workflow_id, issue_id, actor, request.data)
    return Response(outcome.result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def retry_job(request, workflow_id, connector):
    return Response(repository.retry_job(workflow_id, connector, get_actor(request)))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def override_job(request, workflow_id, connector):
    reason = request.data.get('reason') if hasattr(request.data, 'get') else None
    return Response(repository.override_job(workflow_id, connector, get_actor(request), reason))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def timeline(request, workflow_id):
    actor = get_actor(request)
    doc = _visible_workflow(actor, workflow_id)
    users = repository.users_by_id(repository.referenced_user_ids([doc]))
    return Response({'id': str(doc['_id']), 'events': events_in_order(doc, users)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def directory(request):
    actor = get_actor(request)
    if actor['role'] != 'hr':
        raise PermissionDenied()
    users = repository.directory_users()
    return Response({'users': [{'id': str(user['_id']), 'username': user['username'], 'role': user['role']}
                               for user in users]})


@api_view(['GET'])
@authentication_classes([])
@permission_classes([AllowAny])
def healthz(request):
    try:
        get_db().command('ping')
    except PyMongoError:
        return Response({'status': 'unavailable'}, status=503)
    return Response({'status': 'ok'})
