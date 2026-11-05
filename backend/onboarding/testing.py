"""Tests use real MongoDB with a separate database for each test class."""

from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .db import get_db, get_users


class MongoTestCase(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.database_name = f'test_launchops_{uuid4().hex}'
        cls.enterClassContext(override_settings(MONGO_DB=cls.database_name))
        database = get_db()
        cls.addClassCleanup(database.client.drop_database, database.name)
        get_users()
