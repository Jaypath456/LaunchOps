"""Shared MongoDB connection and timezone-aware collection access."""

from datetime import UTC
import sys

from django.conf import settings
from pymongo import MongoClient


_client = MongoClient(
    settings.MONGO_URI,
    tz_aware=True,
    tzinfo=UTC,
    connect=False,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000,
)
_test_database_name = None


def get_db():
    if _test_database_name is not None:
        return _client[_test_database_name]
    if 'test' in sys.argv[1:]:
        raise RuntimeError('MongoDB access during tests requires a MongoTestCase database scope.')
    return _client[settings.MONGO_DB]


def get_users():
    return get_db()['users']
