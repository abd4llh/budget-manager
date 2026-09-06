# Budget Manager 0.9.0-beta

Budget Manager is a self-hosted household finance application with a web interface and a native Android client.

This first public beta is intended for people who want to keep their budgeting data on infrastructure they control while still having a modern browser and Android workflow.

## Components

- **Server 0.9.0-beta** — Django + PostgreSQL, budgets, accounts, transaction Review, reports, recurring rules, savings goals, loans, reimbursements, optional read-only bank sync, mobile API, backups, and optional LAN discovery.
- **Android 0.9.0-beta** — native Jetpack Compose client with LAN discovery/manual server connection, secure token storage, Dashboard, Review, Budget, Accounts, Reports, Bank Sync, and calculator.

## Highlights

- Self-hosted; there is no central Budget Manager cloud service.
- Docker Compose installation with PostgreSQL 18.
- Native Android client rather than a WebView wrapper.
- Review inbox for bank-imported transactions before they enter the ledger.
- Explainable category suggestions using explicit rules, learned history, and curated worldwide/European matching.
- Category suggestions are always editable before import.
- Salary/pay-cycle-aware budgeting.
- Multi-owner household accounts and reimbursement workflows.
- Optional read-only Open Banking integration through the operator's own Enable Banking application.
- Daily database/media backups plus export/restore helpers.

## Quick start from source

```bash
cp .env.example .env
# Edit .env and replace every CHANGE_ME value.
docker compose up -d --build
```

Open `http://SERVER-IP:8015`, sign in using the administrator credentials configured in `.env`, and complete Initial setup.

Verify the deployment:

```bash
docker compose ps
curl -fsS http://127.0.0.1:8015/health/
docker compose exec web ./scripts/selfcheck.sh
```

The current server test suite contains **47 tests** and passed on the reference Docker deployment used for this beta.

## Install from the published container

After the `0.9.0-beta` GHCR image is available, set in `.env`:

```env
BUDGET_SERVER_IMAGE=ghcr.io/abd4llh/budget-manager-server:0.9.0-beta
```

Then run:

```bash
docker compose -f compose.release.yaml up -d
```

For reproducible production-style deployments, prefer the immutable digest shown by GHCR over a floating tag.

## Optional Android LAN discovery

On a Linux Docker host:

```bash
docker compose --profile discovery up -d
```

The Android app can also connect by manually entering a server URL, so discovery is optional.

## Android

Build requirements and release-signing instructions are in [`docs/ANDROID.md`](docs/ANDROID.md).

Public APKs are signed with one persistent project key. Android will reject future updates signed by a different key, so anyone producing their own builds should keep their signing key safe.

## Remote access

Do **not** expose port 8015 as plain HTTP to the public internet. Use HTTPS at a reverse proxy you control or a trusted private VPN. See [`SECURITY.md`](SECURITY.md) and [`docs/INSTALL.md`](docs/INSTALL.md).

## Bank sync

Enable Banking support is optional. Each server operator supplies their own application ID/private key and is responsible for provider eligibility, consent, and bank/country availability. Budget Manager never ships a shared banking credential.

## Documentation

- [`docs/INSTALL.md`](docs/INSTALL.md) — installation and HTTPS guidance
- [`docs/ANDROID.md`](docs/ANDROID.md) — Android build/signing
- [`docs/BACKUP_RESTORE.md`](docs/BACKUP_RESTORE.md) — backup and restore
- [`docs/OPEN_BANKING.md`](docs/OPEN_BANKING.md) — optional Enable Banking integration
- [`docs/UPGRADING.md`](docs/UPGRADING.md) — upgrade procedure
- [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) — beta limitations
- [`SECURITY.md`](SECURITY.md) — security policy
- [`PRIVACY.md`](PRIVACY.md) — privacy overview

## License

Budget Manager is licensed under the **GNU Affero General Public License v3.0 only (AGPL-3.0-only)**. See [`LICENSE`](LICENSE).
