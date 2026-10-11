from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from courses.models import Course, Chapter
from quizzes.models import Quiz, Submission
from django.db import transaction
from .models import Onboarding, Step, Event, LessonReview, AssessmentAttempt, Issue
from .serializers import CreateOnboardingSerializer, OnboardingSerializer, TaskStepSerializer, RescheduleSerializer
from .services import create_onboarding, complete_step, can_view, reschedule, valid_start_date
from .blueprint import HANDBOOK, HANDBOOK_VERSION
from django.utils import timezone
from rest_framework.exceptions import ValidationError


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
        return Response(OnboardingSerializer(visible(request.user)[:100], many=True).data)
    if request.user.role != 'hr':
        raise PermissionDenied('Only HR can start onboarding.')
    serializer = CreateOnboardingSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    try:
        record = create_onboarding(employee=data['employee'], manager=data['manager'], creator=request.user,
                                   job_title=data['job_title'], department=data['department'], start_date=data['start_date'],
                                   course=data.get('training_course'))
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
        with transaction.atomic():
            locked = Onboarding.objects.select_for_update().get(pk=record.pk)
            if 'training_course_id' in serializer.validated_data:
                course = serializer.validated_data['training_course_id']
                if course.pk != locked.training_course_id:
                    if locked.steps.filter(key='training', completed_at__isnull=False).exists():
                        raise ValidationError({'training_course_id': 'Completed training is preserved; its track cannot be replaced.'})
                    if not course.chapters.filter(visibility='public').exists() or not course.quizzes.filter(is_published=True).exists():
                        raise ValidationError({'training_course_id': 'Choose a track with public lessons and published assessments.'})
                    from courses.models import Enrollment
                    locked.training_course = course
                    locked.save(update_fields=['training_course'])
                    Enrollment.objects.get_or_create(course=course, student=locked.employee)
                    Event.objects.create(onboarding=locked, actor=request.user, action='track_assigned', detail=f'Learning track assigned: {course.title}'[:240])
            if 'start_date' in serializer.validated_data:
                reschedule(record.pk, request.user, serializer.validated_data['start_date'])
        record = visible(request.user).get(pk=pk)
    return Response(OnboardingSerializer(record).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def step_detail(request, pk):
    step = get_object_or_404(Step.objects.select_related('onboarding__employee', 'onboarding__manager', 'completed_by').prefetch_related('prerequisites'), pk=pk)
    if not can_view(request.user, step.onboarding):
        return Response({'detail': 'Not found.'}, status=404)
    progress = []
    if step.key == 'training' and request.user.pk == step.onboarding.employee_id and step.onboarding.training_course_id:
        quizzes = list(Quiz.objects.filter(course_id=step.onboarding.training_course_id, is_published=True).values('id', 'title'))
        results = {row['quiz_id']: row for row in Submission.objects.filter(student=request.user, quiz_id__in=[quiz['id'] for quiz in quizzes]).values('quiz_id', 'score', 'max_score')}
        progress = [{**quiz, 'score': results[quiz['id']]['score'] if quiz['id'] in results else None,
                     'max_score': results[quiz['id']]['max_score'] if quiz['id'] in results else None,
                     'passed': bool(quiz['id'] in results and results[quiz['id']]['max_score'] and
                                    results[quiz['id']]['score'] * 100 >= results[quiz['id']]['max_score'] * 70)}
                    for quiz in quizzes]
    record = step.onboarding
    reviews = set(record.lesson_reviews.values_list('chapter_id', flat=True)) if step.key == 'training' else set()
    lessons = [{'id': c.id, 'title': c.title, 'reviewed': c.id in reviews} for c in
               Chapter.objects.filter(course_id=record.training_course_id, visibility='public')] if step.key == 'training' else []
    related = record.steps.select_related('completed_by').prefetch_related('prerequisites')
    # Final and manager reviews expose the actual saved artifacts, not just checkboxes.
    review_keys = {'manager_signoff': ['goals', 'calendar'], 'receipt': ['equipment', 'it_handoff'],
                   'endpoint': ['equipment'], 'mfa': ['access'], 'it_handoff': ['equipment', 'mfa', 'endpoint']}
    review = related.exclude(pk=step.pk) if step.key == 'ready' else related.filter(key__in=review_keys.get(step.key, []))
    return Response({'step': TaskStepSerializer(step).data,
        'dependencies': [{'id': p.id, 'title': p.title, 'completed': bool(p.completed_at)} for p in step.prerequisites.all()],
        'review': TaskStepSerializer(review, many=True).data,
        'lessons': lessons, 'handbook': {'version': HANDBOOK_VERSION, 'sections': HANDBOOK} if step.key in ('policy', 'handbook') else None,
        'issues': list(record.issues.values('id', 'detail', 'resolution', 'resolved_at')) if step.key == 'ready' else [], 'assessments': progress, 'workflow': {
        'id': step.onboarding_id, 'employee': step.onboarding.employee.username,
        'manager': record.manager.username, 'training_course_id': record.training_course_id,
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
    return Response({'users': list(users.values('id', 'username', 'role')),
                     'tracks': list(Course.objects.filter(is_published=True).values('id', 'title'))})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def retry_assessment(request, quiz_id):
    if request.user.role != 'employee':
        raise PermissionDenied()
    quiz = get_object_or_404(Quiz, pk=quiz_id, is_published=True)
    with transaction.atomic():
        record = get_object_or_404(Onboarding.objects.select_for_update(), employee=request.user,
                                   training_course_id=quiz.course_id)
        training = record.steps.get(key='training')
        if training.completed_at:
            return Response({'detail': 'Training is already complete.'}, status=409)
        submission = Submission.objects.select_for_update().filter(quiz=quiz, student=request.user).first()
        if not submission:
            return Response({'detail': 'There is no attempt to retry.'}, status=409)
        if submission.max_score and submission.score * 100 >= submission.max_score * 70:
            return Response({'detail': 'This assessment has already been passed.'}, status=409)
        Event.objects.create(onboarding=record, step=training, actor=request.user,
                             action='assessment_retry', detail=f'{quiz.title}: previous score {submission.score}/{submission.max_score}')
        AssessmentAttempt.objects.create(onboarding=record, quiz=quiz, answers=submission.answers,
            score=submission.score, max_score=submission.max_score, submitted_at=submission.submitted_at)
        submission.delete()
    return Response({'detail': 'You can retake this assessment now.'})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def lesson_review(request, chapter_id):
    if request.user.role != 'employee':
        raise PermissionDenied()
    chapter = get_object_or_404(Chapter, pk=chapter_id, visibility='public')
    with transaction.atomic():
        record = get_object_or_404(Onboarding.objects.select_for_update(), employee=request.user, training_course_id=chapter.course_id)
        review = LessonReview.objects.filter(onboarding=record, chapter=chapter).first()
        if request.method == 'POST' and not review:
            if request.data != {'acknowledged': True}:
                raise ValidationError({'acknowledged': 'Explicit acknowledgment is required.'})
            review = LessonReview.objects.create(onboarding=record, chapter=chapter)
            Event.objects.create(onboarding=record, actor=request.user, step=record.steps.get(key='training'),
                                 action='lesson_acknowledged', detail=f'Lesson acknowledged: {chapter.title}'[:240])
    return Response({'reviewed': review is not None})


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
