from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate

from .serializers import UserSerializer, SessionRefreshSerializer
from rest_framework_simplejwt.views import TokenRefreshView


class SessionRefreshView(TokenRefreshView):
    serializer_class = SessionRefreshSerializer


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    username = request.data.get('username')
    password = request.data.get('password')
    role = request.data.get('role')
    if role is not None and role not in ('hr', 'it', 'manager', 'employee'):
        return Response({'error': 'Choose a valid onboarding role.'}, status=400)

    user = authenticate(username=username, password=password)

    if user is None:
        return Response(
            {'error': 'Invalid username or password'},
            status=status.HTTP_401_UNAUTHORIZED
        )

    if role is not None and user.role != role:
        return Response({'error': 'This account does not belong to the selected role.'}, status=403)

    refresh = RefreshToken.for_user(user)

    return Response({
        'user': UserSerializer(user).data,
        'access': str(refresh.access_token),
        'refresh': str(refresh)
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    serializer = UserSerializer(request.user)
    return Response(serializer.data)
