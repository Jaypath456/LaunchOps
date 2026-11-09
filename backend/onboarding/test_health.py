from unittest import mock

from pymongo import timeout
from pymongo.errors import ConnectionFailure, ExecutionTimeout, OperationFailure, ServerSelectionTimeoutError
from rest_framework.test import APIClient

from . import db
from .testing import MongoTestCase


class HealthTests(MongoTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_healthy_without_authentication(self):
        with mock.patch.object(db, 'timeout', wraps=timeout) as deadline:
            response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})
        deadline.assert_called_once_with(1.0)

    def test_invalid_credentials_are_ignored(self):
        response = self.client.get('/healthz', HTTP_AUTHORIZATION='Bearer invalid')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_mongo_failures_return_unavailable_without_details(self):
        for error in (ConnectionFailure, ExecutionTimeout, OperationFailure, ServerSelectionTimeoutError):
            with self.subTest(error=error.__name__):
                with mock.patch.object(db, 'get_db') as database:
                    database.return_value.command.side_effect = error('private connection details')
                    response = self.client.get('/healthz')
                    database.return_value.command.assert_called_once_with('ping')
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {'status': 'unavailable'})

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post('/healthz').status_code, 405)
