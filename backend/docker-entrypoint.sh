#!/bin/sh
# Runs migrations before handing off to the container's main command
# (e.g. `runserver`), so `docker compose up` always starts from an
# up-to-date schema without a separate manual step.
set -e

python manage.py migrate --noinput

exec "$@"
