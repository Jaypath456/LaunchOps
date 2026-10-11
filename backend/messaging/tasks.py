"""The 'chat resets after the course tenure has ended' requirement:
once a course's term.end_date has passed, its chat room's history is
purged. Hard-delete matches "reset" literally; if a retention
requirement ever shows up, swap this for an archived_at flag on Message
instead of deleting - the query below is the only place that would
need to change.

Writes are already refused for an ended term at send-time (see
messaging/consumers.py::_is_writable), so this job is only responsible
for clearing out what accumulated *before* the term ended - by the time
this runs, no new messages can have been added to an affected course.
"""
import logging
from datetime import date

from lms_project.celery import app

from .models import Message

logger = logging.getLogger(__name__)


@app.task(name='messaging.tasks.purge_ended_term_chats')
def purge_ended_term_chats():
    deleted_count, _ = Message.objects.filter(course__term__end_date__lt=date.today()).delete()
    logger.info('Purged %d chat message(s) from courses with ended terms.', deleted_count)
    return deleted_count
