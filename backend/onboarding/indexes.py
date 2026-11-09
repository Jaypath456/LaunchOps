"""Idempotent MongoDB indexes and the shared provisioning write contract."""

import json
from pathlib import Path

from pymongo.errors import CollectionInvalid, OperationFailure

from .db import get_db


CONTRACT_PATH = Path(__file__).resolve().parents[2] / 'contracts' / 'provisioning_job.schema.json'


def load_provisioning_schema():
    return json.loads(CONTRACT_PATH.read_text())


def _mongo_schema(schema):
    """Translate our JSON contract's types to MongoDB's BSON schema dialect."""
    result = {}
    for key, value in schema.items():
        if key in ('$schema', 'title', 'format'):
            continue
        if key == 'type':
            types = value if isinstance(value, list) else [value]
            bson_types = []
            for item in types:
                if item == 'integer':
                    bson_types.extend(('int', 'long'))
                elif item == 'string' and schema.get('format') == 'date-time':
                    bson_types.append('date')
                else:
                    bson_types.append(item)
            result['bsonType'] = bson_types[0] if len(bson_types) == 1 else bson_types
        elif key == 'properties':
            result[key] = {name: _mongo_schema(child) for name, child in value.items()}
        elif key == 'items':
            result[key] = _mongo_schema(value)
        else:
            result[key] = value
    return result


def workflow_validator():
    return {'$jsonSchema': {
        'bsonType': 'object',
        'required': ['provisioning'],
        'properties': {
            '_id': {'bsonType': 'objectId'},
            'provisioning': {
                'bsonType': 'array',
                'items': _mongo_schema(load_provisioning_schema()),
            },
        },
    }}


EMPLOYEE_INDEX = 'employee_id_1'


def _ensure_unique_employee_index(instances):
    """One workflow per employee (v1 OneToOneField). Only documents with an ObjectId employee_id take part."""
    options = {'name': EMPLOYEE_INDEX, 'unique': True,
               'partialFilterExpression': {'employee_id': {'$type': 'objectId'}}}
    try:
        instances.create_index('employee_id', **options)
    except OperationFailure as exc:
        if exc.code not in (85, 86):  # IndexOptionsConflict / IndexKeySpecsConflict: an older non-unique index.
            raise
        instances.drop_index(EMPLOYEE_INDEX)
        instances.create_index('employee_id', **options)


def ensure_indexes():
    database = get_db()
    options = {
        'validator': workflow_validator(),
        'validationLevel': 'strict',
        'validationAction': 'error',
    }
    # Collection creation is atomic. A concurrent initializer can win the race;
    # collMod also updates the validator when the contract changes later.
    try:
        database.create_collection('workflow_instances', **options)
    except CollectionInvalid:
        database.command('collMod', 'workflow_instances', **options)
    except OperationFailure as exc:
        if exc.code != 48:  # NamespaceExists after a concurrent create_collection.
            raise
        database.command('collMod', 'workflow_instances', **options)

    database['users'].create_index('username', unique=True)
    instances = database['workflow_instances']
    _ensure_unique_employee_index(instances)
    instances.create_index('manager_id')
    instances.create_index([('created_at', -1)])
    instances.create_index([('provisioning.status', 1), ('provisioning.next_attempt_at', 1)])
