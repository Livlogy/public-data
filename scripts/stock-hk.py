import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from market_language import SUPPORTED_LANGUAGES, TRADINGVIEW_LANGUAGES, localized_filename


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "MarketData" / "Generated"
ARCHIVE_DIR = OUTPUT_DIR / "Archive"
FIXED_OUTPUT_FILE = "hk_market.json"
TRADINGVIEW_SCANNER_URL = "https://scanner.tradingview.com/hongkong/scan"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}
BATCH_SIZE = 80
REQUEST_DELAY_SECONDS = 1.5
HKT = timezone(timedelta(hours=8))
MARKET_METADATA = {}
TICKER_SOURCE = "fallback"
FALLBACK_TICKERS = ["0700.HK", "9988.HK", "3690.HK", "0388.HK", "2800.HK"]


def normalize_hk_symbol(value):
    raw = str(value or "").strip().upper().replace("HKEX:", "").replace(".HK", "")
    if not raw.isdigit() or len(raw) > 5:
        return ""
    return f"{raw.zfill(4)}.HK"


def fetch_market_tickers(language):
    global TICKER_SOURCE
    payload = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "options": {"lang": TRADINGVIEW_LANGUAGES[language]},
        "markets": ["hongkong"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "description", "currency", "exchange", "sector"],
        "range": [0, 10000],
    }
    response = requests.post(
        TRADINGVIEW_SCANNER_URL, json=payload, headers=REQUEST_HEADERS, timeout=45
    )
    response.raise_for_status()
    body = response.json()
    rows = body.get("data") or []
    symbols = []
    for row in rows:
        fields = row.get("d") or []
        if len(fields) < 5:
            continue
        symbol = normalize_hk_symbol(fields[0])
        if not symbol:
            continue
        symbols.append(symbol)
        MARKET_METADATA[symbol] = {
            "name": fields[1] or symbol,
            "currency": fields[2] or "HKD",
            "exchange": fields[3] or "HKEX",
            "sector": fields[4] or "N/A",
        }
    unique_symbols = sorted(set(symbols))
    expected_count = int(body.get("totalCount") or 0)
    if len(unique_symbols) < 1_000:
        raise ValueError(
            f"HK scanner returned only {len(unique_symbols)} of {expected_count} reported stocks"
        )
    TICKER_SOURCE = "tradingview_hkex"
    return unique_symbols


def ticker_frame(download, ticker, ticker_count):
    if download.empty:
        return pd.DataFrame()
    if isinstance(download.columns, pd.MultiIndex):
        if ticker in download.columns.get_level_values(0):
            return download[ticker]
        if ticker in download.columns.get_level_values(1):
            return download.xs(ticker, axis=1, level=1)
        return pd.DataFrame()
    return download if ticker_count == 1 else pd.DataFrame()


def quote_item(ticker, frame, timestamp):
    frame = frame.dropna(subset=["Close"]) if not frame.empty else frame
    if frame.empty:
        return None
    latest = frame.iloc[-1]
    price = float(latest["Close"])
    volume = int(latest.get("Volume", 0) or 0)
    if pd.isna(price) or price <= 0 or volume <= 0:
        return None
    previous_close = float(frame.iloc[-2]["Close"]) if len(frame) > 1 else float(latest.get("Open", price))
    metadata = MARKET_METADATA.get(ticker, {})
    return {
        "symbol": ticker,
        "name": metadata.get("name") or ticker,
        "price": round(price, 3),
        "change": round(price - previous_close, 3),
        "currency": metadata.get("currency") or "HKD",
        "market": "Asia/Hong_Kong",
        "sector": metadata.get("sector") or "N/A",
        "exchange": metadata.get("exchange") or "HKEX",
        "previousClose": round(previous_close, 3),
        "volume": volume,
        "dayHigh": round(float(latest.get("High", price) or price), 3),
        "dayLow": round(float(latest.get("Low", price) or price), 3),
        "marketState": "CLOSED",
        "updatedAt": timestamp,
        "snapshotAt": timestamp,
        "isStale": False,
    }


def write_outputs(payload, now_hkt, language):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    paths = [
        OUTPUT_DIR / localized_filename(FIXED_OUTPUT_FILE, language),
        ARCHIVE_DIR / localized_filename(f"hk_market_{now_hkt:%Y%m%d}.json", language),
    ]
    for path in paths:
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
    return paths


def main():
    parser = argparse.ArgumentParser(description="Build the Livlogy HKEX market catalog")
    parser.add_argument("--limit", type=int, default=0, help="Limit symbols for a quick validation run")
    parser.add_argument("--language", choices=SUPPORTED_LANGUAGES, default="en")
    args = parser.parse_args()
    now_hkt = datetime.now(HKT)
    timestamp = now_hkt.isoformat(timespec="seconds")

    print(f"[{now_hkt:%Y-%m-%d %H:%M:%S}] Fetching the HKEX stock catalog...")
    try:
        tickers = fetch_market_tickers(args.language)
    except Exception as error:
        print(f"[-] HKEX discovery failed: {error}. Using fallback symbols.")
        tickers = FALLBACK_TICKERS
    discovered_count = len(tickers)
    if args.limit > 0:
        tickers = tickers[:args.limit]
    print(f"[+] Processing {len(tickers)} of {discovered_count} discovered symbols")

    items = []
    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start:start + BATCH_SIZE]
        print(f"[>] Quotes {start + 1}-{start + len(batch)}... ", end="", flush=True)
        try:
            download = yf.download(
                batch, period="5d", interval="1d", group_by="ticker",
                auto_adjust=False, threads=True, progress=False, ignore_tz=False,
            )
            batch_items = [
                item for item in (
                    quote_item(ticker, ticker_frame(download, ticker, len(batch)), timestamp)
                    for ticker in batch
                ) if item
            ]
            items.extend(batch_items)
            print(f"{len(batch_items)} saved")
        except Exception as error:
            print(f"failed: {error}")
        time.sleep(REQUEST_DELAY_SECONDS)

    payload = {
        "timezone": "Asia/Hong_Kong",
        "_lastUpdated": timestamp,
        "metadata": {
            "market": "hk",
            "source": TICKER_SOURCE,
            "discoveredCount": discovered_count,
            "requestedCount": len(tickers),
            "stockCount": len(items),
            "failedCount": len(tickers) - len(items),
            "batchSize": BATCH_SIZE,
            "batchDelaySeconds": REQUEST_DELAY_SECONDS,
            "limited": args.limit > 0,
            "requestedLanguage": args.language,
            "nameLanguage": args.language if TICKER_SOURCE == "tradingview_hkex" else "und",
            "providerLanguage": TRADINGVIEW_LANGUAGES[args.language],
        },
        "data": items,
    }
    paths = write_outputs(payload, now_hkt, args.language)
    print(f"[+] Saved {len(items)}/{len(tickers)} requested HKEX quotes")
    for path in paths:
        print(f"[+] {path}")


if __name__ == "__main__":
    main()
