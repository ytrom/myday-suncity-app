"""Test settings: both databases are SQLite, and the BowlControl tables get created for the tests."""
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "tests-only")

from myday_project.settings import *  # noqa: E402,F401,F403

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "bowlcontrol": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}
DATABASE_ROUTERS = ["tests.settings.TestRouter"]
TEST_RUNNER = "tests.settings.Runner"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
DEV_USER_EMAIL = ""
CF_ACCESS_TEAM_DOMAIN = ""
CF_ACCESS_AUD = ""
VENUE_LAT, VENUE_LNG, VENUE_RADIUS_M = -26.6720, 153.1060, 200
VENUE_PUBLIC_IPS = ["203.0.113.10/32"]
WRITES_ENABLED = False


class TestRouter:
    def _bc(self, model):
        return model._meta.app_label == "bc"

    def db_for_read(self, model, **hints):
        return "bowlcontrol" if self._bc(model) else None

    db_for_write = db_for_read

    def allow_relation(self, a, b, **hints):
        return None

    def allow_migrate(self, db, app_label, **hints):
        if app_label == "bc":
            return db == "bowlcontrol"
        return db == "default"


from django.test.runner import DiscoverRunner  # noqa: E402


class Runner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        from django.apps import apps

        for model in apps.get_app_config("bc").get_models():
            model._meta.managed = True
        return super().setup_databases(**kwargs)
