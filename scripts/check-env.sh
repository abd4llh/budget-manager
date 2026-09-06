#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo 'ERROR: copy .env.example to .env first.' >&2; exit 1; }
if grep -Eq '^[A-Z0-9_]+=.*CHANGE_ME' .env; then
  echo 'ERROR: .env still contains CHANGE_ME placeholders.' >&2
  exit 1
fi
value() { sed -n "s/^$1=//p" .env | tail -n 1; }
secret=$(value DJANGO_SECRET_KEY)
dbpass=$(value POSTGRES_PASSWORD)
adminuser=$(value BUDGET_ADMIN_USERNAME)
adminpass=$(value BUDGET_ADMIN_PASSWORD)
[ ${#secret} -ge 32 ] || { echo 'ERROR: DJANGO_SECRET_KEY is missing or too short.' >&2; exit 1; }
[ ${#dbpass} -ge 16 ] || { echo 'ERROR: POSTGRES_PASSWORD is missing or too short.' >&2; exit 1; }
[ -n "$adminuser" ] || { echo 'ERROR: BUDGET_ADMIN_USERNAME is required for first setup.' >&2; exit 1; }
[ ${#adminpass} -ge 12 ] || { echo 'ERROR: BUDGET_ADMIN_PASSWORD must be at least 12 characters for first setup.' >&2; exit 1; }
echo '.env basic validation passed.'
