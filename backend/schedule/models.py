from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from courses.models import Course


class Term(models.Model):
    """e.g. 'Summer 2026'. Drives scheduler filters (Section.term) and is
    the source of truth for "has this course's tenure ended"
    (Course.term.end_date), reused by the course-chat reset feature.
    """

    name = models.CharField(max_length=100, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ['-start_date']

    def __str__(self):
        return self.name


DAY_CHOICES = [
    (0, 'Monday'),
    (1, 'Tuesday'),
    (2, 'Wednesday'),
    (3, 'Thursday'),
    (4, 'Friday'),
    (5, 'Saturday'),
    (6, 'Sunday'),
]


class Section(models.Model):
    """One offering of a Course for a given Term. Instructors create
    sections for courses they teach; students don't create these - they
    only choose among them (in a later milestone's schedule builder).
    """

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='sections'
    )
    term = models.ForeignKey(
        Term,
        on_delete=models.CASCADE,
        related_name='sections'
    )
    section_code = models.CharField(max_length=20, blank=True, default='')
    location = models.CharField(max_length=100, blank=True, default='')
    capacity = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['term', 'course', 'section_code']

    def __str__(self):
        return f'{self.course.title} - {self.section_code or "Section"} ({self.term.name})'


class Meeting(models.Model):
    """A recurring weekly time block for a Section (e.g. "Mon/Wed
    10:00-11:15"). The schedule generation engine (a later milestone)
    treats these as the fixed, non-negotiable slots a Section occupies.
    """

    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        related_name='meetings'
    )
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ['day_of_week', 'start_time']

    def __str__(self):
        return f'{self.get_day_of_week_display()} {self.start_time}-{self.end_time}'

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({'end_time': 'end_time must be after start_time.'})


class SavedSchedule(models.Model):
    """A student's chosen candidate from the schedule generation engine's
    output - the 'shopping cart' / current schedule from the College
    Scheduler-style flow. Confirming it (see the confirm endpoint) creates
    the actual Enrollment rows, which is the same Enrollment table that
    already gates chapters/quizzes/chat - one membership concept
    everywhere, not a second one for "scheduled but not yet confirmed".
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_schedules'
    )
    term = models.ForeignKey(
        Term,
        on_delete=models.CASCADE,
        related_name='saved_schedules'
    )
    sections = models.ManyToManyField(Section, related_name='saved_schedules')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        state = 'confirmed' if self.confirmed_at else 'draft'
        return f'{self.student.username} - {self.term.name} ({state})'


class Break(models.Model):
    """A student's personal blocked time window (e.g. "no classes before
    10am on Mondays"). Deliberately student-only: an instructor's
    equivalent blocked time is derived from their own existing Meetings
    rather than a second concept - see the schedule generation engine.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='breaks'
    )
    day_of_week = models.IntegerField(choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    label = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        ordering = ['day_of_week', 'start_time']

    def __str__(self):
        return f'{self.student.username} - {self.get_day_of_week_display()} {self.start_time}-{self.end_time}'

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({'end_time': 'end_time must be after start_time.'})
