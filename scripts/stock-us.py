import argparse
import json
import time
from pathlib import Path

import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

from market_language import SUPPORTED_LANGUAGES, localized_filename

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "MarketData" / "Generated"
ARCHIVE_DIR = OUTPUT_DIR / "Archive"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
FIXED_OUTPUT_FILE = "us_market.json"
REQUEST_DELAY_SECONDS = 2.0
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
}
NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
MARKET_METADATA = {}
TICKER_SOURCE = "fallback"

FALLBACK_TICKERS = [
    "AAPL", "NVDA", "MSFT", "TSLA", "SPY", "QQQ", "VOO", "IWM", "META", "AMZN",
    "GOOGL", "AVGO", "AMD", "UNH", "JPM", "XOM", "CVX", "V", "PG", "WMT",
    "LLY", "COST", "XLE", "XLF", "XLK", "XLC", "XLU", "PLTR", "BRK.B", "NKE"
]


INVALID_SYMBOLS = {
    "CONTAINER", "SUMMARY", "DIVIDENDS", "LABEL", "HISTORICAL", "NASDAQ",
    "NYSE", "AMEX", "INDEX", "SECTOR", "ETF", "STOCK", "PAGE", "SEARCH",
    "ABOUT", "CONTACT", "LOGIN", "SIGNUP"
}


def normalize_symbol(symbol):
    cleaned = (symbol or "").strip()
    if not cleaned:
        return ""
    return cleaned.upper()


def is_valid_symbol_candidate(symbol):
    sym = normalize_symbol(symbol)
    if not sym or len(sym) > 10:
        return False
    if sym.replace("/", "").replace(".", "").replace("-", "") in INVALID_SYMBOLS:
        return False
    if not all(char.isalnum() or char in ".-/" for char in sym):
        return False
    return True


def yahoo_lookup_symbol(symbol):
    cleaned = (symbol or "").strip().upper()
    if not cleaned:
        return ""
    return cleaned.replace("/", "-").replace(".", "-")


def fetch_market_tickers():
    global TICKER_SOURCE

    try:
        response = requests.get(NASDAQ_SCREENER_URL, headers=REQUEST_HEADERS, timeout=30)
        response.raise_for_status()
        rows = ((response.json().get("data") or {}).get("rows") or [])
        symbols = []
        for row in rows:
            symbol = normalize_symbol(row.get("symbol"))
            if not is_valid_symbol_candidate(symbol):
                continue
            symbols.append(symbol)
            MARKET_METADATA[symbol] = {
                "name": row.get("name") or symbol,
                "sector": row.get("sector") or "N/A",
                "exchange": "NASDAQ/NYSE/AMEX",
            }
        if len(symbols) >= 1_000:
            TICKER_SOURCE = "nasdaq_screener"
            return sorted(set(symbols))
    except Exception as error:
        print(f"[-] NASDAQ screener failed: {error}")

    symbols = []
    for source_url, symbol_key, exchange_key in [
        (NASDAQ_LISTED_URL, "Symbol", None),
        (OTHER_LISTED_URL, "ACT Symbol", "Exchange"),
    ]:
        try:
            response = requests.get(source_url, headers=REQUEST_HEADERS, timeout=30)
            response.raise_for_status()
            lines = response.text.splitlines()
            headers = lines[0].split("|")
            for line in lines[1:]:
                values = line.split("|")
                if len(values) != len(headers):
                    continue
                row = dict(zip(headers, values))
                symbol = normalize_symbol(row.get(symbol_key))
                if row.get("Test Issue") == "Y" or not is_valid_symbol_candidate(symbol):
                    continue
                symbols.append(symbol)
                MARKET_METADATA[symbol] = {
                    "name": row.get("Security Name") or symbol,
                    "sector": "N/A",
                    "exchange": row.get(exchange_key) if exchange_key else "NASDAQ",
                }
        except Exception as error:
            print(f"[-] NASDAQ Trader source failed: {error}")

    if len(symbols) >= 1_000:
        TICKER_SOURCE = "nasdaq_trader"
        return sorted(set(symbols))

    return FALLBACK_TICKERS

# ==========================================
# 1. Fetch Today's Live Ticker List
# ==========================================
parser = argparse.ArgumentParser(description="Generate the Livlogy US stock market feed.")
parser.add_argument("--limit", type=int, help="Limit symbols for a smoke test.")
parser.add_argument("--language", choices=SUPPORTED_LANGUAGES, default="en")
args = parser.parse_args()

print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Fetching latest US stock and ETF ticker list...")

try:
    all_tickers = fetch_market_tickers()
    all_tickers = sorted(set(all_tickers))
    if not all_tickers:
        raise ValueError("No live tickers returned")
except Exception as e:
    print(f"[-] Failed to fetch live ticker list: {e}. Switching to fallback core tickers.")
    all_tickers = FALLBACK_TICKERS
if args.limit is not None:
    all_tickers = all_tickers[:max(args.limit, 0)]

# Unique timestamp setups for HKT (UTC+8)
hkt_zone = timezone(timedelta(hours=8))
now_hkt = datetime.now(hkt_zone)
last_updated_str = now_hkt.strftime("%Y-%m-%dT%H:%M:%00+08:00")

total_tickers = len(all_tickers)
print(f"[+] Total tickers identified for today: {total_tickers}")

# ==========================================
# 2. Batch Download and Format Data to JSON
# ==========================================
BATCH_SIZE = 100
json_data_list = []

for i in range(0, total_tickers, BATCH_SIZE):
    batch = all_tickers[i:i + BATCH_SIZE]
    print(f"[>] Processing tickers {i} to {min(i + BATCH_SIZE, total_tickers)}... ", end="", flush=True)

    try:
        lookup_batch = [yahoo_lookup_symbol(ticker) for ticker in batch]
        market_data = yf.download(
            lookup_batch,
            period="5d",
            interval="1d",
            group_by='ticker',
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

                ticker_df = ticker_df.dropna(subset=['Close'])
                if ticker_df.empty:
                    continue

                last_row = ticker_df.iloc[-1]
                close_price = float(last_row.get('Close', 0.0) or 0.0)
                open_price = float(last_row.get('Open', close_price) or close_price)
                high_price = float(last_row.get('High', close_price) or close_price)
                low_price = float(last_row.get('Low', close_price) or close_price)
                volume_val = int(last_row.get('Volume', 0) or 0)

                if pd.isna(close_price) or close_price <= 0 or volume_val <= 0:
                    continue

                metadata = MARKET_METADATA.get(ticker, {})
                name = metadata.get('name') or f"{ticker} Inc."
                currency = 'USD'
                sector = metadata.get('sector') or 'N/A'
                exchange = metadata.get('exchange') or 'NASDAQ/NYSE/AMEX'
                prev_close = float(ticker_df.iloc[-2]['Close']) if len(ticker_df) > 1 else open_price
                market_state = 'CLOSED'

                price_change = round(close_price - float(prev_close), 2)

                item_json = {
                    "symbol": ticker,
                    "name": name,
                    "price": round(close_price, 2),
                    "change": price_change,
                    "currency": currency,
                    "market": "America/New_York",
                    "sector": sector,
                    "exchange": exchange,
                    "previousClose": round(float(prev_close), 2),
                    "volume": volume_val,
                    "dayHigh": round(high_price, 2),
                    "dayLow": round(low_price, 2),
                    "marketState": market_state,
                    "updatedAt": last_updated_str,
                    "snapshotAt": last_updated_str,
                    "isStale": False,
                }
                json_data_list.append(item_json)
            except Exception:
                continue

        print("Success")
    except Exception as e:
        print(f"Failed: {e}")

    time.sleep(REQUEST_DELAY_SECONDS)

# ==========================================
# 3. Compile Master JSON Object & Save
# ==========================================
master_json = {
    "timezone": "Asia/Hong_Kong",
    "_lastUpdated": last_updated_str,
    "metadata": {
        "market": "us",
        "source": TICKER_SOURCE,
        "discoveredCount": total_tickers,
        "stockCount": len(json_data_list),
        "failedCount": total_tickers - len(json_data_list),
        "batchSize": BATCH_SIZE,
        "batchDelaySeconds": REQUEST_DELAY_SECONDS,
        "requestedLanguage": args.language,
        "nameLanguage": "en",
        "localizationFallback": args.language != "en",
    },
    "data": json_data_list
}

# Keep a stable filename for current clients and a dated snapshot for history.
output_paths = [
    OUTPUT_DIR / localized_filename(FIXED_OUTPUT_FILE, args.language),
    ARCHIVE_DIR / localized_filename(f"us_market_{now_hkt:%Y%m%d}.json", args.language),
]
for output_path in output_paths:
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(master_json, f, indent=2, ensure_ascii=False)

print(f"\n==========================================")
print(f"[+] JSON formatting completed successfully!")
print(f"[+] Total tickers compiled into JSON array: {len(json_data_list)}")
for output_path in output_paths:
    print(f"[+] Destination file: {output_path}")
print(f"==========================================")
