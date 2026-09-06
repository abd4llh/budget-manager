# Contributing

Contributions are welcome.

## Before opening a pull request

- Keep personal data, `.env`, bank credentials, database dumps, signing keys, and exported financial files out of commits.
- Run the public-tree check:

```bash
python3 scripts/check-public-tree.py
```

- Run the server checks:

```bash
docker compose exec web ./scripts/selfcheck.sh
```

- For Android changes, build with JDK 17 and Gradle 8.9.

## Categorisation rules

Built-in Review rules live in `server/finance/category_rules.json`. Prefer broadly useful worldwide or European terms and merchants. Keep rules explainable and avoid forcing a specific category when evidence is weak.

## Security issues

Do not open a public issue for a vulnerability that could expose financial information, credentials, authentication tokens, or private data. Use GitHub's private vulnerability reporting / Security Advisories instead.

## License

By contributing, you agree that your contribution is licensed under the repository's AGPL-3.0-only license.
