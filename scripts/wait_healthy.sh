#!/usr/bin/env bash
# Wait for all compose services to report healthy. Used by CI.
set -e
for i in {1..60}; do
  unhealthy=$(docker compose ps --format json | grep -c '"Health":"unhealthy"' || true)
  starting=$(docker compose ps --format json | grep -c '"Health":"starting"' || true)
  if [ "$unhealthy" -eq 0 ] && [ "$starting" -eq 0 ]; then
    echo "all healthy"
    exit 0
  fi
  sleep 2
done
echo "timed out waiting for healthy"
docker compose ps
exit 1
