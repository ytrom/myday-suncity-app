"""MyDay settings. Everything environment-specific comes from env vars (see .env.example)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-not-secret")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "myday.suncitytenpin.com.au,localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "https://myday.suncitytenpin.com.au")

if not DEBUG and SECRET_KEY == "dev-only-not-secret":
    raise RuntimeError("Set DJANGO_SECRET_KEY in /opt/myday/.env")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bc",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.CloudflareIdentityMiddleware",
    "core.middleware.PinLockMiddleware",
]

ROOT_URLCONF = "myday_project.urls"
WSGI_APPLICATION = "myday_project.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "core.context.myday",
            ],
        },
    },
]

# "default" = MyDay's own small database (sessions, PIN lockouts, check-in flags).
# "bowlcontrol" = Vensoft / BowlControl on VM 102, via the limited myday_app login.
DATA_DIR = Path(env("MYDAY_DATA_DIR", BASE_DIR / "data"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "myday.sqlite3",
    },
    "bowlcontrol": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("BC_DB_HOST", "192.168.0.252"),
        "PORT": env("BC_DB_PORT", "5432"),
        "NAME": env("BC_DB_NAME", "bowlcontrol"),
        "USER": env("BC_DB_USER", "myday_app"),
        "PASSWORD": env("BC_DB_PASSWORD", ""),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"options": "-c timezone=Australia/Brisbane"},
    },
    # "lanes" = Lane Marshal's trouble calls (Suncity_Database, same server), for the Mechanic screen.
    "lanes": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("LANES_DB_HOST", env("BC_DB_HOST", "192.168.0.252")),
        "PORT": env("LANES_DB_PORT", env("BC_DB_PORT", "5432")),
        "NAME": env("LANES_DB_NAME", "Suncity_Database"),
        "USER": env("LANES_DB_USER", env("BC_DB_USER", "myday_app")),
        "PASSWORD": env("LANES_DB_PASSWORD", env("BC_DB_PASSWORD", "")),
        "CONN_MAX_AGE": 60,
    },
}
DATABASE_ROUTERS = ["myday_project.routers.BowlControlRouter"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-au"
TIME_ZONE = "Australia/Brisbane"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# --- Cloudflare Access ---------------------------------------------------
# Team domain, e.g. "suncity.cloudflareaccess.com", and the Access app's AUD tag.
CF_ACCESS_TEAM_DOMAIN = env("CF_ACCESS_TEAM_DOMAIN", "")
CF_ACCESS_AUD = env("CF_ACCESS_AUD", "")
# Only for testing on the LAN before Cloudflare is set up. Never set in production.
DEV_USER_EMAIL = env("MYDAY_DEV_USER_EMAIL", "")

# --- MyDay behaviour -------------------------------------------------------
PIN_IDLE_MINUTES = int(env("MYDAY_PIN_IDLE_MINUTES", "10"))
PIN_MAX_ATTEMPTS = int(env("MYDAY_PIN_MAX_ATTEMPTS", "5"))
PIN_LOCKOUT_MINUTES = int(env("MYDAY_PIN_LOCKOUT_MINUTES", "5"))

# Sun City Tenpin, 8 Mari St, Alexandra Headland. Confirm on a map before go-live.
VENUE_LAT = float(env("MYDAY_VENUE_LAT", "-26.6720"))
VENUE_LNG = float(env("MYDAY_VENUE_LNG", "153.1060"))
VENUE_RADIUS_M = int(env("MYDAY_VENUE_RADIUS_M", "200"))
# The venue's public internet address(es); a phone on the venue wifi counts as on site.
VENUE_PUBLIC_IPS = env_list("MYDAY_VENUE_PUBLIC_IPS", "")

# Writes into BowlControl stay off until Fletcher confirms the tables and values.
WRITES_ENABLED = env_bool("MYDAY_WRITES_ENABLED", False)
# RDO / leave requests can be switched on by themselves, ahead of the other writes.
# Location check at Log on / Log off for anyone without their own setting (manage.py location_check).
LOCATION_CHECK_DEFAULT = env_bool("MYDAY_LOCATION_CHECK", False)
# Log on / Log off into the Vensoft time log, likewise on its own.
CLOCK_WRITES_ENABLED = WRITES_ENABLED or env_bool("MYDAY_CLOCK_WRITES", False)
LEAVE_WRITES_ENABLED = WRITES_ENABLED or env_bool("MYDAY_LEAVE_WRITES", False)
# Lane calls (Lane Marshal's trouble calls): the Mechanic and Lane Marshal screens, for staff
# switched on with manage.py mechanic / manage.py marshal.
MECHANIC_ENABLED = env_bool("MYDAY_MECHANIC", False)
LANE_COUNT = int(env("MYDAY_LANES", "20"))
# The faults a lane marshal can report (same list as Lane Marshal's /marshall screen).
MARSHAL_CALLS = env_list(
    "MYDAY_MARSHAL_CALLS",
    "Full Set,Full Set on Second Ball,Ball Down the Back,Off Spot,Interlock,Scored – No Cycle,Machine Off,Other",
)
MECHANIC_POLL_SECONDS = int(env("MYDAY_MECHANIC_POLL_SECONDS", "5"))
# Which membership/bowl MyDay acts for when a staff member has several.
DEFAULT_BOWL_ID = env("MYDAY_BOWL_ID", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
