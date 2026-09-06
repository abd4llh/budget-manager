# Budget Manager Server

Self-hosted household budgeting, transaction review, account tracking, reports, recurring rules, savings goals, optional read-only bank sync, and the JSON API used by Budget Manager Mobile.

This directory is the Django server component. The public release repository also contains Docker Compose deployment files and the Android client.

## Runtime

- Python 3.13
- Django 5.2 LTS
- PostgreSQL 18
- Gunicorn + WhiteNoise
- Optional Enable Banking integration
- Optional UDP LAN discovery for the Android client

## First start

The Docker entrypoint runs database migrations, collects static files, and optionally creates the first administrator from environment variables. After signing in for the first time, the setup screen creates a household, neutral starter accounts, and optionally a small set of starter categories.

No sample transactions, balances, personal budget plan, banking credentials, or household-specific data are shipped.

## Important deployment rule

Plain HTTP is intended for trusted LAN/VPN use only. For access over the public internet, put Budget Manager behind HTTPS and enable secure cookies. See the repository security and reverse-proxy documentation.

## Bank sync

Bank sync is optional. Each operator must register and configure their own Enable Banking application and comply with the provider and bank terms that apply to them. Budget Manager requests account-information access only; imported bank rows remain in a review inbox until explicitly imported into the ledger.

## Development checks

```bash
python -m compileall finance config
python manage.py check
python manage.py test finance
```

Inside Docker, use:

```bash
docker compose exec web ./scripts/selfcheck.sh
```
