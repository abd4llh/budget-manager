# Changelog

## 0.9.0-beta

First public beta.

### Server

- Dockerized Django + PostgreSQL household finance server.
- Accounts, budgets, salary/pay-cycle budgeting, reports, recurring transactions, savings, loans, multi-owner accounts, reimbursements, and audit history.
- Review inbox for staged bank imports.
- Optional read-only Enable Banking integration using the operator's own credentials.
- Native mobile API with revocable 90-day bearer tokens.
- Optional LAN discovery responder.
- Daily PostgreSQL/media backups and host export/restore helpers.
- Explainable smart Review categorisation:
  - explicit household Import Rules first;
  - previously confirmed counterparty/account/merchant choices next;
  - curated worldwide/European deterministic rules next;
  - safe `Other` / `Other income` fallback instead of choosing the first database category.
- Existing pending Review rows are re-evaluated so stale incorrect fallback suggestions can be corrected without another bank sync.
- Built-in categorisation rules are data-driven in `finance/category_rules.json`.

### Android

- Native Jetpack Compose client.
- Dashboard, Review, Budget, Accounts, Transactions, Reports, Bank Sync, server discovery/manual connection, and calculator.
- Secure mobile token storage with Android Keystore; password is not retained.
- Review category selector available directly on each Review card and in the import confirmation dialog.
- `versionCode` 23, `versionName` `0.9.0-beta`.

### Validation

- Clean public installation tested without private household data.
- Data persistence across container restart verified.
- Native Android client tested against a clean independent server installation.
- Server self-check: **47/47 tests passing** on the reference Docker host.
- Android debug build/install verified with JDK 17 and Gradle 8.9.
