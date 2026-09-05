#!/usr/bin/env python3
"""Collect annual dividend history and upcoming estimates for Livlogy symbols.

Yahoo Finance is a discovery source, not an authoritative corporate-actions
feed. Historical dividend rows are recorded as paid on their ex-dividend date;
calendar dates are used for upcoming events, otherwise the next date is inferred.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import yfinance as yf


PROJECT_DIR = Path(__file__).resolve().parent.parent
GENERATED_DIR = PROJECT_DIR / "MarketData" / "Generated"
ARCHIVE_DIR = GENERATED_DIR / "Archive"
DEFAULT_MARKETS = ("us", "uk", "hk")
UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate payout_market.json from Yahoo Finance discovery data."
    )
    parser.add_argument("symbols", nargs="*", help="Optional Yahoo symbols, for example VOO 0700.HK")
    parser.add_argument("--markets", nargs="+", choices=DEFAULT_MARKETS, default=list(DEFAULT_MARKETS))
    parser.add_argument("--limit", type=int, help="Limit symbols after loading and de-duplicating them")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many sorted symbols before --limit")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent Yahoo requests (default: 6)")
    parser.add_argument("--year", type=int, default=date.today().year, help="Calendar year of payout history to collect")
    parser.add_argument("--include-previous-year", action="store_true", help="Also collect paid history for the preceding year")
    parser.add_argument("--resume", action="store_true", help="Reuse completed rows from the checkpoint")
    parser.add_argument("--merge", action="store_true", help="Preserve existing events for symbols outside this run")
    parser.add_argument("--existing-scope", action="store_true", help="Refresh only symbols already in payout_market.json")
    parser.add_argument("--no-archive", action="store_true", help="Write only the stable snapshot")
    return parser.parse_args()


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date()
    if isinstance(value, (list, tuple)) and value:
        return normalize_date(value[0])
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def finite_nonnegative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def load_catalog(markets: Iterable[str]) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for market in markets:
        path = GENERATED_DIR / f"{market}_market.json"
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
        for item in payload.get("data", []):
            symbol = str(item.get("symbol", "")).strip().upper()
            if symbol:
                yahoo_symbol = f"{symbol}.L" if market == "uk" and "." not in symbol else symbol
                catalog[yahoo_symbol] = {
                    "symbol": symbol,
                    "name": str(item.get("name") or symbol).strip(),
                    "currency": str(item.get("currency") or "USD").strip().upper(),
                    "market": market,
                }
    return catalog


def ticker_currency(ticker: yf.Ticker) -> str:
    try:
        currency = ticker.fast_info.get("currency")
        if isinstance(currency, str) and len(currency) == 3:
            return currency.upper()
    except Exception:
        pass
    return "USD"


def calendar_value(calendar: Any, *keys: str) -> Any:
    if not isinstance(calendar, dict):
        return None
    for key in keys:
        if key in calendar:
            return calendar[key]
    return None


def inferred_interval_days(dividend_dates: list[date]) -> int | None:
    recent_dates = dividend_dates[-6:]
    intervals = [
        (right - left).days
        for left, right in zip(recent_dates, recent_dates[1:])
        if 20 <= (right - left).days <= 400
    ]
    if not intervals:
        return None
    return max(20, min(400, round(statistics.median(intervals))))


def next_estimated_date(dividend_dates: list[date], today: date) -> date | None:
    interval = inferred_interval_days(dividend_dates)
    if not dividend_dates or interval is None:
        return None
    candidate = dividend_dates[-1] + timedelta(days=interval)
    while candidate < today:
        candidate += timedelta(days=interval)
    return candidate


def collect_symbol(
    symbol: str,
    retrieved_at: datetime,
    metadata: dict[str, str] | None = None,
    year: int | None = None,
    include_previous_year: bool = False,
) -> list[dict[str, Any]]:
    ticker = yf.Ticker(symbol)
    history = ticker.dividends
    if history is None or history.empty:
        return []
    dated_amounts = [
        (dividend_date, amount)
        for index, raw_amount in history.items()
        if (dividend_date := normalize_date(index)) is not None
        and (amount := finite_nonnegative(raw_amount)) is not None
    ]
    dividend_dates = sorted(dividend_date for dividend_date, _ in dated_amounts)
    if not dividend_dates:
        return []

    try:
        calendar: Any = ticker.calendar
    except Exception:
        calendar = None
    today = retrieved_at.date()
    selected_year = year or today.year
    selected_years = {selected_year, selected_year - 1} if include_previous_year else {selected_year}
    metadata = metadata or {}
    output_symbol = metadata.get("symbol") or symbol
    name = metadata.get("name") or output_symbol
    currency = metadata.get("currency") or ticker_currency(ticker)
    market = metadata.get("market") or ("hk" if symbol.endswith(".HK") else "uk" if symbol.endswith(".L") else "us")
    source_url = f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/history/?filter=div"
    events = [
        {
            "id": f"yahoo:{output_symbol}:{dividend_date.isoformat()}",
            "symbol": output_symbol,
            "name": name,
            "market": market,
            "declarationDate": None,
            "exDividendDate": dividend_date.isoformat(),
            "recordDate": None,
            "paymentDate": dividend_date.isoformat(),
            "amountPerShare": amount,
            "currency": currency,
            "eligibleShares": None,
            "estimatedHoldingAmount": None,
            "status": "paid",
            "source": {"provider": "Yahoo Finance dividend history", "url": source_url},
            "retrievedAt": iso_datetime(retrieved_at),
        }
        for dividend_date, amount in dated_amounts
        if dividend_date.year in selected_years and dividend_date <= today
    ]

    ex_date = normalize_date(calendar_value(calendar, "Ex-Dividend Date", "ExDividendDate"))
    payment_date = normalize_date(calendar_value(calendar, "Dividend Date", "DividendDate"))
    ex_date = ex_date if ex_date is not None and ex_date >= today else None
    payment_date = payment_date if payment_date is not None and payment_date >= today else None
    estimated_date = ex_date or payment_date or next_estimated_date(dividend_dates, today)
    if estimated_date is None:
        return events
    ex_date = ex_date or estimated_date
    payment_date = payment_date or estimated_date

    last_amount = finite_nonnegative(history.iloc[-1])
    if last_amount is None:
        return events
    estimated_event = {
        "id": f"yahoo:{output_symbol}:{payment_date.isoformat()}",
        "symbol": output_symbol,
        "name": name,
        "market": market,
        "declarationDate": None,
        "exDividendDate": ex_date.isoformat(),
        "recordDate": None,
        "paymentDate": payment_date.isoformat(),
        "amountPerShare": last_amount,
        "currency": currency,
        "eligibleShares": None,
        "estimatedHoldingAmount": None,
        "status": "estimated",
        "source": {
            "provider": "Yahoo Finance discovery estimate",
            "url": source_url,
        },
        "retrievedAt": iso_datetime(retrieved_at),
    }
    if estimated_date.year == selected_year and estimated_event["id"] not in {event["id"] for event in events}:
        events.append(estimated_event)
    return events


def validate_event(event: dict[str, Any]) -> None:
    required = {
        "id", "symbol", "name", "market", "amountPerShare", "currency", "paymentDate",
        "status", "source", "retrievedAt",
    }
    missing = required - event.keys()
    if missing:
        raise ValueError(f"Missing payout fields: {sorted(missing)}")
    if event["status"] not in {"paid", "estimated"}:
        raise ValueError("Yahoo-discovered payouts must be paid history or estimated")
    if event["market"] not in DEFAULT_MARKETS:
        raise ValueError(f"Invalid market for {event['symbol']}")
    if len(event["currency"]) != 3 or event["amountPerShare"] < 0:
        raise ValueError(f"Invalid currency or amount for {event['symbol']}")
    date.fromisoformat(event["paymentDate"])
    date.fromisoformat(event["exDividendDate"])


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    catalog = load_catalog(args.markets)
    symbols = sorted({symbol.strip().upper() for symbol in args.symbols if symbol.strip()})
    if not symbols and args.existing_scope:
        stable_path = GENERATED_DIR / "payout_market.json"
        with stable_path.open(encoding="utf-8") as file:
            existing_symbols = {
                str(event.get("symbol", "")).strip().upper()
                for event in json.load(file).get("data", [])
            }
        symbols = sorted(
            yahoo_symbol
            for yahoo_symbol, metadata in catalog.items()
            if metadata.get("symbol", "").upper() in existing_symbols
        )
    if not symbols:
        symbols = sorted(catalog)
    if args.offset < 0:
        raise SystemExit("--offset cannot be negative")
    symbols = symbols[args.offset:]
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be at least 1")
        symbols = symbols[: args.limit]

    retrieved_at = utc_now()
    market_scope = "-".join(args.markets)
    limit_scope = "all" if args.limit is None else str(args.limit)
    year_scope = f"{args.year}-previous" if args.include_previous_year else str(args.year)
    checkpoint = GENERATED_DIR / f".payout_market.{market_scope}.{args.offset}.{limit_scope}.{year_scope}.checkpoint.json"
    completed: dict[str, list[dict[str, Any]]] = {}
    if args.resume and checkpoint.exists():
        with checkpoint.open(encoding="utf-8") as file:
            saved_completed = json.load(file).get("completed", {})
        completed = {
            symbol: events if isinstance(events, list) else [events] if isinstance(events, dict) else []
            for symbol, events in saved_completed.items()
        }

    pending = [symbol for symbol in symbols if symbol not in completed]
    failures: list[str] = []
    print(f"[+] Collecting payouts for {len(symbols)} symbols ({len(pending)} pending)")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                collect_symbol,
                symbol,
                retrieved_at,
                catalog.get(symbol),
                args.year,
                args.include_previous_year,
            ): symbol
            for symbol in pending
        }
        for index, future in enumerate(as_completed(futures), start=1):
            symbol = futures[future]
            try:
                completed[symbol] = future.result()
            except Exception as error:
                failures.append(symbol)
                print(f"[!] {symbol}: {error}")
            if index % 25 == 0 or index == len(pending):
                write_json(checkpoint, {"completed": completed})
                print(f"[+] Completed {index}/{len(pending)} pending symbols")

    events = [event for symbol in symbols for event in completed.get(symbol, [])]
    if args.merge:
        stable_path = GENERATED_DIR / "payout_market.json"
        if stable_path.exists():
            with stable_path.open(encoding="utf-8") as file:
                existing_events = json.load(file).get("data", [])
            scanned_output_symbols = {
                (catalog.get(symbol) or {}).get("symbol", symbol) for symbol in symbols
            }
            events.extend(
                event for event in existing_events
                if event.get("symbol") not in scanned_output_symbols
            )
    for event in events:
        validate_event(event)
    events.sort(key=lambda event: (event["paymentDate"], event["symbol"]))
    payload = {
        "schemaVersion": 1,
        "timezone": "UTC",
        "_lastUpdated": iso_datetime(retrieved_at),
        "data": events,
    }
    write_json(GENERATED_DIR / "payout_market.json", payload)
    if not args.no_archive:
        write_json(ARCHIVE_DIR / f"payout_market_{retrieved_at:%Y%m%d}.json", payload)
    if failures:
        write_json(checkpoint, {"completed": completed, "failed": sorted(failures)})
        print(f"[!] {len(failures)} symbols failed and remain retryable with --resume")
    else:
        checkpoint.unlink(missing_ok=True)
    years_label = f"{args.year - 1}-{args.year}" if args.include_previous_year else str(args.year)
    print(f"[+] Wrote {len(events)} payout events for {years_label} to {GENERATED_DIR / 'payout_market.json'}")
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
