"""
Django settings for config project.
"""

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

# Read from the environment (Docker / server); the defaults keep local
# development working with no .env changes.
SECRET_KEY = env("SECRET_KEY", default='django-insecure-1-zfx!x3ph)0qskat^*o7)p61^+t$3wv_0hi+cnxlf75mph4@c')
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# Behind nginx: trust its X-Forwarded-Proto so Django knows the request was https.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'tenants',
    'items',
    'parties',
    'units',
    'tax',
    'bank',
    'currency',
    'branch',
    'hsn',
    'docnumbering',
    'approvals',
    'common',
    'purchase',
    'inventory',
    'sales',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# one-line: the ONLY DATABASES definition in the file now — no dead SQLite
# block sitting above it waiting to silently win if this ever gets reordered.
DATABASES = {
    'default': env.db('DATABASE_URL')
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
# collectstatic target, served by WhiteNoise (Django admin CSS/JS in Docker).
STATIC_ROOT = BASE_DIR / 'staticfiles'

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS", default=["http://localhost:5173", "http://127.0.0.1:5173"]
)
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "common.validation.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": (
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
    ),
    "DEFAULT_PARSER_CLASSES": (
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
    ),
}

# E-invoice / e-way bill provider: "sandbox" (built-in, no network) or
# "gsp" (live integration - credentials from EINVOICE_GSP_* env vars).
EINVOICE_PROVIDER = env("EINVOICE_PROVIDER", default="sandbox")
