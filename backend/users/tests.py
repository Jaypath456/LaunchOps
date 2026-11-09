from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bson import ObjectId
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.test import SimpleTestCase, override_settings
from pymongo.errors import DuplicateKeyError
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.authentication import JWTStatelessUserAuthentication
from rest_framework_simplejwt.models import TokenUser
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from onboarding.auth import get_actor, issue_refresh_token
from onboarding.db import get_db, get_users
from onboarding.testing import MongoTestCase


def create_user(username, password='test password', role='employee'):
    user = {
        '_id': ObjectId(),
        'username': username,
        'display_name': username,
        'role': role,
        'password': make_password(password),
    }
    get_users().insert_one(user)
    return user


class RoleLoginTests(MongoTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_each_role_login_is_validated_without_changing_privileges(self):
        for role in ('hr', 'it', 'manager', 'employee'):
            with self.subTest(role=role):
                user = create_user(role, password='exact password', role=role)
                response = self.client.post('/api/auth/login/', {
                    'username': role, 'password': 'exact password', 'role': role,
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.data), {'user', 'access', 'refresh'})
                self.assertEqual(response.data['user'], {
                    'id': str(user['_id']), 'username': role, 'email': '', 'role': role,
                })
                for token in (AccessToken(response.data['access']), RefreshToken(response.data['refresh'])):
                    self.assertEqual(token['user_id'], str(user['_id']))
                    self.assertEqual(token['username'], role)
                    self.assertEqual(token['role'], role)
                    self.assertNotIn('password', token.payload)
                wrong = 'employee' if role != 'employee' else 'hr'
                response = self.client.post('/api/auth/login/', {
                    'username': role, 'password': 'exact password', 'role': wrong,
                })
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('access', response.data)
                self.assertEqual(get_users().find_one({'_id': user['_id']})['role'], role)

    def test_invalid_role_and_credentials_issue_no_tokens(self):
        create_user('known')
        cases = [
            ({'role': 'superuser'}, 400),
            ({'role': {'$ne': ''}}, 400),
            ({'username': 'missing', 'password': 'bad', 'role': 'hr'}, 401),
            ({'username': 'known', 'password': 'wrong'}, 401),
            ({'username': 'known'}, 401),
            ({'password': 'test password'}, 401),
            ({'username': 'known', 'password': 123}, 401),
            ({'username': {'$ne': ''}, 'password': 'test password'}, 401),
            ({'username': 'known', 'password': {'$ne': ''}}, 401),
        ]
        for data, expected in cases:
            with self.subTest(data=data):
                response = self.client.post('/api/auth/login/', data, format='json')
                self.assertEqual(response.status_code, expected)
                self.assertNotIn('access', response.data)
                self.assertNotIn('refresh', response.data)

    def test_login_preserves_exact_password(self):
        password = '  whitespace password  '
        user = create_user('hire', password=password)
        self.assertNotEqual(user['password'], password)
        self.assertTrue(check_password(password, user['password']))
        self.assertEqual(self.client.post('/api/auth/login/', {
            'username': 'hire', 'password': password,
        }).status_code, 200)
        self.assertEqual(self.client.post('/api/auth/login/', {
            'username': 'hire', 'password': password.strip(),
        }).status_code, 401)

    def test_malformed_user_documents_are_rejected(self):
        for field, value in (
            ('password', None), ('password', ''), ('password', 123), ('password', {}),
            ('role', None), ('role', ''), ('role', 'superuser'), ('role', {}),
        ):
            with self.subTest(field=field, value=value):
                user = create_user(f'malformed_{uuid4().hex}')
                update = {'$unset': {field: ''}} if value is None else {'$set': {field: value}}
                get_users().update_one({'_id': user['_id']}, update)
                for role in (None, 'employee'):
                    data = {'username': user['username'], 'password': 'test password'}
                    if role is not None:
                        data['role'] = role
                    response = self.client.post('/api/auth/login/', data, format='json')
                    self.assertEqual(response.status_code, 401)
                    self.assertNotIn('access', response.data)
                    self.assertNotIn('refresh', response.data)

    def test_login_and_refresh_allow_an_expired_bearer_header(self):
        user = create_user('expired_header')
        refresh = issue_refresh_token(user)
        access = refresh.access_token
        access.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(self.client.post('/api/auth/login/', {
            'username': user['username'], 'password': 'test password',
        }).status_code, 200)
        self.assertEqual(self.client.post('/api/auth/refresh/', {
            'refresh': str(refresh),
        }).status_code, 200)


class TokenRefreshTests(MongoTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user(f'refresh_{uuid4().hex}')
        self.refresh = issue_refresh_token(self.user)

    def test_expired_access_refreshes_and_new_access_uses_current_server_identity(self):
        access = self.refresh.access_token
        access.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        get_users().update_one({'_id': self.user['_id']}, {
            '$set': {'role': 'manager', 'username': 'renamed'},
        })
        response = self.client.post('/api/auth/refresh/', {'refresh': str(self.refresh)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'access'})
        access = AccessToken(response.data['access'])
        self.assertEqual(access['user_id'], str(self.user['_id']))
        self.assertEqual(access['username'], 'renamed')
        self.assertEqual(access['role'], 'manager')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(self.client.get('/api/auth/me/').data, {
            'id': str(self.user['_id']), 'username': 'renamed', 'email': '', 'role': 'manager',
        })

    def test_invalid_expired_and_wrong_token_types_are_rejected(self):
        expired = issue_refresh_token(self.user)
        expired.set_exp(lifetime=timedelta(seconds=-1))
        tampered = str(self.refresh).split('.')
        tampered[1] = tampered[1][::-1]
        for token in ('invalid', str(expired), str(self.refresh.access_token), '.'.join(tampered)):
            with self.subTest(token=token[:8]):
                response = self.client.post('/api/auth/refresh/', {'refresh': token})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('access', response.data)
        for data in ({}, {'refresh': ''}, {'refresh': None}, {'refresh': []}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post('/api/auth/refresh/', data, format='json').status_code, 400)

    def test_deleted_account_cannot_refresh_and_access_lifetime_is_configured(self):
        access = self.refresh.access_token
        self.assertEqual(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'], timedelta(minutes=15))
        self.assertEqual(
            access['exp'] - access['iat'],
            settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds(),
        )
        self.assertEqual(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'], timedelta(days=7))
        get_users().delete_one({'_id': self.user['_id']})
        response = self.client.post('/api/auth/refresh/', {'refresh': str(self.refresh)})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('access', response.data)

    def test_missing_and_malformed_user_ids_are_rejected(self):
        for user_id in (None, '', 'invalid', 123, {'$ne': ''}, str(ObjectId())):
            with self.subTest(user_id=user_id):
                refresh = RefreshToken()
                if user_id is not None:
                    refresh['user_id'] = user_id
                response = self.client.post('/api/auth/refresh/', {'refresh': str(refresh)})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('access', response.data)


class StatelessSessionTests(MongoTestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user(f'session_{uuid4().hex}', role='it')
        self.access = issue_refresh_token(self.user).access_token

    def test_me_requires_a_valid_access_token(self):
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)
        expired = issue_refresh_token(self.user).access_token
        expired.set_exp(lifetime=timedelta(seconds=-1))
        for token in ('invalid', str(expired), str(issue_refresh_token(self.user))):
            with self.subTest(token=token[:8]):
                self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
                self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)

    def test_me_and_actor_are_backed_by_token_claims(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'id': str(self.user['_id']), 'username': self.user['username'], 'email': '', 'role': 'it',
        })
        request = APIRequestFactory().get('/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {self.access}')
        request.user, request.auth = JWTStatelessUserAuthentication().authenticate(request)
        self.assertIsInstance(request.user, TokenUser)
        self.assertEqual(get_actor(request), {
            'id': str(self.user['_id']), 'username': self.user['username'], 'role': 'it',
        })

    def test_malformed_actor_claims_are_rejected(self):
        for claim, value in (
            ('user_id', 'invalid'), ('user_id', 123), ('username', ''),
            ('username', None), ('role', 'superuser'), ('role', None),
        ):
            with self.subTest(claim=claim, value=value):
                access = issue_refresh_token(self.user).access_token
                access[claim] = value
                self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
                self.assertEqual(self.client.get('/api/auth/me/').status_code, 401)

    def test_onboarding_routes_require_authentication(self):
        # c07 wired the MongoDB workflow API; v1 integer routes stay gone.
        self.assertEqual(self.client.get('/api/onboarding/workflows/').status_code, 401)
        self.assertEqual(self.client.get('/api/onboarding/steps/1/').status_code, 404)


class UnscopedMongoAccessTests(SimpleTestCase):
    def test_unscoped_database_and_users_access_are_rejected(self):
        with override_settings(MONGO_DB='launchops'):
            for accessor in (get_db, get_users):
                with self.subTest(accessor=accessor.__name__):
                    with self.assertRaisesRegex(RuntimeError, 'MongoTestCase database scope'):
                        accessor()


class MongoFoundationTests(MongoTestCase):
    def test_usernames_are_unique(self):
        create_user('unique_username')
        with self.assertRaises(DuplicateKeyError):
            create_user('unique_username')

    def test_mongo_datetimes_are_aware_utc(self):
        now = datetime.now(UTC)
        collection = get_db()['clock_test']
        document_id = collection.insert_one({'at': now}).inserted_id
        stored = collection.find_one({'_id': document_id})['at']
        self.assertEqual(stored.tzinfo, UTC)
        self.assertEqual(stored, now.replace(microsecond=now.microsecond // 1000 * 1000))

    def test_get_db_stays_in_the_test_scope_despite_database_setting(self):
        database = get_db()
        self.assertEqual(database.name, self.database_name)
        self.assertNotEqual(database.name, 'launchops')
        with override_settings(MONGO_DB='launchops'):
            self.assertEqual(get_db().name, self.database_name)
            self.assertIs(get_db().client, database.client)
        self.assertEqual(get_db().name, self.database_name)

    def test_test_database_is_dropped_after_class_cleanup(self):
        class IsolatedMongoTests(MongoTestCase):
            pass

        database = get_db()
        IsolatedMongoTests.setUpClass()
        try:
            isolated = get_db()
            self.assertNotEqual(isolated.name, database.name)
            isolated['sentinel'].insert_one({'marker': True})
            self.assertIn(isolated.name, database.client.list_database_names())
        finally:
            IsolatedMongoTests.doClassCleanups()
        self.assertEqual(get_db().name, database.name)
        self.assertNotIn(isolated.name, database.client.list_database_names())
