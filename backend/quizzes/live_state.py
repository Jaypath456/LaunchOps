"""Ephemeral live-session state in Redis, mirroring LiveSession's DB
status on every REST transition (M16) and every WebSocket-driven
per-question transition (the live quiz consumer, consumers.py).

Redis holds the "hot" state the consumer needs to check on every message
without hitting Postgres each time; the LiveSession row remains the
durable source of truth and is exactly what gets read back if Redis is
flushed or restarted mid-session - nothing here is ever the only copy of
anything that matters. The one exception is the live chart counters
below, which are genuinely ephemeral (visualization only) and are never
read back from anywhere else - see consumers.py for how the same
information is reconstructed from Postgres (Submission.answers) at
question-close time for the "chart counts equal Postgres submission
counts" guarantee.
"""
import logging

from django.core.cache import cache

from lms_project.safe_cache import safe_delete, safe_get, safe_set

logger = logging.getLogger(__name__)

# Generous relative to how long a single live quiz session realistically
# runs - this is a safety-net expiry, not a session-length assumption.
SESSION_STATE_TTL_SECONDS = 60 * 60 * 6
CHART_TTL_SECONDS = 60 * 60 * 6
LEADERBOARD_TTL_SECONDS = 60 * 60 * 6
LEADERBOARD_TOP_N = 10


def _state_key(room_code):
    return f'live_session_state:{room_code}'


def _chart_key(room_code, question_id):
    return f'live_chart:{room_code}:{question_id}'


def _leaderboard_key(room_code):
    return f'live_leaderboard:{room_code}'


def set_session_state(session, question_revealed_at=None):
    """`question_revealed_at` (an ISO timestamp string) is only ever
    passed by consumers.py::_advance_to_next_question - it's what lets a
    late joiner's or reconnecting client's countdown (Question.
    time_limit_seconds, see StudentQuestionSerializer) start from the
    correct remaining time instead of always restarting a full-length
    timer. REST-only transitions (session_create/session_start in
    views.py) never have a revealed question yet, so they leave this
    None.
    """
    safe_set(
        _state_key(session.room_code),
        {
            'quiz_id': session.quiz_id,
            'host_id': session.host_id,
            'status': session.status,
            'current_question_index': session.current_question_index,
            'question_revealed_at': question_revealed_at,
        },
        timeout=SESSION_STATE_TTL_SECONDS,
    )


def get_session_state(room_code):
    return safe_get(_state_key(room_code))


def clear_session_state(room_code):
    safe_delete(_state_key(room_code))


def _redis_client():
    # django-redis exposes the raw redis-py client for operations (hash
    # field increments) that Django's generic cache API doesn't cover.
    # This ties chart tracking specifically to the django-redis backend,
    # which is an acceptable coupling here since it's already the only
    # supported CACHES backend for this project (see settings.py).
    return cache.client.get_client()


def increment_chart_bucket(room_code, question_id, bucket):
    """Bumps one bucket (an option id for choice questions, or
    'correct'/'incorrect' for short_answer - see consumers.py) in a
    question's live chart by one. Best-effort: a chart update that fails
    to land never blocks or invalidates the answer it came from - the
    chart is a visualization, not a correctness record (that's
    Postgres's job, via the Submission row consumers.py writes
    separately in the same request).

    Every operation on this key (increment, read, expire, delete) goes
    through the same raw redis-py client, never django-redis's cache.*
    API - that API applies its own KEY_PREFIX/VERSION namespacing, which
    would silently point cache.expire()/cache.delete() at a different
    key than the one hincrby() actually wrote to.
    """
    try:
        key = _chart_key(room_code, question_id)
        client = _redis_client()
        client.hincrby(key, bucket, 1)
        client.expire(key, CHART_TTL_SECONDS)
    except Exception:
        logger.warning(
            'Chart increment failed for room=%s question=%s bucket=%s',
            room_code, question_id, bucket, exc_info=True,
        )


def get_chart_counts(room_code, question_id):
    try:
        raw = _redis_client().hgetall(_chart_key(room_code, question_id))
        return {
            (k.decode() if isinstance(k, bytes) else k): int(v)
            for k, v in raw.items()
        }
    except Exception:
        logger.warning(
            'Chart read failed for room=%s question=%s', room_code, question_id, exc_info=True,
        )
        return {}


def clear_chart(room_code, question_id):
    try:
        _redis_client().delete(_chart_key(room_code, question_id))
    except Exception:
        logger.warning(
            'Chart clear failed for room=%s question=%s', room_code, question_id, exc_info=True,
        )


def increment_leaderboard_score(room_code, user_id, points):
    """ZINCRBY the student's running total for this session by `points`
    (called for every accepted answer, even worth 0 points, so everyone
    who has answered at least once appears on the leaderboard - not
    just students who've gotten something right). Redis keeps the set
    sorted automatically; there's no separate re-sort step anywhere in
    this module, which is the entire point of using a sorted set here
    instead of recomputing rankings from scratch on every update.
    """
    try:
        client = _redis_client()
        client.zincrby(_leaderboard_key(room_code), points, str(user_id))
        client.expire(_leaderboard_key(room_code), LEADERBOARD_TTL_SECONDS)
    except Exception:
        logger.warning(
            'Leaderboard increment failed for room=%s user=%s', room_code, user_id, exc_info=True,
        )


def get_leaderboard(room_code, top_n=LEADERBOARD_TOP_N):
    """Returns [(user_id_str, score), ...] sorted highest-first. Usernames
    aren't stored in Redis at all - the consumer resolves them from
    Postgres by id, since a live session's roster is always small enough
    that this is cheap, and it avoids duplicating user data into Redis.
    """
    try:
        raw = _redis_client().zrevrange(_leaderboard_key(room_code), 0, top_n - 1, withscores=True)
        return [
            (uid.decode() if isinstance(uid, bytes) else uid, int(score))
            for uid, score in raw
        ]
    except Exception:
        logger.warning('Leaderboard read failed for room=%s', room_code, exc_info=True)
        return []


def clear_leaderboard(room_code):
    try:
        _redis_client().delete(_leaderboard_key(room_code))
    except Exception:
        logger.warning('Leaderboard clear failed for room=%s', room_code, exc_info=True)


def clear_all_live_state(room_code, question_ids):
    """Full teardown for a room: the session-state mirror, the
    leaderboard, and every question's chart. Used by both the WS-driven
    session.end (consumers.py) and the REST session_end escape hatch
    (views.py), so ending a session either way leaves no ephemeral
    state behind regardless of which path the host used.
    """
    clear_session_state(room_code)
    clear_leaderboard(room_code)
    for question_id in question_ids:
        clear_chart(room_code, question_id)
