# Optional Open Banking integration

Budget Manager can use Enable Banking for read-only account information. This feature is optional; the rest of Budget Manager works without it.

Each server operator must create and maintain their own provider application and private key. Do not distribute one shared application credential in a public container or Android build.

Configure:

```env
ENABLE_BANKING_APP_ID=
ENABLE_BANKING_PRIVATE_KEY_B64=
BANK_SYNC_BASE_URL=https://budget.example.com
```

The callback URL is:

```text
https://budget.example.com/banking/callback/
```

Provider/bank/country availability and consent rules can change independently of Budget Manager. Synced rows are staged in Review before they become ledger transactions.
