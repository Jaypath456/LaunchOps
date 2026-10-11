"""Private bootstrap for the Go live-room workers.

The browser never calls this endpoint. The caller must present both a valid
user JWT and the independent service secret; Django keeps ownership of role,
enrolment, room lifecycle and answer keys.
"""
import hmac
import os

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from courses.models import Enrollment
from .models import LiveSession, Question
from .serializers import StudentQuestionSerializer, LiveSessionSerializer
from . import live_state


def _service_only(request):
    supplied = request.headers.get('X-Live-Service', '')
    expected = settings.LIVE_SERVICE_SECRET
    if not expected or not hmac.compare_digest(supplied, expected):
        raise PermissionDenied('Service authorization required.')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def bootstrap(request, room_code):
    _service_only(request)
    session = get_object_or_404(LiveSession.objects.select_related('quiz__course'), room_code=room_code)
    if session.engine != 'go':
        raise PermissionDenied('This room uses the Django live engine.')
    host = session.host_id == request.user.pk
    if not host and not Enrollment.objects.filter(student=request.user, course=session.quiz.course).exists():
        raise PermissionDenied('Course enrollment required.')
    questions = list(session.quiz.questions.order_by('order_index', 'id'))
    return Response({
        'room': room_code, 'quiz_id': session.quiz_id, 'status': session.status,
        'host': host, 'user_id': request.user.pk, 'username': request.user.username,
        'questions': [
            {'public': StudentQuestionSerializer(q).data,
             'answer_key': q.body.get('correct_option_ids', []) if q.question_type != 'short_answer' else q.body.get('correct_answer', '')}
            for q in questions
        ],
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def finish(request, room_code):
    _service_only(request)
    session = get_object_or_404(LiveSession, room_code=room_code)
    if session.host_id != request.user.pk:
        raise PermissionDenied('Only the host can end a room.')
    # Reuse the lifecycle endpoint to update the Django record and Redis
    # compatibility state, including idempotent repeats.
    if session.status != LiveSession.ENDED:
        session.status = LiveSession.ENDED
        session.ended_at = timezone.now()
        session.save(update_fields=['status', 'ended_at'])
        question_ids = Question.objects.filter(quiz_id=session.quiz_id).values_list('id', flat=True)
        live_state.clear_all_live_state(room_code, question_ids)
    return Response(LiveSessionSerializer(session).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def results(request, room_code):
    """Read durable per-room scores; a student sees only their own row."""
    from pymongo import MongoClient
    session = get_object_or_404(LiveSession.objects.select_related('quiz__course'), room_code=room_code)
    host = session.host_id == request.user.pk
    if not host and not Enrollment.objects.filter(student=request.user, course=session.quiz.course).exists():
        raise PermissionDenied('Course enrollment required.')
    with MongoClient(os.getenv('MONGO_URI', 'mongodb://localhost:27017'), serverSelectionTimeoutMS=2000) as client:
        collection = client['launchops_live']['live_answers']
        matches = {'room': room_code}
        if not host:
            matches['user_id'] = request.user.pk
        rows = list(collection.aggregate([
            {'$match': matches},
            {'$group': {'_id': '$user_id', 'username': {'$first': '$username'},
                        'score': {'$sum': '$points'}, 'answered': {'$sum': 1}}},
            {'$sort': {'score': -1, '_id': 1}},
            {'$limit': 100 if host else 1},
        ]))
    return Response({'room_code': room_code, 'status': session.status,
                     'scores': [{'user_id': row['_id'], 'username': row['username'],
                                 'score': row['score'], 'answered': row['answered']} for row in rows]})
