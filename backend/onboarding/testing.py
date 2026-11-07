"""Tests use real MongoDB with a separate database for each test class."""

from datetime import timedelta
from uuid import uuid4

from bson import ObjectId
from django.contrib.auth.hashers import make_password
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import db
from .auth import issue_refresh_token
from .blueprint import FORMS
from .indexes import ensure_indexes
from .repository import create_workflow, now_ms


class MongoTestCase(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.database_name = f'test_launchops_{uuid4().hex}'
        cls.enterClassContext(override_settings(MONGO_DB=cls.database_name))
        previous_database_name = db._test_database_name
        db._test_database_name = cls.database_name
        cls.addClassCleanup(setattr, db, '_test_database_name', previous_database_name)
        database = db.get_db()
        cls.addClassCleanup(database.client.drop_database, database.name)
        ensure_indexes()


# Workflow test helpers. Direct collection writes below are test fixtures standing in for v1's
# `Step.objects.update(...)` shortcuts; application code writes business state only via repository.py.

def make_user(username, role, password=None):
    user = {'_id': ObjectId(), 'username': username, 'display_name': username, 'role': role,
            'password': make_password(password)}
    db.get_users().insert_one(user)
    return user


def actor_of(user):
    return {'id': str(user['_id']), 'username': user['username'], 'role': user['role']}


def bearer(user):
    return f'Bearer {issue_refresh_token(user).access_token}'


def valid_evidence(key):
    return {field['name']: (True if field['type'] == 'checkbox' else field['options'][0] if field['type'] == 'select'
            else timezone.localdate().isoformat() if field['type'] == 'date' else 'Local test ticket reference')
            for field in FORMS[key]}


def instances():
    return db.get_db()['workflow_instances']


def mark_done(instance_id, keys, evidence=None, proof=None, by=None):
    for key in keys:
        result = instances().update_one({'_id': instance_id}, {'$set': {
            'steps.$[s].status': 'done',
            'steps.$[s].completion': {'by': by, 'at': now_ms(), 'evidence': evidence or {}, 'proof': proof,
                                      'request_id': None},
        }}, array_filters=[{'s.key': key}])
        assert result.matched_count == 1


def reset_steps(instance_id):
    instances().update_one({'_id': instance_id}, {
        '$set': {'steps.$[].status': 'open', 'steps.$[].completion': None, 'issues': [], 'status': 'active'},
        '$pull': {'events': {'type': 'completed'}},
    })


def stored_step(instance_id, key):
    doc = instances().find_one({'_id': instance_id})
    return next(step for step in doc['steps'] if step['key'] == key)


def events(instance_id, type_=None, step_key=None):
    doc = instances().find_one({'_id': instance_id})
    return [event for event in doc['events']
            if (type_ is None or event['type'] == type_) and (step_key is None or event['step_key'] == step_key)]


class WorkflowTestCase(MongoTestCase):
    """v1 OnboardingFlowTests fixture: four role users, one other employee and one workflow."""

    def setUp(self):
        super().setUp()
        db.get_users().delete_many({})
        instances().delete_many({})
        self.users = {role: make_user(role, role) for role in ('hr', 'it', 'manager', 'employee')}
        self.other = make_user('other', 'employee')
        self.record = create_workflow(
            actor=actor_of(self.users['hr']), employee_id=self.users['employee']['_id'],
            manager_id=self.users['manager']['_id'], job_title='Engineer', department='Engineering',
            start_date=timezone.localdate() + timedelta(days=10))
        self.wid = self.record['_id']
        self.client = APIClient()

    def as_user(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=bearer(user))

    def as_role(self, role):
        self.as_user(self.users[role])

    def evidence(self, key):
        return valid_evidence(key)

    def url(self, suffix='', workflow_id=None):
        return f'/api/onboarding/workflows/{workflow_id or self.wid}/{suffix}'

    def complete(self, key, evidence=None, workflow_id=None, **headers):
        return self.client.post(self.url(f'steps/{key}/complete/', workflow_id),
                                {'evidence': self.evidence(key) if evidence is None else evidence},
                                format='json', **headers)

    def step(self, key, workflow_id=None):
        return stored_step(workflow_id or self.wid, key)

    def doc(self, workflow_id=None):
        return instances().find_one({'_id': workflow_id or self.wid})

    def assert_unchanged(self, key):
        step = self.step(key)
        self.assertEqual(step['status'], 'open')
        self.assertIsNone(step['completion'])
        self.assertEqual(events(self.wid, 'completed', key), [])

    def assert_replayed(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.headers.get('Idempotent-Replayed'), 'true')
