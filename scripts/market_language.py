"""Shared language handling for Livlogy market-data collectors."""

from __future__ import annotations

import re
from pathlib import Path


SUPPORTED_LANGUAGES = ("en", "zh-Hans", "zh-Hant", "ja", "ko", "es", "fr", "de", "pt-BR", "ru")

TRADINGVIEW_LANGUAGES = {
    "en": "en",
    "zh-Hans": "zh_CN",
    "zh-Hant": "zh_TW",
    "ja": "ja",
    "ko": "ko",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "pt-BR": "pt",
    "ru": "ru",
}

COINGECKO_LANGUAGES = {
    "en": "en",
    "zh-Hans": "zh",
    "zh-Hant": "zh-tw",
    "ja": "ja",
    "ko": "ko",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "pt-BR": "pt",
    "ru": "ru",
}

BABEL_LOCALES = {
    "en": "en",
    "zh-Hans": "zh_Hans",
    "zh-Hant": "zh_Hant",
    "ja": "ja",
    "ko": "ko",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "pt-BR": "pt_BR",
    "ru": "ru",
}

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCALIZED_NAME_CACHE_DIR = PROJECT_DIR / "MarketData" / "LocalizedNames"

VERIFIED_STOCK_NAME_OVERRIDES = {
    "zh-Hant": {
        "HKD|0088.HK": "大昌集團",
        "HKD|0096.HK": "友成控股",
    },
}


def localized_name_cache_path(domain: str, language: str) -> Path:
    return LOCALIZED_NAME_CACHE_DIR / language / f"{domain}.json"


def localized_filename(filename: str, language: str) -> str:
    path = Path(filename)
    return filename if language == "en" else f"{path.stem}.{language}{path.suffix}"


def valid_stock_localization(value: object, language: str) -> bool:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() == "none":
        return False
    if language == "zh-Hant":
        return re.search(r"[\u3400-\u9fff]", value) is not None
    return True


def verified_stock_name_overrides(language: str) -> dict[str, str]:
    return VERIFIED_STOCK_NAME_OVERRIDES.get(language, {})