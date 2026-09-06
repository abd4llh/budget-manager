# Upgrading

Before every upgrade:

1. Read the release notes.
2. Export a database backup and media backup.
3. Pull/extract the new release.
4. Review `.env.example` for new settings; do not overwrite your `.env` blindly.
5. Rebuild and restart:

```bash
docker compose build --pull web scheduler
docker compose up -d
```

6. Verify migrations and tests:

```bash
docker compose exec web python manage.py showmigrations finance
docker compose exec web ./scripts/selfcheck.sh
```

Django migrations are applied automatically by the web container entrypoint. Never delete the PostgreSQL volume to perform a normal upgrade.
