import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

try:
    from babel.numbers import get_currency_name
except ImportError:
    get_currency_name = None

from market_language import BABEL_LOCALES, SUPPORTED_LANGUAGES, localized_filename


PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "MarketData" / "Generated"
ARCHIVE_DIR = OUTPUT_DIR / "Archive"
CONFIG_FILE = PROJECT_DIR / "Config" / "DefaultCurrencyConfig.json"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
FIXED_OUTPUT_FILE = "currency_market.json"
BATCH_SIZE = 50
BATCH_DELAY_SECONDS = 1.0
HKT = timezone(timedelta(hours=8))


def load_currency_config():
    with CONFIG_FILE.open(encoding="utf-8") as file:
        config = json.load(file)
    codes = [entry["code"] for entry in config["currencies"]]
    return codes, config["defaultExchangeRates"]


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


def localized_pair_name(code, language):
    if get_currency_name is None:
        return f"US Dollar / {code}"
    locale = BABEL_LOCALES[language]
    return f"{get_currency_name('USD', locale=locale)} / {get_currency_name(code, locale=locale)}"


def quote_item(code, frame, fallback_rate, timestamp, language):
    name = localized_pair_name(code, language)
    frame = frame.dropna(subset=["Close"]) if not frame.empty else frame
    if not frame.empty:
        latest = frame.iloc[-1]
        price = float(latest["Close"])
        previous_close = float(frame.iloc[-2]["Close"]) if len(frame) > 1 else float(latest.get("Open", price))
        if pd.notna(price) and price > 0:
            return {
                "symbol": f"USD{code}", "name": name,
                "price": round(price, 6), "change": round(price - previous_close, 6),
                "currency": code, "baseCurrency": "USD", "market": "Global/24H",
                "sector": "Currency / Forex", "exchange": "CCY",
                "previousClose": round(previous_close, 6), "marketState": "OPEN",
                "updatedAt": timestamp, "snapshotAt": timestamp,
                "source": "yahoo_finance", "isStale": False,
            }
    return {
        "symbol": f"USD{code}", "name": name,
        "price": round(float(fallback_rate), 6), "change": 0.0,
        "currency": code, "baseCurrency": "USD", "market": "Global/24H",
        "sector": "Currency / Forex", "exchange": "CCY",
        "previousClose": round(float(fallback_rate), 6), "marketState": "UNKNOWN",
        "updatedAt": timestamp, "snapshotAt": timestamp,
        "source": "default_config", "isStale": True,
    }


def write_outputs(payload, now_hkt, language):
    paths = [
        OUTPUT_DIR / localized_filename(FIXED_OUTPUT_FILE, language),
        ARCHIVE_DIR / localized_filename(f"currency_market_{now_hkt:%Y%m%d}.json", language),
    ]
    for path in paths:
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
    return paths


def main():
    parser = argparse.ArgumentParser(description="Build the Livlogy currency market catalog")
    parser.add_argument("--language", choices=SUPPORTED_LANGUAGES, default="en")
    args = parser.parse_args()
    now_hkt = datetime.now(HKT)
    timestamp = now_hkt.isoformat(timespec="seconds")
    currency_codes, fallback_rates = load_currency_config()
    quote_codes = [code for code in currency_codes if code != "USD"]
    frames = {}

    print(f"[{now_hkt:%Y-%m-%d %H:%M:%S}] Fetching {len(quote_codes)} USD currency pairs...")
    for start in range(0, len(quote_codes), BATCH_SIZE):
        codes = quote_codes[start:start + BATCH_SIZE]
        tickers = [f"USD{code}=X" for code in codes]
        try:
            download = yf.download(
                tickers, period="5d", interval="1d", group_by="ticker",
                auto_adjust=False, threads=True, progress=False, ignore_tz=False,
            )
            for code, ticker in zip(codes, tickers):
                frames[code] = ticker_frame(download, ticker, len(tickers))
        except Exception as error:
            print(f"[-] Batch {start + 1}-{start + len(codes)} failed: {error}")
        time.sleep(BATCH_DELAY_SECONDS)

    items = [quote_item("USD", pd.DataFrame(), 1.0, timestamp, args.language)]
    items[0].update(source="identity", isStale=False)
    for code in quote_codes:
        fallback_rate = fallback_rates.get(code)
        if fallback_rate is None:
            print(f"[-] No live or fallback rate for {code}; skipping")
            continue
        items.append(quote_item(code, frames.get(code, pd.DataFrame()), fallback_rate, timestamp, args.language))

    live_count = sum(not item["isStale"] for item in items)
    payload = {
        "timezone": "Asia/Hong_Kong", "_lastUpdated": timestamp,
        "metadata": {
            "market": "currency", "baseCurrency": "USD",
            "configuredCount": len(currency_codes), "currencyCount": len(items),
            "liveCount": live_count, "fallbackCount": len(items) - live_count,
            "requestedLanguage": args.language,
            "nameLanguage": args.language if get_currency_name is not None else "en",
            "nameSource": "unicode_cldr" if get_currency_name is not None else "canonical_fallback",
            "localizationFallback": get_currency_name is None and args.language != "en",
            "providerLanguage": BABEL_LOCALES[args.language],
        },
        "data": items,
    }
    paths = write_outputs(payload, now_hkt, args.language)
    print(f"[+] Saved {len(items)}/{len(currency_codes)} configured currencies ({live_count} live)")
    for path in paths:
        print(f"[+] {path}")


if __name__ == "__main__":
    main()
