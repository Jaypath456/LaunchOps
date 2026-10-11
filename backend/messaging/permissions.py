from courses.models import Enrollment


def can_access_course_chat(user, course):
    """The chat room's population is exactly: the course's instructor,
    plus every student enrolled in it - the same population
    course_enrolled_students already computes in courses/views.py, and
    the same Enrollment table every other membership check in this
    project uses (no second membership concept for chat).
    """
    if course.instructor_id == user.id:
        return True
    return Enrollment.objects.filter(student=user, course=course).exists()
