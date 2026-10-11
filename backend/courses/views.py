from django.conf import settings
from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from lms_project.safe_cache import safe_delete_pattern, safe_get, safe_set
from .models import Course, Chapter, Enrollment
from .pagination import StandardResultsPagination
from .serializers import (
    CourseSerializer, 
    ChapterSerializer, 
    EnrollmentSerializer
)
from .permissions import IsInstructor, IsStudent, IsCourseInstructor, IsEnrolled


def _with_course_counts(queryset):
    """Annotates chapter_count/enrolled_count on a Course queryset with a
    single GROUP BY query, instead of CourseSerializer issuing two COUNT
    queries per course (see CourseSerializer.get_chapter_count/
    get_enrolled_count for the fallback used when a queryset isn't
    annotated, e.g. nested course objects reached via my_courses)."""
    return queryset.annotate(
        chapter_count=Count('chapters', distinct=True),
        enrolled_count=Count('enrollments', distinct=True),
    )


# ─── Course Views ─────────────────────────────────────────────

# Short TTL: this is a read-heavy, low-staleness-tolerance cache (a newly
# published/unpublished course should show up reasonably quickly), not a
# long-lived cache. Invalidated explicitly on every write path that can
# change catalog membership (course_create, course_detail's PUT/DELETE)
# so the TTL is really just a safety net, not the primary consistency
# mechanism.
CATALOG_CACHE_KEY = 'course_catalog:published'
CATALOG_CACHE_TTL_SECONDS = 60


def _invalidate_catalog_cache():
    # Wipes every cached page/page_size variant of the catalog (see
    # course_list's cache_key construction), not just the plain
    # CATALOG_CACHE_KEY used for the default first page.
    safe_delete_pattern(f'{CATALOG_CACHE_KEY}*')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def course_list(request):
    # See settings.LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS - this branch
    # exists purely so loadtests/ can measure this endpoint with the
    # exact same code path minus caching, never for production use.
    caching_enabled = not settings.LOAD_TEST_DISABLE_REDIS_OPTIMIZATIONS

    # The cached payload is the *paginated* envelope (count/next/previous/
    # results), so the cache key has to vary by page/page_size too -
    # otherwise a page-2 request could be served page 1's cached response.
    # The overwhelmingly common case (first page, default size) still
    # collapses onto the plain CATALOG_CACHE_KEY used by the rest of this
    # module (invalidation, tests).
    page_param = request.query_params.get('page')
    page_size_param = request.query_params.get('page_size')
    cache_key = CATALOG_CACHE_KEY
    if page_param or page_size_param:
        cache_key = f'{CATALOG_CACHE_KEY}:page={page_param or "1"}:size={page_size_param or ""}'

    if caching_enabled:
        cached = safe_get(cache_key)
        if cached is not None:
            return Response(cached)

    # Course.Meta.ordering (-created_at) isn't a unique key - courses
    # created in the same request/transaction can share a timestamp,
    # which would make paginated results unstable across page fetches
    # (Django warns about this: UnorderedObjectListWarning). Adding `id`
    # as a tiebreaker keeps ordering fully deterministic without touching
    # the model's default ordering.
    courses = _with_course_counts(
        Course.objects.filter(is_published=True)
        .select_related('instructor', 'term')
        .order_by('-created_at', 'id')
    )

    paginator = StandardResultsPagination()
    page = paginator.paginate_queryset(courses, request)
    serializer = CourseSerializer(page, many=True)
    response = paginator.get_paginated_response(serializer.data)

    if caching_enabled:
        safe_set(cache_key, response.data, CATALOG_CACHE_TTL_SECONDS)

    return response


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsInstructor])
def instructor_courses(request):
    # Deliberately not paginated: the Dashboard/CourseList frontend pages
    # compute aggregate stats (total published, total students) across the
    # instructor's *entire* course list, and an instructor's own course
    # count is bounded by what they personally author - unlike the public
    # catalog, this isn't a platform-wide growth concern.
    courses = _with_course_counts(
        Course.objects.filter(instructor=request.user).select_related('instructor', 'term')
    )
    serializer = CourseSerializer(courses, many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def course_create(request):
    serializer = CourseSerializer(data=request.data)
    if serializer.is_valid():
        course = serializer.save(instructor=request.user)
        # A new course could be created already-published, so the
        # catalog cache must be invalidated unconditionally rather than
        # only when is_published=True.
        _invalidate_catalog_cache()
        # Re-fetch through the annotated queryset so the response uses the
        # same annotated code path as every other CourseSerializer usage,
        # instead of relying on the fallback .count() queries for this one
        # case (a freshly created course has 0 chapters/enrollments either
        # way, but this keeps the behavior consistent and query-cheap).
        course = _with_course_counts(Course.objects.filter(pk=course.pk)).get()
        return Response(CourseSerializer(course).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def course_detail(request, course_id):
    course = get_object_or_404(
        _with_course_counts(Course.objects.select_related('instructor', 'term')), id=course_id
    )

    is_owner = IsCourseInstructor().has_object_permission(request, None, course)

    if request.method == 'GET':
        if not course.is_published and not is_owner:
            return Response({'error': 'This course is not available'}, status=status.HTTP_403_FORBIDDEN)
        return Response(CourseSerializer(course).data)

    # Permission check for modifying/deleting
    if not is_owner:
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'PUT':
        serializer = CourseSerializer(course, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            # Covers both is_published toggling and title/description
            # edits, which the cached catalog response also contains.
            _invalidate_catalog_cache()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    course.delete()
    _invalidate_catalog_cache()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Chapter Views ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def chapter_list(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    if course.instructor == request.user:
        chapters = Chapter.objects.filter(course=course)
    elif course.is_published:
        chapters = Chapter.objects.filter(course=course, visibility='public')
    else:
        return Response({'error': 'This course is not available'}, status=status.HTTP_403_FORBIDDEN)

    serializer = ChapterSerializer(chapters.order_by('order_index', 'id'), many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsInstructor])
def chapter_create(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    if not IsCourseInstructor().has_object_permission(request, None, course):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    serializer = ChapterSerializer(data=request.data)
    if serializer.is_valid():
        chapter = serializer.save(course=course)
        return Response(ChapterSerializer(chapter).data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def chapter_detail(request, chapter_id):
    chapter = get_object_or_404(Chapter, id=chapter_id)
    is_owner = IsCourseInstructor().has_object_permission(request, None, chapter)

    if request.method == 'GET':
        if is_owner:
            return Response(ChapterSerializer(chapter).data)

        is_enrolled = IsEnrolled().has_object_permission(request, None, chapter)
        if chapter.course.is_published and chapter.visibility == 'public' and is_enrolled:
            return Response(ChapterSerializer(chapter).data)

        return Response({'error': 'This chapter is not available'}, status=status.HTTP_403_FORBIDDEN)

    if not is_owner:
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'PUT':
        serializer = ChapterSerializer(chapter, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    chapter.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Enrollment Views ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def my_courses(request):
    enrollments = Enrollment.objects.filter(student=request.user).select_related(
        'course', 'course__instructor', 'course__term'
    )
    serializer = EnrollmentSerializer(enrollments, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsStudent])
def enrollment_status(request, course_id):
    enrolled = Enrollment.objects.filter(student=request.user, course_id=course_id).exists()
    return Response({'enrolled': enrolled})

@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated, IsStudent])
def manage_enrollment(request, course_id):
    if request.method == 'POST':
        # Only published courses can be joined - draft courses are not
        # available to students even if they know the course id.
        course = get_object_or_404(Course, id=course_id, is_published=True)
        Enrollment.objects.get_or_create(student=request.user, course=course)
        return Response({"message": "Enrolled"}, status=status.HTTP_201_CREATED)

    # DELETE: allow unenrolling even if the course was unpublished after
    # the student joined.
    course = get_object_or_404(Course, id=course_id)
    Enrollment.objects.filter(student=request.user, course=course).delete()
    return Response({"message": "Unenrolled"}, status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsInstructor])
def course_enrolled_students(request, course_id):
    course = get_object_or_404(Course, id=course_id, instructor=request.user)
    # Get all enrollments for this course
    enrollments = Enrollment.objects.filter(course=course).select_related('student')

    # Extract student data
    data = [{
        "id": e.student.id,
        "name": e.student.username,
        "email": e.student.email,
    } for e in enrollments]

    return Response(data)