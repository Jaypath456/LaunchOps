"""Thin wrappers around django.core.cache.cache that degrade gracefully
if the cache backend (Redis) is unreachable, instead of raising and
breaking the request.

Every feature that touches the cache in this project - catalog caching,
the quiz submission idempotency lock - must keep working (just without
the speed-up) even when Redis is completely down. Caching and the
SETNX-style idempotency lock are both optimizations, never a correctness
requirement; the real guarantees are DB constraints (unique_together,
etc.) that exist independently of Redis.
"""
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def safe_get(key, default=None):
    try:
        return cache.get(key, default)
    except Exception:
        logger.warning('Cache GET failed for key=%s; treating as a miss.', key, exc_info=True)
        return default


def safe_set(key, value, timeout=None):
    try:
        cache.set(key, value, timeout)
    except Exception:
        logger.warning('Cache SET failed for key=%s; continuing without caching.', key, exc_info=True)


def safe_delete(key):
    try:
        cache.delete(key)
    except Exception:
        logger.warning('Cache DELETE failed for key=%s.', key, exc_info=True)


def safe_delete_pattern(pattern):
    """Deletes every cache key matching a glob-style pattern (e.g.
    'course_catalog:published*'), for cases like catalog invalidation
    where a single logical entry is actually spread across several keys
    (one per page/page_size combination - see courses/views.py::
    course_list). Relies on django-redis's delete_pattern, which isn't
    part of Django's generic cache API, so this degrades to a no-op
    (rather than raising) on any other cache backend."""
    try:
        cache.delete_pattern(pattern)
    except AttributeError:
        pass
    except Exception:
        logger.warning('Cache DELETE_PATTERN failed for pattern=%s.', pattern, exc_info=True)


def safe_add(key, value, timeout=None, default_on_error=True):
    """Wraps cache.add (SETNX semantics: sets the value only if the key
    isn't already present, returns True iff it set the value).

    On a cache backend error, returns `default_on_error` instead of
    raising. Callers using this for an idempotency lock should leave
    this as True, so a Redis outage never blocks a legitimate first-time
    request - it just means the fast path is skipped and the request
    falls through to whatever DB-level guarantee already exists.
    """
    try:
        return cache.add(key, value, timeout)
    except Exception:
        logger.warning('Cache ADD failed for key=%s; degrading gracefully.', key, exc_info=True)
        return default_on_error
