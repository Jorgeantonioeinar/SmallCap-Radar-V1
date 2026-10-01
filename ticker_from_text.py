"""Extrae símbolos y métricas de screener desde texto, CSV y Excel de Moomoo/Webull."""
from __future__ import annotations

import csv
import io
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import List

_STOP = {
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER", "WAS", "ONE",
    "OUR", "OUT", "DAY", "GET", "HAS", "HIM", "HIS", "HOW", "MAN", "NEW", "NOW", "OLD",
    "SEE", "WAY", "WHO", "BOY", "DID", "ITS", "LET", "PUT", "SAY", "SHE", "TOO", "USE",
    "GAP", "RVOL", "RSI", "PMH", "VWAP", "ORB", "ETF", "USA", "USD", "CEO", "CFO",
    "IPO", "ATH", "ATL", "HALT", "OPEN", "CLOSE", "HIGH", "LOW", "LAST", "BID", "ASK",
    "VOL", "AVG", "CHG", "PCT", "PRE", "POST", "AH", "PM", "NYSE", "NASA", "HTTP",
    "HTTPS", "WWW", "COM", "NET", "ORG", "PDF", "API", "SDK", "UI", "UX",
    "LONG", "SHORT", "BUY", "SELL", "HOLD", "STOP", "TIME", "DATE", "TRUE", "FALSE",
    "NONE", "NULL", "SCAN", "LIST", "TOP", "MIN", "MAX", "SUM", "NAME", "PRICE",
    "SYMBOL", "VOLUME", "CHANGE", "MARKET", "CAP", "TURN", "OVER", "INDUSTRY",
    "RATIO", "RANGE", "LOSS", "PREV", "SIZE", "RATE", "FROM", "WITH", "THIS",
    "INC", "LTD", "CORP", "PLC", "CO", "GROUP", "HOLDINGS", "HOLDING", "TECHNOLOGY",
    "TECHNOLOGIES", "LIMITED", "INCORPORATED", "INTERNATIONAL", "GLOBAL", "BIO",
    "PREMARKET", "AFTER", "HOURS", "MINUTES", "WEEKS", "MONTH", "MONTHS", "SPARK",
    "CHART", "GAINERS", "LOSERS", "ACTIVE", "WEBULL", "MOOMOO", "NO", "TICKER",
}
_RE_TICKER_LINE = re.compile(r"^\s*([A-Za-z]{1,5})\s*$")
_RE_TICKER_TOKEN = re.compile(r"\b([A-Za-z]{1,5})\b")
_RE_PCT = re.compile(r"[+\-]?\d+\.?\d*\s*%")
_RE_NUM = re.compile(r"^[\d,\.]+[KMBT]?$", re.I)


@dataclass(frozen=True)
class _ExcelPercent:
    """Un valor numérico de Excel con formato %, almacenado como fracción."""
    value: float


def _clean_token(t: str) -> str:
    t = (t or "").upper().strip()
    if "." in t:
        t = t.split(".")[-1]
    return re.sub(r"[^A-Z]", "", t)


def _is_ticker(t: str) -> bool:
    return bool(t and 2 <= len(t) <= 5 and t not in _STOP and t.isalpha())


def extract_tickers(text: str, max_n: int = 80) -> List[str]:
    if not text or not str(text).strip():
        return []
    text = str(text).replace("\xa0", " ").replace("…", " ")
    out: List[str] = []
    seen = set()

    def add(tok: str):
        tok = _clean_token(tok)
        if _is_ticker(tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)

    for line in text.splitlines():
        line = line.strip()
        if not line or _RE_PCT.search(line) or _RE_NUM.match(line.replace(",", "")):
            continue
        match = _RE_TICKER_LINE.match(line)
        if match:
            add(match.group(1))
    if len(out) < 3:
        cleaned = _RE_PCT.sub(" ", text)
        for match in _RE_TICKER_TOKEN.finditer(cleaned):
            add(match.group(1))
            if len(out) >= max_n:
                break
    return out[:max_n]


def _normalize_header(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", text)


def _find_key(headers, aliases):
    normalized = [(_normalize_header(h), h) for h in headers if h is not None]
    for alias in aliases:
        needle = _normalize_header(alias)
        if not needle:
            continue
        for key, original in normalized:
            if key == needle:
                return original
    # Only permit longer alias fragments inside headers, never the reverse.
    # This avoids matching "Volume" as "Pre-Market Volume" or "Chg" as "% Chg".
    for alias in aliases:
        needle = _normalize_header(alias)
        if len(needle) < 5:
            continue
        for key, original in normalized:
            if needle in key:
                return original
    return None


def _find_percent_header(headers, required_terms):
    """Find a genuinely percent-labelled column; don't confuse Chg with % Chg."""
    for header in headers:
        raw = str(header or "").lower()
        normalized = _normalize_header(header)
        has_percent = "%" in raw or "percent" in raw or "pct" in normalized
        is_session_specific = normalized.startswith((
            "premkt", "premarket", "afterhours", "afterhour", "postmkt", "postmarket", "post", "ah"
        ))
        if has_percent and not is_session_specific and any(term in normalized for term in required_terms):
            return header
    return None


def _find_session_percent_header(headers, session_prefixes):
    for header in headers:
        raw = str(header or "").lower()
        normalized = _normalize_header(header)
        has_percent = "%" in raw or "percent" in raw or "pct" in normalized
        has_change = "chg" in normalized or "change" in normalized
        if has_percent and has_change and normalized.startswith(session_prefixes):
            return header
    return None


_SYMBOL_HEADERS = ("symbol", "ticker", "sym", "code", "stock", "符号")
_PRE_GAP_HEADERS = (
    "pre mkt % chg", "pre mkt %", "premkt % chg", "premkt %", "premarket % chg",
    "pre-market % chg", "pre market % change", "premarket change %", "pre-market change %",
    "pre market change percent", "pre market chg", "premarket chg", "premkt change",
)
_GAP_HEADERS = ("percent change", "change percent", "chg pct", "change %", "% change", "% chg", "chg%")
_RVOL_HEADERS = ("vol ratio", "volume ratio", "rvol", "rel volume", "relative volume")
_PRE_PRICE_HEADERS = ("pre mkt stock price", "pre mkt price", "pre market price", "premarket price", "pre-market price")
_AFTER_PRICE_HEADERS = ("after hours price", "afterhour price", "post mkt price", "post market price", "postmarket price", "ah price")
_PRICE_HEADERS = ("last price", "last", "price", "close")
_FLOAT_HEADERS = ("float shares", "shares float", "free float", "float")
_PRE_VOL_HEADERS = ("pre mkt volume", "pre mkt vol", "premarket volume", "pre-market volume", "pre market volume")
_AFTER_VOL_HEADERS = ("after hours volume", "afterhour volume", "post mkt volume", "post market volume", "postmarket volume", "ah volume")
_VOL_HEADERS = ("volume", "shares traded", "vol")
_MARKET_CAP_HEADERS = ("market cap", "mkt cap", "market capitalization", "marketcap")

# Canonical fields retain the full useful Moomoo row. The fields the current
# scoring engines consume are mapped to overrides below; the rest remain visible
# as market context instead of being silently discarded.
_MOMO_TEXT_FIELDS = {
    "name": ("name",),
    "industry": ("industry",),
}
_MOMO_NUMBER_FIELDS = {
    "price": _PRICE_HEADERS,
    "premarket_price": _PRE_PRICE_HEADERS,
    "afterhours_price": _AFTER_PRICE_HEADERS,
    "change": ("chg", "change"),
    "market_cap": _MARKET_CAP_HEADERS,
    "volume": _VOL_HEADERS,
    "turnover": ("turnover",),
    "bid": ("bid",),
    "ask": ("ask",),
    "free_float": _FLOAT_HEADERS,
    "bid_size": ("bid size",),
    "ask_size": ("ask size",),
    "open": ("open",),
    "prev_close": ("prev close", "previous close"),
    "high": ("high",),
    "low": ("low",),
    "vol_ratio": _RVOL_HEADERS,
    "bid_ask_ratio": ("bid/ask ratio", "bid ask ratio"),
    "pe_lfy": ("p/e lfy", "pe lfy", "p/e"),
    "change_rate": ("change rate",),
    "five_min_change": ("5min chg", "5 min chg", "5 minute change"),
    "five_day_change": ("5d chg", "5 day change"),
    "ten_day_change": ("10d chg", "10 day change"),
    "twenty_day_change": ("20d chg", "20 day change"),
    "sixty_day_change": ("60d chg", "60 day change"),
    "one_hundred_twenty_day_change": ("120d chg", "120 day change"),
    "two_hundred_fifty_day_change": ("250d chg", "250 day change"),
    "ytd_change": ("ytd chg", "ytd change"),
    "premarket_volume": _PRE_VOL_HEADERS,
    "afterhours_volume": _AFTER_VOL_HEADERS,
}
_MOMO_PERCENT_FIELDS = {
    "change_pct": ("chg", "change"),
    "premarket_change_pct": ("chg", "change"),
    "afterhours_change_pct": ("chg", "change"),
    "range_pct": ("range",),
    "turnover_pct": ("turnover",),
}


def _parse_number(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, _ExcelPercent):
        return val.value * 100.0
    if isinstance(val, bool):
        return float(val)
    if isinstance(val, (int, float)):
        try:
            return float(val) if math.isfinite(float(val)) else None
        except Exception:
            return None
    text = str(val).strip().replace("\xa0", "").replace(" ", "").replace("$", "").replace("%", "")
    if not text or text.lower() in ("n/a", "na", "-", "none", "null", "nan"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.rstrip("xX")
    multiplier = 1.0
    if text and text[-1].upper() in "KMBT":
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1].upper()]
        text = text[:-1]
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            text = "".join(parts)
        else:
            text = ".".join(parts)
    try:
        value = float(text) * multiplier
        return -value if negative else value
    except Exception:
        return None


def _find_momo_column(headers, field, aliases):
    if field in _MOMO_PERCENT_FIELDS:
        if field == "change_pct":
            return _find_percent_header(headers, _MOMO_PERCENT_FIELDS[field])
        if field == "premarket_change_pct":
            return _find_session_percent_header(headers, ("premkt", "premarket"))
        if field == "afterhours_change_pct":
            return _find_session_percent_header(headers, ("afterhours", "afterhour", "postmkt", "postmarket", "ah"))
        return _find_percent_header(headers, _MOMO_PERCENT_FIELDS[field])
    if field == "price":
        return _find_key(headers, aliases)
    return _find_key(headers, aliases)


def _entries_from_rows(headers, rows, max_n: int = 80) -> list:
    symbol_key = _find_key(headers, _SYMBOL_HEADERS)
    if symbol_key is None:
        return []

    field_keys = {}
    for field, aliases in _MOMO_TEXT_FIELDS.items():
        field_keys[field] = _find_key(headers, aliases)
    for field, aliases in _MOMO_NUMBER_FIELDS.items():
        field_keys[field] = _find_momo_column(headers, field, aliases)
    for field in _MOMO_PERCENT_FIELDS:
        field_keys[field] = _find_momo_column(headers, field, ())

    # Other optional/session-specific formats.
    if field_keys.get("premarket_volume") is None:
        field_keys["premarket_volume"] = _find_key(headers, _PRE_VOL_HEADERS)

    out, seen = [], set()
    for row in rows:
        raw_symbol = row.get(symbol_key)
        if raw_symbol is None:
            continue
        token = _clean_token(str(raw_symbol).strip().split()[0])
        if not _is_ticker(token) or token in seen:
            continue
        seen.add(token)

        momo = {"symbol": token}
        for field, key in field_keys.items():
            if key is None:
                continue
            value = row.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if field in _MOMO_TEXT_FIELDS:
                momo[field] = str(value).strip()
            elif field == "pe_lfy":
                momo[field] = _parse_number(value)
                if momo[field] is None:
                    momo[field] = str(value).strip()
            else:
                parsed = _parse_number(value)
                if parsed is not None:
                    momo[field] = parsed

        # Preserve future/less common columns too, with normalized header keys.
        known_keys = {key for key in field_keys.values() if key is not None}
        for header in headers:
            if header is None or header == symbol_key or header in known_keys:
                continue
            key = _normalize_header(header)
            value = row.get(header)
            if not key or value is None or (isinstance(value, str) and not value.strip()):
                continue
            parsed = _parse_number(value)
            momo.setdefault(key, parsed if parsed is not None else str(value).strip())

        # These are the columns currently consumed by the scoring formulas.
        entry = {"symbol": token, "momo_data": momo}
        selected_gap = next((momo.get(key) for key in (
            "afterhours_change_pct", "premarket_change_pct", "change_pct"
        ) if momo.get(key) is not None), None)
        if selected_gap is not None:
            entry["gap_override"] = selected_gap
        elif momo.get("price") and momo.get("prev_close"):
            entry["gap_override"] = (momo["price"] - momo["prev_close"]) / momo["prev_close"] * 100.0
        if momo.get("vol_ratio") is not None:
            entry["rvol_override"] = momo["vol_ratio"]
        selected_price = next((momo.get(key) for key in (
            "afterhours_price", "premarket_price", "price"
        ) if momo.get(key) is not None and momo.get(key) > 0), None)
        if selected_price is not None and selected_price > 0:
            entry["price_hint"] = selected_price
        if momo.get("free_float") is not None and momo["free_float"] > 1000:
            entry["float_override"] = momo["free_float"]
        if momo.get("volume") is not None and momo["volume"] >= 0:
            entry["volume_override"] = momo["volume"]
        if momo.get("premarket_volume") is not None and momo["premarket_volume"] >= 0:
            entry["premarket_volume_override"] = momo["premarket_volume"]
        if momo.get("afterhours_volume") is not None and momo["afterhours_volume"] >= 0:
            entry["afterhours_volume_override"] = momo["afterhours_volume"]
        if momo.get("market_cap") is not None and momo["market_cap"] > 0:
            entry["market_cap_override"] = momo["market_cap"]
        if momo.get("prev_close") is not None and momo["prev_close"] > 0:
            entry["prev_close_hint"] = momo["prev_close"]
        out.append(entry)
        if len(out) >= max_n:
            break
    return out


def _decode_csv(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")


def extract_rows_from_csv(file_bytes: bytes, max_n: int = 80) -> list:
    """CSV Moomoo/Webull → métricas completas y overrides relevantes para scoring."""
    text = _decode_csv(file_bytes)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except Exception:
        dialect = csv.excel
    try:
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            return [{"symbol": symbol} for symbol in extract_tickers_from_csv(file_bytes, max_n=max_n)]
        rows = list(reader)
        rich = _entries_from_rows(reader.fieldnames, rows, max_n=max_n)
        return rich or [{"symbol": symbol} for symbol in extract_tickers_from_csv(file_bytes, max_n=max_n)]
    except Exception:
        return [{"symbol": symbol} for symbol in extract_tickers_from_csv(file_bytes, max_n=max_n)]


def _find_header_row(rows):
    for index, row in enumerate(rows[:10]):
        headers = [cell.value for cell in row]
        if _find_key(headers, _SYMBOL_HEADERS):
            return index
    return 0 if rows else None


def extract_rows_from_excel(file_bytes: bytes, filename: str = "screener.xlsx", max_n: int = 80) -> list:
    """Lee XLSX con openpyxl y XLS con pandas/xlrd; mantiene el formato porcentual de Excel."""
    lower_name = (filename or "").lower()
    if lower_name.endswith(".xls") and not lower_name.endswith(".xlsx"):
        import pandas as pd
        frame = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, header=None, dtype=object)
        raw_rows = frame.values.tolist()
        header_index = next((i for i, row in enumerate(raw_rows[:10]) if _find_key(row, _SYMBOL_HEADERS)), None)
        if header_index is None:
            raise ValueError("No encontré la columna Symbol/Ticker en la primera hoja del Excel.")
        headers = raw_rows[header_index]
        rows = [dict(zip(headers, values)) for values in raw_rows[header_index + 1:]]
        return _entries_from_rows(headers, rows, max_n=max_n)

    from openpyxl import load_workbook
    workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    try:
        if not workbook.worksheets:
            raise ValueError("El libro Excel no contiene hojas.")
        sheet = workbook.worksheets[0]
        first_rows = list(sheet.iter_rows(min_row=1, max_row=10))
        header_index = _find_header_row(first_rows)
        if header_index is None:
            raise ValueError("La primera hoja del Excel está vacía.")
        header_cells = first_rows[header_index]
        headers = [cell.value for cell in header_cells]
        symbol_key = _find_key(headers, _SYMBOL_HEADERS)
        if symbol_key is None:
            raise ValueError("No encontré la columna Symbol/Ticker en la primera hoja del Excel.")
        percent_keys = {
            _find_momo_column(headers, field, aliases)
            for field, aliases in _MOMO_PERCENT_FIELDS.items()
        }
        percent_indices = {i for i, value in enumerate(headers) if value in percent_keys}
        rows = []
        for cells in sheet.iter_rows(min_row=header_index + 2):
            values = {}
            for col_idx, cell in enumerate(cells):
                value = cell.value
                if col_idx in percent_indices and isinstance(value, (int, float)) and "%" in (cell.number_format or ""):
                    value = _ExcelPercent(float(value))
                values[headers[col_idx] if col_idx < len(headers) else col_idx] = value
            if any(value is not None for value in values.values()):
                rows.append(values)
                if len(rows) >= max_n * 3:
                    break
        return _entries_from_rows(headers, rows, max_n=max_n)
    finally:
        workbook.close()


def extract_tickers_from_csv(file_bytes: bytes, max_n: int = 80) -> List[str]:
    """CSV Moomoo/Webull: columna Symbol/Ticker, o primera columna."""
    text = _decode_csv(file_bytes)
    out: List[str] = []
    seen = set()

    def add(value):
        token = _clean_token(str(value or "").split()[0])
        if _is_ticker(token) and token not in seen:
            seen.add(token)
            out.append(token)

    try:
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if reader.fieldnames:
            symbol_key = _find_key(reader.fieldnames, _SYMBOL_HEADERS)
            if symbol_key:
                for row in reader:
                    add(row.get(symbol_key))
                    if len(out) >= max_n:
                        return out
                if out:
                    return out
    except Exception:
        pass

    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        start = 1 if rows and re.search(r"symbol|ticker|name", str(rows[0][0] or ""), re.I) else 0
        for row in rows[start:]:
            if row:
                add(row[0])
            if len(out) >= max_n:
                break
    except Exception:
        pass
    return out or extract_tickers(text, max_n=max_n)


def ocr_image_to_text(file_bytes: bytes) -> str:
    try:
        from io import BytesIO
        from PIL import Image
        import pytesseract
        return pytesseract.image_to_string(Image.open(BytesIO(file_bytes))) or ""
    except Exception:
        return ""
