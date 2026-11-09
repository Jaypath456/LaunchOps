"""MongoDB users and stateless SimpleJWT sessions."""

from bson import ObjectId
from django.contrib.auth.hashers import check_password, make_password
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenViewBase

from .db import get_users


ROLES = ('hr', 'it', 'manager', 'employee')


def _set_claims(token, user):
    token['user_id'] = str(user['_id'])
    token['username'] = user['username']
    token['role'] = user['role']


def issue_refresh_token(user):
    refresh = RefreshToken()
    _set_claims(refresh, user)
    return refresh


def _user_response(user):
    # The MongoDB user schema has no email; keep the v1 response field.
    return {
        'id': str(user['_id']),
        'username': user['username'],
        'email': '',
        'role': user['role'],
    }


def get_actor(request):
    """Return the authenticated token's identity for workflow permissions/events."""
    user = request.user
    if (
        not user.is_authenticated
        or not isinstance(user.id, str)
        or not ObjectId.is_valid(user.id)
        or not isinstance(user.username, str)
        or not user.username
        or user.role not in ROLES
    ):
        raise AuthenticationFailed('Token contained no valid actor.')
    return {'id': user.id, 'username': user.username, 'role': user.role}


class SessionRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(trim_whitespace=False)

    def validate(self, attrs):
        try:
            refresh = RefreshToken(attrs['refresh'])
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc

        user_id = refresh.get('user_id')
        if not isinstance(user_id, str) or not ObjectId.is_valid(user_id):
            raise InvalidToken('Token contained no valid user identification.')
        user = get_users().find_one({'_id': ObjectId(user_id)})
        if user is None or user.get('role') not in ROLES:
            raise InvalidToken('This account is no longer available.')

        access = refresh.access_token
        _set_claims(access, user)
        return {'access': str(access)}


class SessionRefreshView(TokenViewBase):
    serializer_class = SessionRefreshSerializer


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def login(request):
    username = request.data.get('username')
    password = request.data.get('password')
    role = request.data.get('role')
    if role is not None and role not in ROLES:
        return Response({'error': 'Choose a valid onboarding role.'}, status=400)

    user = None
    if isinstance(username, str) and isinstance(password, str) and username and password:
        user = get_users().find_one({'username': username})
    encoded_password = user.get('password') if user is not None else None
    user_role = user.get('role') if user is not None else None
    if not isinstance(encoded_password, str) or not encoded_password or user_role not in ROLES:
        # Match Django's missing-user password hashing cost.
        make_password(password if isinstance(password, str) else '')
        valid_credentials = False
    else:
        valid_credentials = check_password(password, encoded_password)
    if not valid_credentials:
        return Response(
            {'error': 'Invalid username or password'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if role is not None and user_role != role:
        return Response({'error': 'This account does not belong to the selected role.'}, status=403)

    refresh = issue_refresh_token(user)
    return Response({
        'user': _user_response(user),
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me(request):
    actor = get_actor(request)
    return Response(_user_response({
        '_id': actor['id'], 'username': actor['username'], 'role': actor['role'],
    }))
