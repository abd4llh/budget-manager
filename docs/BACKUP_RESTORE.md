# Backup and restore

The supplied Compose stack keeps three named volumes:

- `postgres_data` — PostgreSQL database
- `media_data` — receipts/imported files
- `backup_data` — rolling in-Docker daily database/media backups

The `backup` service writes a PostgreSQL custom-format dump and media archive once per day and applies `BACKUP_RETENTION_DAYS`.

## Export a backup to the host

Use the supplied helper so the backup is visible outside Docker named volumes:

```bash
./scripts/export-backup.sh
```

It creates timestamped files under `exports/`:

```text
exports/budget-YYYYMMDD-HHMMSS.dump
exports/media-YYYYMMDD-HHMMSS.tgz
```

Store copies somewhere outside the Docker host if the data matters.

## Restore

Restore only into a Docker Compose project whose state you understand. The helper requires typing `RESTORE` before it replaces the selected project's database:

```bash
./scripts/restore-backup.sh exports/budget-YYYYMMDD-HHMMSS.dump exports/media-YYYYMMDD-HHMMSS.tgz
```

The script stops the web/scheduler/backup services, restores PostgreSQL, restores the named media volume when supplied, then starts the application again.

**Test backup and restore with disposable data before relying on it operationally.** A backup that has never been restored is not a verified backup.
