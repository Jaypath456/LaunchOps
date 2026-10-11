#!/bin/sh
# Fixed project and ports prevent accidental interaction with the running copy.
set -eu
cd "$(dirname "$0")/.."
export BACKEND_PORT=38000 FRONTEND_PORT=33000 LIVE_PORT=39090
export REACT_APP_API_URL=http://localhost:38000/api
export REACT_APP_LIVE_WS_URL=ws://localhost:39090
export CORS_ALLOWED_ORIGINS=http://localhost:33000
export DEBUG=False
export DOCKER_BUILDKIT=0
export COMPOSE_PARALLEL_LIMIT=1
exec docker compose --env-file /dev/null -p launchops-review-20260927 \
  -f docker-compose.yml -f docker-compose.review.yml "$@"
