#!/bin/sh
# Container start: bring the database schema up to date, then run the
# server command (gunicorn by default, or whatever compose passes).
set -e

python manage.py migrate --noinput

exec "$@"
