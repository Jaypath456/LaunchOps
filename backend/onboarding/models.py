from django.conf import settings
from django.db import models


class Onboarding(models.Model):
    employee = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='onboarding')
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='managed_onboardings')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_onboardings')
    job_title = models.CharField(max_length=160)
    department = models.CharField(max_length=100)
    start_date = models.DateField()
    training_course = models.ForeignKey('courses.Course', null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Step(models.Model):
    KIND = [('approval', 'Approval'), ('provisioning', 'Provisioning'), ('training', 'Training'), ('orientation', 'Orientation')]
    onboarding = models.ForeignKey(Onboarding, on_delete=models.CASCADE, related_name='steps')
    key = models.CharField(max_length=60)
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    owner_role = models.CharField(max_length=20)
    kind = models.CharField(max_length=20, choices=KIND)
    due_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    evidence = models.JSONField(default=dict, blank=True)
    # A dependency can only refer to earlier steps from the same onboarding;
    # workflow creation owns this invariant. The API never mutates edges.
    prerequisites = models.ManyToManyField('self', symmetrical=False, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['onboarding', 'key'], name='unique_onboarding_step')]
        ordering = ['id']
        indexes = [models.Index(fields=['owner_role', 'due_at'])]


class Event(models.Model):
    onboarding = models.ForeignKey(Onboarding, on_delete=models.CASCADE, related_name='events')
    step = models.ForeignKey(Step, null=True, blank=True, on_delete=models.SET_NULL)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=50)
    detail = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['onboarding', '-created_at'])]
        constraints = [models.UniqueConstraint(fields=['step', 'action'], condition=models.Q(action='overdue'), name='unique_overdue_event')]


class LessonReview(models.Model):
    onboarding = models.ForeignKey(Onboarding, on_delete=models.CASCADE, related_name='lesson_reviews')
    chapter = models.ForeignKey('courses.Chapter', on_delete=models.PROTECT)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['onboarding', 'chapter'], name='unique_lesson_review')]


class AssessmentAttempt(models.Model):
    """Retains failed attempts when the legacy single-submission slot is retried."""
    onboarding = models.ForeignKey(Onboarding, on_delete=models.CASCADE)
    quiz = models.ForeignKey('quizzes.Quiz', on_delete=models.PROTECT)
    answers = models.JSONField(default=dict)
    score = models.IntegerField()
    max_score = models.IntegerField()
    submitted_at = models.DateTimeField()
    archived_at = models.DateTimeField(auto_now_add=True)


class Issue(models.Model):
    onboarding = models.ForeignKey(Onboarding, on_delete=models.CASCADE, related_name='issues')
    step = models.OneToOneField(Step, on_delete=models.CASCADE)
    detail = models.CharField(max_length=240)
    resolution = models.CharField(max_length=240, blank=True)
    resolved_at = models.DateTimeField(null=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
