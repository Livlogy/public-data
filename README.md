# Livlogy Public Market Data

Generated stock, crypto, and currency quote data published by the
`market-data-sync` GitHub Action in [Livlogy/macos](https://github.com/Livlogy/macos).

This repository contains only generated JSON snapshots. No source code,
credentials, or private data are published here.

## Contents

- `MarketData/Generated/us_market.json`
- `MarketData/Generated/uk_market.json`
- `MarketData/Generated/hk_market.json`
- `MarketData/Generated/crypto_market.json`
- `MarketData/Generated/currency_market.json`
- `MarketData/Generated/payout_market.json`

Files are overwritten on each scheduled run; see the workflow in the private
repo for details.

## Automation

`.github/workflows/market-data-sync.yml` runs the collectors in `scripts/`
daily (and on manual dispatch), then commits any changed files under
`MarketData/Generated/` using the workflow's own `GITHUB_TOKEN` — no secrets
are required beyond the optional `COINGECKO_DEMO_API_KEY`. `scripts/` and
`Config/DefaultCurrencyConfig.json` are copies maintained from
[Livlogy/macos](https://github.com/Livlogy/macos)'s `docs/` collectors.
