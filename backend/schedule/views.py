from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from courses.models import Course, Enrollment
from courses.permissions import IsCourseInstructor, IsInstructor, IsStudent

from .models import Break, Meeting, SavedSchedule, Section, Term
from .serializers import BreakSerializer, SavedScheduleSerializer, SectionSerializer, TermSerializer
from .services import generate_schedules


def _is_section_owner(request, section):
    return IsCourseInstructor().has_object_permission(request, None, section)


# ─── Term Views ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def term_list(request):
    terms = Term.objects.all()
    return Response(TermSerializer(terms, many=True).data)


# ─── Section Views ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def section_list(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    sections = Section.objects.filter(course=course).select_related('term').prefetch_related('meetings')

    term_id = request.query_params.get('term')
    if term_id:
        sections = sections.filter(term_id=term_id)

    return Response(SectionSerializer(sections.order_by('term', 'section_code'), many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def section_create(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    if not IsCourseInstructor().has_object_permission(request, None, course):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    serializer = SectionSerializer(data=request.data)
    if serializer.is_valid():
        section = serializer.save(course=course)
        return Response(SectionSerializer(section).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def section_detail(request, section_id):
    section = get_object_or_404(Section, id=section_id)

    if request.method == 'GET':
        return Response(SectionSerializer(section).data)

    if not _is_section_owner(request, section):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'PUT':
        serializer = SectionSerializer(section, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    section.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Break Views (student-owned only) ────────────────────────
# Instructors don't get Breaks - their equivalent "don't double-book me"
# constraint is derived from their own Meetings by the schedule
# generation engine (a later milestone), not a second concept here.

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsStudent])
def break_list_create(request):
    if request.method == 'GET':
        breaks = Break.objects.filter(student=request.user)
        return Response(BreakSerializer(breaks, many=True).data)

    serializer = BreakSerializer(data=request.data)
    if serializer.is_valid():
        brk = serializer.save(student=request.user)
        return Response(BreakSerializer(brk).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, IsStudent])
def break_delete(request, break_id):
    # Scoped by student=request.user in the lookup itself (rather than a
    # fetch-then-check), so a break belonging to another student 404s
    # instead of leaking a 403 that would confirm the id exists.
    brk = get_object_or_404(Break, id=break_id, student=request.user)
    brk.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Schedule Generation (common to both roles) ────────────────
# One endpoint, one pure function (schedule.services.generate_schedules)
# for both students and instructors - only the source of "blocked
# intervals" differs by role, exactly per the architecture design: a
# student's blocks are their Breaks; an instructor's blocks are their own
# other Meetings, since instructors don't get a Break-like model.

def _section_to_candidate(section):
    return {
        'id': section.id,
        'meetings': [(m.day_of_week, m.start_time, m.end_time) for m in section.meetings.all()],
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_schedule(request):
    course_ids = request.data.get('course_ids')
    term_id = request.data.get('term_id')

    if not term_id:
        return Response({'error': 'term_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(course_ids, list) or not course_ids:
        return Response({'error': 'course_ids must be a non-empty list'}, status=status.HTTP_400_BAD_REQUEST)

    term = get_object_or_404(Term, id=term_id)

    course_groups = []
    for course_id in course_ids:
        sections = (
            Section.objects.filter(course_id=course_id, term=term)
            .prefetch_related('meetings')
        )
        course_groups.append([_section_to_candidate(s) for s in sections])

    if request.user.role == 'instructor':
        # Block the instructor's own meetings for their OTHER courses in
        # this term, so they don't schedule a clash with their existing
        # teaching. Courses currently being scheduled are excluded so
        # editing a course's own sections never self-conflicts.
        own_other_meetings = Meeting.objects.filter(
            section__course__instructor=request.user,
            section__term=term,
        ).exclude(section__course_id__in=course_ids)
        blocked_intervals = [
            (m.day_of_week, m.start_time, m.end_time) for m in own_other_meetings
        ]
    else:
        blocked_intervals = [
            (b.day_of_week, b.start_time, b.end_time)
            for b in Break.objects.filter(student=request.user)
        ]

    combinations = generate_schedules(course_groups, blocked_intervals=blocked_intervals)

    # Serialize each unique section exactly once, regardless of how many
    # combinations reference it, instead of re-serializing per-combination.
    all_section_ids = {candidate['id'] for group in course_groups for candidate in group}
    sections_qs = (
        Section.objects.filter(id__in=all_section_ids)
        .select_related('course', 'term')
        .prefetch_related('meetings')
    )
    serialized_by_id = {s.id: SectionSerializer(s).data for s in sections_qs}

    schedules = [
        [serialized_by_id[candidate['id']] for candidate in combination]
        for combination in combinations
    ]

    return Response({'count': len(schedules), 'schedules': schedules})


# ─── Saved Schedules (student's chosen candidate) ────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsStudent])
def saved_schedule_list_create(request):
    if request.method == 'GET':
        saved = (
            SavedSchedule.objects.filter(student=request.user)
            .select_related('term')
            .prefetch_related('sections__meetings', 'sections__course')
        )
        return Response(SavedScheduleSerializer(saved, many=True).data)

    serializer = SavedScheduleSerializer(data=request.data)
    if serializer.is_valid():
        saved = serializer.save(student=request.user)
        return Response(SavedScheduleSerializer(saved).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsStudent])
def saved_schedule_confirm(request, saved_schedule_id):
    """Turns a chosen candidate into real Enrollments - the same
    Enrollment table that already gates chapters/quizzes/chat. Uses
    get_or_create per section's course, so confirming the same
    SavedSchedule twice (double click, retried request) never creates
    duplicate Enrollment rows - Enrollment's own unique_together
    (student, course) is the actual guarantee, matching the idempotency
    pattern already used for quiz submissions.
    """
    saved = get_object_or_404(SavedSchedule, id=saved_schedule_id, student=request.user)

    for section in saved.sections.select_related('course').all():
        Enrollment.objects.get_or_create(student=request.user, course=section.course)

    if saved.confirmed_at is None:
        saved.confirmed_at = timezone.now()
        saved.save(update_fields=['confirmed_at'])

    return Response(SavedScheduleSerializer(saved).data)
