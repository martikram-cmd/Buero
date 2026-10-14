"""Einstellungen für REA. Alle betriebsrelevanten Werte kommen aus Umgebungsvariablen (.env)."""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "ja")


SECRET_KEY = os.environ.get("REA_SECRET_KEY", "nur-fuer-entwicklung-unsicher")
DEBUG = env_bool("REA_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("REA_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("REA_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "buero",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "buero.middleware.LoginRequiredMiddleware",
]

ROOT_URLCONF = "rea.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "buero.context.firma",
            ],
        },
    },
]

WSGI_APPLICATION = "rea.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        env="REA_DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'data' / 'rea.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("REA_MEDIA_ROOT", BASE_DIR / "data" / "media"))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "start"
LOGOUT_REDIRECT_URL = "login"

# Hinter dem Reverse Proxy der Synology (HTTPS endet dort)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env_bool("REA_SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# E-Mail-Versand über das eigene Postfach (SMTP)
EMAIL_BACKEND = os.environ.get("REA_EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("REA_EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("REA_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("REA_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("REA_EMAIL_PASSWORD", "")
EMAIL_USE_TLS = env_bool("REA_EMAIL_TLS", True)
EMAIL_USE_SSL = env_bool("REA_EMAIL_SSL", False)
DEFAULT_FROM_EMAIL = os.environ.get("REA_EMAIL_FROM", EMAIL_HOST_USER or "rea@localhost")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "REA API",
    "DESCRIPTION": "Kunden, Artikel, Rechnungen und Eingangsrechnungen",
    "VERSION": "1.0.0",
}

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"django": {"level": "INFO"}, "buero": {"level": "INFO"}},
}
