from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from courses.models import Course

from .models import Message
from .pagination import MessagePagination
from .permissions import can_access_course_chat
from .serializers import MessageSerializer


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def message_list(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    if not can_access_course_chat(request.user, course):
        return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

    messages = (
        Message.objects.filter(course=course)
        .select_related('sender')
        .order_by('-created_at')
    )

    paginator = MessagePagination()
    page = paginator.paginate_queryset(messages, request)
    serializer = MessageSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)
