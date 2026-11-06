"""Tests use real MongoDB with a separate database for each test class."""

from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from . import db
from .indexes import ensure_indexes


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
