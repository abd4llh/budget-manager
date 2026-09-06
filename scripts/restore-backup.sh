#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ $# -lt 1 ]; then
  echo "Usage: ./scripts/restore-backup.sh exports/budget-YYYYMMDD-HHMMSS.dump [exports/media-YYYYMMDD-HHMMSS.tgz]" >&2
  exit 2
fi
DUMP=$1
MEDIA=${2:-}
[ -f "$DUMP" ] || { echo "Database dump not found: $DUMP" >&2; exit 2; }
[ -z "$MEDIA" ] || [ -f "$MEDIA" ] || { echo "Media archive not found: $MEDIA" >&2; exit 2; }
[ -f .env ] || { echo ".env is required." >&2; exit 2; }

printf 'This will replace the database in Docker Compose project "%s". Type RESTORE to continue: ' "${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}" >&2
IFS= read -r answer
[ "$answer" = RESTORE ] || { echo "Restore cancelled." >&2; exit 1; }

docker compose stop web scheduler backup
docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < "$DUMP"

if [ -n "$MEDIA" ]; then
  docker compose run --rm -T --no-deps --entrypoint sh web -c 'rm -rf /app/media/* /app/media/.[!.]* /app/media/..?* 2>/dev/null || true; tar -xzf - -C /app/media' < "$MEDIA"
fi

docker compose up -d web scheduler backup
printf 'Restore complete. Check: docker compose ps\n' >&2
