import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from market_language import COINGECKO_LANGUAGES, SUPPORTED_LANGUAGES, localized_filename


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "MarketData" / "Generated"
ARCHIVE_DIR = OUTPUT_DIR / "Archive"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
FIXED_OUTPUT_FILE = "crypto_market.json"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
PER_PAGE = 250
PAGE_DELAY_SECONDS = 1.5
MAX_RETRIES = 5
HKT = timezone(timedelta(hours=8))


def request_page(session, page, language):
    headers = {"Accept": "application/json", "User-Agent": "Livlogy-Market-Pipeline/1.0"}
    api_key = os.getenv("COINGECKO_DEMO_API_KEY")
    if api_key:
        headers["x-cg-demo-api-key"] = api_key
    params = {
        "vs_currency": "usd", "order": "market_cap_desc", "per_page": PER_PAGE,
        "page": page, "sparkline": "false", "price_change_percentage": "24h",
        "locale": COINGECKO_LANGUAGES[language],
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(COINGECKO_URL, params=params, headers=headers, timeout=45)
            if response.status_code == 429:
                delay = max(float(response.headers.get("Retry-After", 0) or 0), attempt * 10.0)
                print(f"[!] Rate limited on page {page}; retrying in {delay:.0f}s")
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"page {page} failed after {MAX_RETRIES} attempts: {error}") from error
            delay = attempt * 5.0
            print(f"[!] Page {page} attempt {attempt} failed; retrying in {delay:.0f}s")
            time.sleep(delay)
    return []


def format_item(coin, timestamp):
    price = coin.get("current_price")
    if price is None or price < 0:
        return None
    percentage = coin.get("price_change_percentage_24h")
    change = coin.get("price_change_24h")
    previous_close = price
    if percentage is not None and percentage > -100:
        previous_close = price / (1 + percentage / 100)
    elif change is not None:
        previous_close = price - change
    return {
        "id": coin.get("id"), "providerId": coin.get("id"),
        "symbol": (coin.get("symbol") or "").upper(),
        "name": coin.get("name") or coin.get("id"),
        "price": price, "change": change if change is not None else price - previous_close,
        "changePercentage24h": percentage, "currency": "USD",
        "market": "Global/24H", "sector": "Cryptocurrency", "exchange": "CoinGecko",
        "previousClose": previous_close, "marketCap": coin.get("market_cap"),
        "marketCapRank": coin.get("market_cap_rank"), "volume": coin.get("total_volume") or 0,
        "dayHigh": coin.get("high_24h"), "dayLow": coin.get("low_24h"),
        "imageURL": coin.get("image"), "marketState": "OPEN",
        "updatedAt": timestamp, "snapshotAt": timestamp,
        "source": "coingecko", "isStale": False,
    }


def write_outputs(payload, now_hkt, language):
    paths = [
        OUTPUT_DIR / localized_filename(FIXED_OUTPUT_FILE, language),
        ARCHIVE_DIR / localized_filename(f"crypto_market_{now_hkt:%Y%m%d}.json", language),
    ]
    for path in paths:
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
    return paths


def main():
    parser = argparse.ArgumentParser(description="Build the Livlogy CoinGecko market catalog")
    parser.add_argument("--language", choices=SUPPORTED_LANGUAGES, default="en")
    args = parser.parse_args()
    now_hkt = datetime.now(HKT)
    timestamp = now_hkt.isoformat(timespec="seconds")
    max_pages = int(os.getenv("CRYPTO_MAX_PAGES", "0"))
    session = requests.Session()
    items = []
    seen_provider_ids = set()
    discovered_count = 0
    duplicate_count = 0
    page = 1

    print(f"[{now_hkt:%Y-%m-%d %H:%M:%S}] Fetching CoinGecko crypto market pages...")
    while max_pages == 0 or page <= max_pages:
        rows = request_page(session, page, args.language)
        if not rows:
            break
        discovered_count += len(rows)
        page_items = [item for item in (format_item(row, timestamp) for row in rows) if item]
        unique_page_items = []
        for item in page_items:
            provider_id = item["providerId"]
            if provider_id in seen_provider_ids:
                duplicate_count += 1
                continue
            seen_provider_ids.add(provider_id)
            unique_page_items.append(item)
        items.extend(unique_page_items)
        print(f"[+] Page {page}: {len(unique_page_items)} unique assets ({len(items)} total)")
        if len(rows) < PER_PAGE:
            break
        page += 1
        time.sleep(PAGE_DELAY_SECONDS)

    payload = {
        "timezone": "Asia/Hong_Kong", "_lastUpdated": timestamp,
        "metadata": {
            "market": "crypto", "source": "coingecko", "currency": "USD",
            "discoveredCount": discovered_count, "assetCount": len(items),
            "duplicateCount": duplicate_count, "pageCount": page if items else 0,
            "perPage": PER_PAGE, "limitedByMaxPages": max_pages > 0,
            "requestedLanguage": args.language, "nameLanguage": args.language,
            "providerLanguage": COINGECKO_LANGUAGES[args.language],
        },
        "data": items,
    }
    paths = write_outputs(payload, now_hkt, args.language)
    print(f"[+] Saved {len(items)} crypto assets")
    for path in paths:
        print(f"[+] {path}")


if __name__ == "__main__":
    main()
