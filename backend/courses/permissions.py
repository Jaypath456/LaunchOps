from rest_framework.permissions import BasePermission

from .models import Course, Enrollment


class IsInstructor(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == 'instructor'
        )


class IsStudent(BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in ('student', 'employee')
        )


class IsCourseInstructor(BasePermission):
    """Object-level check: is the requesting user the instructor who owns
    this object? Works for a ``Course`` itself, or anything exposing a
    ``course`` FK (e.g. ``Chapter``).

    This is not wired into ``permission_classes`` for these function-based
    views (DRF only auto-checks object permissions on generic/class-based
    views). Instead, call it directly wherever an inline ownership check is
    needed, e.g.::

        if not IsCourseInstructor().has_object_permission(request, None, course):
            return Response({'error': 'Permission denied'}, status=403)
    """

    def has_object_permission(self, request, view, obj):
        if isinstance(obj, Course):
            return obj.instructor == request.user

        if hasattr(obj, 'course'):
            return obj.course.instructor == request.user

        return False


class IsEnrolled(BasePermission):
    """Grants access to students enrolled in the relevant course.

    Usable two ways:

    * As a view-level permission when the URL exposes ``course_id``
      (e.g. ``/courses/<course_id>/...``) — add it to ``permission_classes``.
    * As an object-level check for objects exposing ``course`` (e.g.
      ``Chapter``) or a ``Course`` itself — call
      ``IsEnrolled().has_object_permission(request, None, obj)`` directly,
      the same way ``IsCourseInstructor`` is used.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        course_id = view.kwargs.get('course_id')
        if course_id is None:
            # No course_id available in the URL - defer to
            # has_object_permission for the actual check.
            return True

        return Enrollment.objects.filter(
            student=request.user, course_id=course_id
        ).exists()

    def has_object_permission(self, request, view, obj):
        course = obj if isinstance(obj, Course) else getattr(obj, 'course', None)
        if course is None:
            return False

        return Enrollment.objects.filter(
            student=request.user, course=course
        ).exists()
