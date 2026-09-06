# Installation

## Prerequisites

- Docker Engine with Docker Compose v2
- A Linux server is recommended for LAN discovery; the core web stack works anywhere Docker Compose works
- A browser on the same trusted network for initial setup

## 1. Configure environment

```bash
cp .env.example .env
```

Generate strong secrets. Example on Linux:

```bash
python3 - <<'PY'
import secrets
print('DJANGO_SECRET_KEY='+secrets.token_urlsafe(64))
print('POSTGRES_PASSWORD='+secrets.token_urlsafe(32))
print('BUDGET_ADMIN_PASSWORD='+secrets.token_urlsafe(24))
PY
```

Put the generated values in `.env`. Add the server IP/hostname to `DJANGO_ALLOWED_HOSTS`.

## 2. Start

```bash
docker compose up -d --build
```

Check status:

```bash
docker compose ps
```

Then verify:

```bash
curl -fsS http://127.0.0.1:${BUDGET_PORT:-8015}/health/
docker compose exec web ./scripts/selfcheck.sh
```

## 3. Initial household

Open `http://SERVER-IP:8015`, sign in with `BUDGET_ADMIN_USERNAME` / `BUDGET_ADMIN_PASSWORD`, and complete Initial setup.

The starter setup is intentionally neutral. Add/rename accounts and categories to match the household.

## Android discovery

On Linux, enable the optional discovery profile after the core server is working:

```bash
docker compose --profile discovery up -d
```

The discovery responder listens on UDP 7358 by default. If host networking is not appropriate for the Docker host, skip discovery and type the server URL manually in Android.

## HTTPS / reverse proxy

For remote access, terminate HTTPS at a reverse proxy. Then set values similar to:

```env
DJANGO_ALLOWED_HOSTS=budget.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://budget.example.com
DJANGO_TRUST_PROXY_HEADERS=1
DJANGO_COOKIE_SECURE=1
BANK_SYNC_BASE_URL=https://budget.example.com
```

Only enable `DJANGO_SECURE_SSL_REDIRECT` and HSTS after verifying HTTPS and proxy forwarding, otherwise it is easy to lock yourself out during setup.

## After first setup

Once the administrator account is confirmed, you may clear `BUDGET_ADMIN_PASSWORD` from `.env`; `ensure_admin` will then skip automatic administrator management. Keep `BUDGET_ADMIN_RESET_PASSWORD=0` unless you intentionally want the container to reset that account password on startup.

## Install from the published image

Set this in `.env`:

```env
BUDGET_SERVER_IMAGE=ghcr.io/abd4llh/budget-manager-server:0.9.0-beta
```

Then run:

```bash
docker compose -f compose.release.yaml up -d
```

For long-lived deployments, prefer the immutable digest shown by GHCR after verifying the release image.
