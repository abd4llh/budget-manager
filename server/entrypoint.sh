#!/bin/sh
set -eu
if [ -n "${POSTGRES_HOST:-}" ]; then
  python - <<'PY'
import os,socket,time
host=os.getenv('POSTGRES_HOST','db'); port=int(os.getenv('POSTGRES_PORT','5432'))
for _ in range(90):
    try:
        with socket.create_connection((host,port),2): break
    except OSError: time.sleep(1)
else: raise SystemExit('Database did not become reachable')
PY
fi
python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear >/dev/null
python manage.py ensure_admin
exec "$@"
