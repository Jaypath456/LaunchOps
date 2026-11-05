from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.db import transaction
from .models import Onboarding, Step, Event, Issue
from .serializers import CreateOnboardingSerializer, OnboardingSerializer, TaskStepSerializer, RescheduleSerializer
from .services import create_onboarding, complete_step, can_view, reschedule, valid_start_date
from .blueprint import HANDBOOK, HANDBOOK_VERSION
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .pagination import WorkflowPagination


def visible(user):
    qs = Onboarding.objects.select_related('employee', 'manager').prefetch_related(
        'steps__prerequisites', 'steps__completed_by', 'events__actor', 'events__step')
    if user.role in ('hr', 'it'):
        return qs
    if user.role == 'manager':
        return qs.filter(manager=user)
    return qs.filter(employee=user)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def onboardings(request):
    if request.method == 'GET':
        paginator = WorkflowPagination()
        page = paginator.paginate_queryset(visible(request.user).order_by('-created_at', '-id'), request)
        return paginator.get_paginated_response(OnboardingSerializer(page, many=True).data)
    if request.user.role != 'hr':
        raise PermissionDenied('Only HR can start onboarding.')
    serializer = CreateOnboardingSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    try:
        record = create_onboarding(employee=data['employee'], manager=data['manager'], creator=request.user,
                                   job_title=data['job_title'], department=data['department'], start_date=data['start_date'])
    except IntegrityError:
        return Response({'employee_id': 'This employee already has an onboarding workflow.'}, status=409)
    return Response(OnboardingSerializer(visible(request.user).get(pk=record.pk)).data, status=201)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def detail(request, pk):
    record = get_object_or_404(visible(request.user), pk=pk)
    if request.method == 'PATCH':
        if request.user.role != 'hr':
            raise PermissionDenied('Only HR can correct a start date.')
        serializer = RescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reschedule(record.pk, request.user, serializer.validated_data['start_date'])
        record = visible(request.user).get(pk=pk)
    return Response(OnboardingSerializer(record).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def step_detail(request, pk):
    step = get_object_or_404(Step.objects.select_related('onboarding__employee', 'onboarding__manager', 'completed_by').prefetch_related('prerequisites'), pk=pk)
    if not can_view(request.user, step.onboarding):
        return Response({'detail': 'Not found.'}, status=404)
    record = step.onboarding
    related = record.steps.select_related('completed_by').prefetch_related('prerequisites')
    # Final and manager reviews expose the actual saved artifacts, not just checkboxes.
    review_keys = {'manager_signoff': ['goals', 'calendar'], 'receipt': ['equipment', 'it_handoff'],
                   'endpoint': ['equipment'], 'mfa': ['access'], 'it_handoff': ['equipment', 'mfa', 'endpoint']}
    review = related.exclude(pk=step.pk) if step.key == 'ready' else related.filter(key__in=review_keys.get(step.key, []))
    return Response({'step': TaskStepSerializer(step).data,
        'dependencies': [{'id': p.id, 'title': p.title, 'completed': bool(p.completed_at)} for p in step.prerequisites.all()],
        'review': TaskStepSerializer(review, many=True).data,
        'handbook': {'version': HANDBOOK_VERSION, 'sections': HANDBOOK} if step.key in ('policy', 'handbook') else None,
        'issues': list(record.issues.values('id', 'detail', 'resolution', 'resolved_at')) if step.key == 'ready' else [], 'workflow': {
        'id': step.onboarding_id, 'employee': step.onboarding.employee.username,
        'manager': record.manager.username,
        'start_date': record.start_date, 'department': record.department, 'job_title': record.job_title,
        'date_needs_correction': not valid_start_date(record.start_date)}})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def complete(request, pk):
    step = get_object_or_404(Step.objects.select_related('onboarding'), pk=pk)
    if not can_view(request.user, step.onboarding):
        raise PermissionDenied()
    step, changed = complete_step(step.pk, request.user, request.data.get('evidence'))
    return Response({'id': step.pk, 'changed': changed, 'state': 'completed'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def directory(request):
    if request.user.role != 'hr':
        raise PermissionDenied()
    users = get_user_model().objects.filter(role__in=['employee', 'manager']).order_by('username')
    return Response({'users': list(users.values('id', 'username', 'role'))})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resolve_issue(request, pk):
    if request.user.role != 'hr':
        raise PermissionDenied('HR records blocker resolution.')
    with transaction.atomic():
        issue = get_object_or_404(Issue, pk=pk)
        Onboarding.objects.select_for_update().get(pk=issue.onboarding_id)
        issue.refresh_from_db()
        if issue.resolved_at:
            return Response({'changed': False})
        resolution = request.data.get('resolution')
        if set(request.data) != {'resolution'} or not isinstance(resolution, str) or not 10 <= len(resolution.strip()) <= 240:
            raise ValidationError({'resolution': 'Describe the resolution in 10–240 characters; do not include sensitive data.'})
        issue.resolution = resolution.strip()
        issue.resolved_by = request.user
        issue.resolved_at = timezone.now()
        issue.save()
        Event.objects.create(onboarding_id=issue.onboarding_id, step=issue.step, actor=request.user,
                             action='issue_resolved', detail='HR recorded blocker resolution (self-reported)')
    return Response({'changed': True})
