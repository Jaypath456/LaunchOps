from courses.models import Enrollment


def can_access_course_chat(user, course):
    """The chat room's population is exactly: the course's instructor,
    plus enrolled legacy students. Onboarding training enrollment does not
    grant employees chat access. Course membership uses the population that
    course_enrolled_students already computes in courses/views.py, and
    the same Enrollment table every other membership check in this
    project uses (no second membership concept for chat).
    """
    if user.role == 'instructor' and course.instructor_id == user.id:
        return True
    return user.role == 'student' and Enrollment.objects.filter(student=user, course=course).exists()
