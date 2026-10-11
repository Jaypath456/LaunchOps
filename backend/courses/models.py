from django.db import models
from users.models import User


class Course(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    instructor = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='courses'
    )
    is_published = models.BooleanField(default=False)
    # Nullable: existing courses predate the concept of a term, and not
    # every course needs to be scheduled. Also doubles as the source of
    # truth for "has this course's tenure ended" (term.end_date), used by
    # the course chat tenure-reset feature in a later milestone.
    term = models.ForeignKey(
        'schedule.Term',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='courses'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Chapter(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='chapters'
    )
    title = models.CharField(max_length=200)
    content = models.JSONField(default=list, blank=True)
    visibility = models.CharField(
        max_length=10,
        choices=[
            ('public', 'Public'),
            ('private', 'Private')
        ],
        default='private'
    )
    order_index = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order_index', 'created_at']

    def __str__(self):
        return self.title


class Enrollment(models.Model):
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='enrollments'
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='enrollments'
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['student', 'course']

    def __str__(self):
        return f"{self.student.username} - {self.course.title}"
