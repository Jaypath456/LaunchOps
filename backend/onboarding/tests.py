from copy import deepcopy
from datetime import UTC, datetime
from io import StringIO
import json

from bson import ObjectId
from django.contrib.auth.hashers import check_password, make_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from jsonschema import Draft7Validator, FormatChecker, ValidationError
from pymongo.errors import DuplicateKeyError, WriteError

from .blueprint import BLUEPRINT, BLUEPRINT_VERSION, CONNECTORS, FORMS
from .db import get_db, get_users
from .graph import validate_blueprint
from .indexes import CONTRACT_PATH, ensure_indexes, load_provisioning_schema
from .management.commands.seed_launchops import PASSWORD
from .testing import MongoTestCase


def fixtures():
    return {
        path.stem: json.loads(path.read_text())
        for path in sorted((CONTRACT_PATH.parent / 'fixtures').glob('*.json'))
    }


def bson_job(job):
    result = deepcopy(job)
    for key in ('lease_until', 'next_attempt_at', 'updated_at'):
        if result[key] is not None:
            result[key] = datetime.fromisoformat(result[key].replace('Z', '+00:00'))
    return result


class BlueprintGraphTests(MongoTestCase):
    def setUp(self):
        self.blueprint = [
            ('second', 'Second', '', 'employee', 'training', 0, ['first']),
            ('first', 'First', '', 'hr', 'approval', 0, []),
        ]
        self.forms = {'first': [], 'second': []}

    def test_cycle_detected(self):
        self.blueprint[1] = (*self.blueprint[1][:-1], ['second'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            validate_blueprint(self.blueprint, self.forms)

    def test_self_cycle_detected(self):
        self.blueprint[1] = (*self.blueprint[1][:-1], ['first'])
        with self.assertRaisesRegex(ValueError, 'cycle'):
            validate_blueprint(self.blueprint, self.forms)

    def test_unknown_prerequisite(self):
        self.blueprint[0] = (*self.blueprint[0][:-1], ['absent'])
        with self.assertRaisesRegex(ValueError, 'Unknown prerequisite'):
            validate_blueprint(self.blueprint, self.forms)

    def test_duplicate_key(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate step key'):
            validate_blueprint(self.blueprint + [self.blueprint[0]], self.forms)

    def test_missing_form(self):
        with self.assertRaisesRegex(ValueError, 'Missing form'):
            validate_blueprint(self.blueprint, {'first': []})

    def test_form_key_must_exist(self):
        with self.assertRaisesRegex(ValueError, 'Form has no blueprint step'):
            validate_blueprint(self.blueprint, {**self.forms, 'absent': []})

    def test_unknown_role_and_kind(self):
        for index, value, message in ((3, 'admin', 'Unknown role'), (4, 'quiz', 'Unknown kind')):
            with self.subTest(value=value):
                step = list(self.blueprint[0])
                step[index] = value
                with self.assertRaisesRegex(ValueError, message):
                    validate_blueprint([tuple(step), self.blueprint[1]], self.forms)

    def test_topological_order_valid(self):
        self.assertEqual(validate_blueprint(self.blueprint, self.forms), ['first', 'second'])

    def test_real_blueprint_valid(self):
        order = validate_blueprint()
        self.assertEqual(len(order), len(BLUEPRINT))
        self.assertEqual(set(order), set(FORMS))
        positions = {key: index for index, key in enumerate(order)}
        for key, _title, _description, _role, _kind, _offset, prerequisites in BLUEPRINT:
            for prerequisite in prerequisites:
                self.assertLess(positions[prerequisite], positions[key])
        self.assertEqual(BLUEPRINT_VERSION, 1)
        self.assertEqual(CONNECTORS, ['workspace', 'slack', 'github'])


class ProvisioningContractTests(MongoTestCase):
    def setUp(self):
        schema = load_provisioning_schema()
        Draft7Validator.check_schema(schema)
        self.validator = Draft7Validator(schema, format_checker=FormatChecker())

    def test_all_six_fixtures_validate(self):
        examples = fixtures()
        self.assertEqual(set(examples), {'pending', 'running', 'retry', 'succeeded', 'dead', 'overridden'})
        for status, job in examples.items():
            with self.subTest(status=status):
                self.assertEqual(job['status'], status)
                self.validator.validate(job)

    def test_contract_rejects_missing_unknown_and_malformed_fields(self):
        pending = fixtures()['pending']
        invalid = []
        for key in pending:
            job = deepcopy(pending)
            del job[key]
            invalid.append(job)
        for key, value in (
            ('unexpected', True), ('connector', 'unknown'), ('status', 'unknown'),
            ('attempts', -1), ('attempts', 1.5), ('fence', -1),
            ('updated_at', None), ('updated_at', 'not-a-date'),
            ('lease_until', 'not-a-date'), ('next_attempt_at', 'not-a-date'),
            ('payload', {'username': 'missing-fields'}), ('idempotency_key', 'invalid'),
        ):
            invalid.append({**pending, key: value})
        for job in invalid:
            with self.subTest(job=job):
                with self.assertRaises(ValidationError):
                    self.validator.validate(job)


class IndexAndValidatorTests(MongoTestCase):
    def setUp(self):
        ensure_indexes()
        self.instances = get_db()['workflow_instances']

    def test_ensure_indexes_twice_is_clean(self):
        before = self.instances.index_information()
        options = self.instances.options()
        call_command('ensure_indexes', stdout=StringIO())
        call_command('ensure_indexes', stdout=StringIO())
        self.assertEqual(self.instances.index_information(), before)
        self.assertEqual(self.instances.options(), options)
        self.assertTrue(get_users().index_information()['username_1']['unique'])
        self.assertEqual({tuple(index['key']) for index in before.values()}, {
            (('_id', 1),), (('employee_id', 1),), (('manager_id', 1),),
            (('created_at', -1),), (('provisioning.status', 1), ('provisioning.next_attempt_at', 1)),
        })

    def test_ensure_indexes_updates_existing_validator(self):
        get_db().command('collMod', 'workflow_instances', validator={})
        ensure_indexes()
        self.assertIn('$jsonSchema', self.instances.options()['validator'])

    def test_mongo_accepts_every_fixture_with_bson_dates(self):
        for status, job in fixtures().items():
            with self.subTest(status=status):
                result = self.instances.insert_one({'provisioning': [bson_job(job)]})
                stored = self.instances.find_one({'_id': result.inserted_id})
                self.assertIsInstance(stored['_id'], ObjectId)
                self.assertEqual(stored['provisioning'][0]['updated_at'].tzinfo, UTC)

    def test_mongo_rejects_malformed_provisioning_on_insert(self):
        pending = bson_job(fixtures()['pending'])
        malformed = [{}, {'connector': 'workspace'}]
        for key in pending:
            job = deepcopy(pending)
            del job[key]
            malformed.append(job)
        for key, value in (
            ('status', 'unknown'), ('connector', 'unknown'), ('attempts', -1),
            ('attempts', 0.5), ('fence', -1), ('fence', True),
            ('updated_at', '2026-01-01T00:00:00Z'), ('updated_at', None),
            ('lease_until', '2026-01-01T00:00:00Z'),
            ('next_attempt_at', '2026-01-01T00:00:00Z'),
            ('payload', {'username': 'missing-fields'}), ('unexpected', 'field'),
        ):
            malformed.append({**pending, key: value})
        for job in malformed:
            with self.subTest(job=job):
                with self.assertRaises(WriteError) as caught:
                    self.instances.insert_one({'provisioning': [job]})
                self.assertEqual(caught.exception.code, 121)

    def test_mongo_rejects_invalid_container_and_object_id(self):
        for document in ({}, {'provisioning': {}}, {'provisioning': None}, {'_id': 'invalid', 'provisioning': []}):
            with self.subTest(document=document):
                with self.assertRaises(WriteError) as caught:
                    self.instances.insert_one(document)
                self.assertEqual(caught.exception.code, 121)

    def test_mongo_validator_applies_to_updates(self):
        result = self.instances.insert_one({'provisioning': [bson_job(fixtures()['pending'])]})
        with self.assertRaises(WriteError) as caught:
            self.instances.update_one({'_id': result.inserted_id}, {'$set': {'provisioning.0.status': 'unknown'}})
        self.assertEqual(caught.exception.code, 121)


class SeedLaunchOpsTests(MongoTestCase):
    def setUp(self):
        get_users().delete_many({})

    @override_settings(DEBUG=True)
    def test_seed_twice_is_clean_and_preserves_credentials(self):
        call_command('seed_launchops', stdout=StringIO())
        before = list(get_users().find().sort('username', 1))
        self.assertEqual(len(before), 4)
        self.assertEqual({user['role'] for user in before}, {'hr', 'it', 'manager', 'employee'})
        for user in before:
            self.assertEqual(user['username'], f'demo_{user["role"]}')
            self.assertTrue(user['display_name'])
            self.assertTrue(check_password(PASSWORD, user['password']))
        user = before[0]
        changed_password = make_password('changed password')
        get_users().update_one({'_id': user['_id']}, {'$set': {'password': changed_password}})
        user['password'] = changed_password
        call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(list(get_users().find().sort('username', 1)), before)
        self.assertIn('validator', get_db()['workflow_instances'].options())

    @override_settings(DEBUG=False)
    def test_seed_refuses_outside_debug_without_writes(self):
        before = get_db().list_collection_names()
        with self.assertRaisesRegex(CommandError, 'DEBUG=True'):
            call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(get_users().count_documents({}), 0)
        self.assertEqual(get_db().list_collection_names(), before)

    @override_settings(DEBUG=True)
    def test_seed_refuses_role_collision_before_creating_users(self):
        get_users().insert_one({'username': 'demo_manager', 'role': 'employee', 'password': 'unchanged'})
        with self.assertRaisesRegex(CommandError, 'collision'):
            call_command('seed_launchops', stdout=StringIO())
        self.assertEqual(get_users().count_documents({}), 1)
        self.assertEqual(get_users().find_one({'username': 'demo_manager'})['role'], 'employee')

    @override_settings(DEBUG=True)
    def test_seed_enforces_unique_usernames(self):
        call_command('seed_launchops', stdout=StringIO())
        with self.assertRaises(DuplicateKeyError):
            get_users().insert_one({'username': 'demo_employee', 'role': 'employee'})
