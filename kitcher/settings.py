"""Local-first settings. Private values are read from var/local.env or environment."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VAR_DIR = Path(os.environ.get("KITCHER_DATA_DIR", BASE_DIR / "var"))
VAR_DIR.mkdir(parents=True, exist_ok=True)
local_env = VAR_DIR / "local.env"
if local_env.exists():
    for line in local_env.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

DEBUG = os.environ.get("KITCHER_DEBUG", "0") == "1"
SECRET_KEY = os.environ.get("KITCHER_SECRET_KEY", "")
if not SECRET_KEY:
    secret_file = VAR_DIR / "django-secret"
    if not secret_file.exists():
        from django.core.management.utils import get_random_secret_key

        try:
            with secret_file.open("x", encoding="utf-8") as stream:
                stream.write(get_random_secret_key())
        except FileExistsError:
            pass
    SECRET_KEY = secret_file.read_text(encoding="utf-8").strip()
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "KITCHER_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]"
    ).split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("KITCHER_CSRF_ORIGINS", "").split(",")
    if origin.strip()
]
HTTPS = os.environ.get("KITCHER_HTTPS", "0") == "1"
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS
SECURE_SSL_REDIRECT = HTTPS
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "pantry",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "kitcher.urls"
WSGI_APPLICATION = "kitcher.wsgi.application"
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
            ]
        },
    }
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("KITCHER_DATABASE", str(VAR_DIR / "kitcher.sqlite3")),
        "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 5},
        "ATOMIC_REQUESTS": False,
        "TEST": {"NAME": str(VAR_DIR / "test-kitcher.sqlite3")},
    }
}
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = VAR_DIR / "static"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "pantry"
LOGOUT_REDIRECT_URL = "login"
DATA_UPLOAD_MAX_MEMORY_SIZE = 11 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024

BACKUP_DIR = Path(os.environ.get("KITCHER_BACKUP_DIR", BASE_DIR / "backups"))
BACKUP_MIRROR = os.environ.get("KITCHER_BACKUP_MIRROR", "")

PHONE_URL = os.environ.get("KITCHER_PHONE_URL", "")
