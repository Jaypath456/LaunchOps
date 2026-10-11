"""No real scheduled jobs yet - this heartbeat is scaffolding for a
later milestone (the course-chat tenure-reset purge job) to build on,
and gives beat something to run so the worker/beat setup is verifiable
end to end before any feature depends on it.
"""
import logging

from .celery import app

logger = logging.getLogger(__name__)


@app.task(name='lms_project.tasks.heartbeat')
def heartbeat():
    logger.info('Celery beat heartbeat: worker is alive.')
    return 'ok'
