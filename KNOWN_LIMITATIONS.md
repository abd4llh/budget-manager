# Known limitations — 0.9.0-beta

This is a public beta. It has been tested on the reference deployment but has not received an independent security audit.

- Budget Manager does not terminate TLS itself. Remote access should use HTTPS at a trusted reverse proxy or a private VPN.
- The application relies on normal Django authentication controls and does not yet include a dedicated application-level brute-force/rate-limit subsystem. Internet-facing reverse proxies should rate-limit login endpoints.
- Android permits cleartext HTTP so it can connect to trusted LAN servers by IP address. Use HTTPS or a trusted VPN outside the LAN.
- The native Android app focuses on everyday workflows. Some infrequent administration still opens the web interface.
- There is no iOS client.
- LAN discovery uses UDP broadcast/host networking and is primarily intended for Linux Docker hosts. Manual server URL entry is always available.
- Enable Banking support is optional and depends on the operator's own provider account, bank eligibility, consent limits, and regional support.
- Mobile bearer tokens expire after 90 days and are revocable, but device-management UI is minimal in this beta.
- Automatic daily backups live in a Docker named volume unless exported. Operators should export and copy verified backups off-host.
- Smart Review categorisation is deterministic/learned rather than machine-learning based. Unknown merchants may initially fall back to `Other` / `Other income`; corrections can improve later suggestions.
