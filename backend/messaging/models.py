from django.conf import settings
from django.db import models

from courses.models import Course

MAX_MESSAGE_LENGTH = 1000


class Message(models.Model):
    """A single chat message in a course's global room. The room IS the
    course - there's no separate Thread/Room model, matching the
    architecture design (one course-scoped room shared by the instructor
    and every enrolled student).
    """

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='chat_messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_chat_messages'
    )
    body = models.CharField(max_length=MAX_MESSAGE_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['course', 'created_at']),
        ]

    def __str__(self):
        return f'{self.sender.username} in {self.course.title}: {self.body[:40]}'
