#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p exports
stamp=$(date +%Y%m%d-%H%M%S)
db="exports/budget-$stamp.dump"
media="exports/media-$stamp.tgz"
docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$db"
docker compose exec -T web tar -czf - -C /app/media . > "$media"
echo "Database backup: $db"
echo "Media backup:    $media"
