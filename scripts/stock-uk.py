import argparse
import json
import time
from pathlib import Path

import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

from market_language import SUPPORTED_LANGUAGES, TRADINGVIEW_LANGUAGES, localized_filename

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "MarketData" / "Generated"
ARCHIVE_DIR = OUTPUT_DIR / "Archive"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
FIXED_OUTPUT_FILE = "uk_market.json"
REQUEST_DELAY_SECONDS = 2.0
BATCH_SIZE = 100
TRADINGVIEW_SCANNER_URL = "https://scanner.tradingview.com/uk/scan"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}
MARKET_METADATA = {}
TICKER_SOURCE = "fallback"
FALLBACK_TICKERS = ["AZN", "SHEL", "HSBA", "ULVR", "BP"]


def is_valid_lse_symbol(symbol):
    return bool(symbol) and len(symbol) <= 15 and all(
        char.isalnum() or char in ".-/" for char in symbol
    )


def yahoo_lookup_symbol(symbol):
    normalized = symbol.strip().upper().rstrip(".").replace("/", "-").replace(".", "-")
    return f"{normalized}.L"


def fetch_market_tickers(language):
    global TICKER_SOURCE

    payload = {
        "filter": [{"left": "exchange", "operation": "equal", "right": "LSE"}],
        "options": {"lang": TRADINGVIEW_LANGUAGES[language]},
        "markets": ["uk"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "description", "type", "subtype", "exchange", "sector", "currency"],
        "range": [0, 10000],
    }
    response = requests.post(
        TRADINGVIEW_SCANNER_URL,
        json=payload,
        headers=REQUEST_HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json().get("data") or []

    symbols = []
    for row in rows:
        fields = row.get("d") or []
        if len(fields) < 7 or fields[2] != "stock":
            continue
        symbol = str(fields[0] or "").strip().upper()
        if not is_valid_lse_symbol(symbol):
            continue
        symbols.append(symbol)
        MARKET_METADATA[symbol] = {
            "name": fields[1] or symbol,
            "assetType": fields[3] or "stock",
            "exchange": fields[4] or "LSE",
            "sector": fields[5] or "N/A",
            "currency": fields[6] or "GBX",
        }

    if len(symbols) < 1_000:
        raise ValueError(f"LSE scanner returned only {len(symbols)} stocks")

    TICKER_SOURCE = "tradingview_lse"
    return sorted(set(symbols))

# ==========================================
# 1. Dynamically Fetch Today's UK Ticker List
# ==========================================
parser = argparse.ArgumentParser(description="Generate the Livlogy UK stock market feed.")
parser.add_argument("--limit", type=int, help="Limit symbols for a smoke test.")
parser.add_argument("--language", choices=SUPPORTED_LANGUAGES, default="en")
args = parser.parse_args()

print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Requesting live asset list from United Kingdom Market (LSE)...")

try:
    all_tickers = fetch_market_tickers(args.language)
except Exception as error:
    print(f"[-] LSE discovery failed: {error}. Switching to fallback core UK tickers.")
    all_tickers = FALLBACK_TICKERS

if args.limit is not None:
    all_tickers = all_tickers[:max(args.limit, 0)]

# Setup timestamps for Hong Kong Time (HKT UTC+8)
hkt_zone = timezone(timedelta(hours=8))
now_hkt = datetime.now(hkt_zone)
last_updated_str = now_hkt.replace(microsecond=0).isoformat()

total_tickers = len(all_tickers)
print(f"[+] Total UK tickers loaded for today: {total_tickers}")

# ==========================================
# 2. Batch Download and Format Data to JSON
# ==========================================
json_data_list = []

for i in range(0, total_tickers, BATCH_SIZE):
    batch = all_tickers[i:i + BATCH_SIZE]
    print(f"[>] Processing UK tickers {i} to {min(i + BATCH_SIZE, total_tickers)}... ", end="", flush=True)

    try:
        lookup_batch = [yahoo_lookup_symbol(ticker) for ticker in batch]
        market_data = yf.download(
            lookup_batch,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
            ignore_tz=False,
        )

        for ticker in batch:
            try:
                lookup_ticker = yahoo_lookup_symbol(ticker)
                is_multi = isinstance(market_data.columns, pd.MultiIndex)
                if is_multi and lookup_ticker in market_data.columns.get_level_values(0):
                    ticker_df = market_data[lookup_ticker]
                elif not is_multi and lookup_ticker in market_data.columns:
                    ticker_df = market_data[[lookup_ticker]]
                else:
                    ticker_df = pd.DataFrame()

                if ticker_df.empty:
                    continue
                ticker_df = ticker_df.dropna(subset=["Close"])
                if ticker_df.empty:
                    continue

                last_row = ticker_df.iloc[-1]
                close_price = float(last_row.get("Close", 0.0) or 0.0)
                open_price = float(last_row.get("Open", close_price) or close_price)
                high_price = float(last_row.get("High", close_price) or close_price)
                low_price = float(last_row.get("Low", close_price) or close_price)
                volume_val = int(last_row.get("Volume", 0) or 0)
                if pd.isna(close_price) or close_price <= 0 or volume_val <= 0:
                    continue

                metadata = MARKET_METADATA.get(ticker, {})
                prev_close = float(ticker_df.iloc[-2]["Close"]) if len(ticker_df) > 1 else open_price
                item_json = {
                    "symbol": ticker,
                    "name": metadata.get("name") or f"LSE Stock {ticker}",
                    "price": round(close_price, 2),
                    "change": round(close_price - prev_close, 2),
                    "currency": metadata.get("currency") or "GBX",
                    "market": "Europe/London",
                    "sector": metadata.get("sector") or "N/A",
                    "exchange": metadata.get("exchange") or "LSE",
                    "previousClose": round(prev_close, 2),
                    "volume": volume_val,
                    "dayHigh": round(high_price, 2),
                    "dayLow": round(low_price, 2),
                    "marketState": "CLOSED",
                    "updatedAt": last_updated_str,
                    "snapshotAt": last_updated_str,
                    "isStale": False,
                }
                json_data_list.append(item_json)
            except Exception:
                continue
        print("Success")
    except Exception as error:
        print(f"Failed: {error}")

    time.sleep(REQUEST_DELAY_SECONDS)

# ==========================================
# 3. Save Master JSON Object
# ==========================================
master_json = {
    "timezone": "Asia/Hong_Kong",
    "_lastUpdated": last_updated_str,
    "metadata": {
        "market": "uk",
        "source": TICKER_SOURCE,
        "discoveredCount": total_tickers,
        "stockCount": len(json_data_list),
        "failedCount": total_tickers - len(json_data_list),
        "batchSize": BATCH_SIZE,
        "batchDelaySeconds": REQUEST_DELAY_SECONDS,
        "requestedLanguage": args.language,
        "nameLanguage": args.language if TICKER_SOURCE == "tradingview_lse" else "und",
        "providerLanguage": TRADINGVIEW_LANGUAGES[args.language],
    },
    "data": json_data_list
}

output_paths = [
    OUTPUT_DIR / localized_filename(FIXED_OUTPUT_FILE, args.language),
    ARCHIVE_DIR / localized_filename(f"uk_market_{now_hkt:%Y%m%d}.json", args.language),
]
for output_path in output_paths:
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(master_json, f, indent=2, ensure_ascii=False)

print(f"\n==========================================")
print(f"[+] UKEX pipeline executed successfully!")
print(f"[+] Total tickers compiled into JSON array: {len(json_data_list)}")
for output_path in output_paths:
    print(f"[+] Output verified and saved: {output_path}")
print(f"==========================================")
