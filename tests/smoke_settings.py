"""Local preview: SQLite for both databases and a test user. Seed with `python -m tests.seed`."""
import os

from tests.settings import *  # noqa: F401,F403

DEBUG = True
_DIR = os.environ.get("SMOKE_DIR", "/tmp")
DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": f"{_DIR}/myday.sqlite3"},
    "bowlcontrol": {"ENGINE": "django.db.backends.sqlite3", "NAME": f"{_DIR}/bc.sqlite3"},
}
DEV_USER_EMAIL = "sam@suncitytenpin.com.au"
ALLOWED_HOSTS = ["*"]
