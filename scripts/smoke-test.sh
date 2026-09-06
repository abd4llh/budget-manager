#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
./scripts/check-env.sh
python3 scripts/check-public-tree.py --allow-runtime-env
docker compose config >/dev/null
docker compose up -d --build db web scheduler backup
printf 'Waiting for web health'
i=0
while [ "$i" -lt 60 ]; do
  if docker compose exec -T web python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8015/health/',timeout=2).read()" >/dev/null 2>&1; then
    echo; break
  fi
  i=$((i+1)); printf '.'; sleep 2
done
[ "$i" -lt 60 ] || { echo; echo 'ERROR: web did not become healthy.' >&2; docker compose logs --tail=100 web; exit 1; }
docker compose exec -T web python manage.py showmigrations finance
docker compose exec -T web ./scripts/selfcheck.sh
docker compose exec -T web python - <<'PY'
import json, urllib.request
for path in ('/health/','/mobile/info/'):
    with urllib.request.urlopen('http://127.0.0.1:8015'+path,timeout=5) as r:
        print(path, json.loads(r.read()))
PY
echo 'Smoke test passed.'
