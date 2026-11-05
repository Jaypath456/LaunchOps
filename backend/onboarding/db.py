"""Shared MongoDB connection and timezone-aware collection access."""

from datetime import UTC

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


def get_db():
    # Read the setting on each call so tests can isolate each class's database.
    return _client[settings.MONGO_DB]


def get_users():
    users = get_db()['users']
    users.create_index('username', unique=True)
    return users
