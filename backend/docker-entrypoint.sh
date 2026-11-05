#!/bin/sh
# MongoDB collections are accessed directly; there are no ORM migrations.
set -e

exec "$@"
