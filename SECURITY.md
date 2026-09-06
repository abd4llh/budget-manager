# Security Policy

Budget Manager stores financial information and should be deployed conservatively.

## Deployment requirements

- Do not expose Budget Manager over plain HTTP to the public internet.
- Use HTTPS through a trusted reverse proxy or a private VPN for remote access.
- Use strong, unique values for `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, and administrator passwords.
- Do not commit `.env`, bank-provider private keys, database backups, Android signing keys, or exported financial data.
- PostgreSQL is intentionally not published to a host port by the supplied Compose file.
- Android LAN discovery is optional and advertises only product/name/address/version. It does not expose financial data or credentials.
- Set `DJANGO_TRUST_PROXY_HEADERS=1` only when Budget Manager is behind a proxy you control that correctly overwrites forwarded headers.
- After HTTPS is confirmed, enable `DJANGO_COOKIE_SECURE=1`; consider `DJANGO_SECURE_SSL_REDIRECT=1` and HSTS only after verifying the deployment.

## Banking

Enable Banking credentials belong to the server operator. Never publish the private key. Bank rows enter a staging/review inbox and do not alter the ledger until imported.

## Android

The Android client stores the mobile bearer token encrypted with Android Keystore. The password is not retained. Application backup is disabled. Public release APKs must be signed with a private key kept outside the repository.

## Vulnerability reporting

Report vulnerabilities through GitHub private vulnerability reporting / Security Advisories rather than public issues, especially when a report could expose financial data, credentials, or authentication details.
