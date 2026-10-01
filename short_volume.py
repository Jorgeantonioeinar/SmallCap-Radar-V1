"""
short_volume.py
---------------
FINRA Daily Short Sale Volume (Reg SHO) — 100% gratis, sin API key.

Fuente:
  https://cdn.finra.org/equity/regsho/daily/CNMSshvolYYYYMMDD.txt

Formato del archivo:
  Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market

Notas importantes para scalping:
  - Es SHORT VOLUME (flujo del día), NO short interest (posición abierta).
  - Solo refleja trades reportados a TRF/ADF de FINRA (off-exchange), no
    el 100% del volumen consolidado. Sirve como CONTEXTO, no como señal
    única de entrada.
  - FINRA publica el archivo del día T el mismo día ~18:00 ET (o al
    siguiente por la mañana). Usamos el día hábil más reciente disponible.

Uso:
  from short_volume import get_short_volume
  info = get_short_volume("BTTC")
  # {"short_volume": ..., "total_volume": ..., "short_pct": 42.5, "date": "20260924", ...}
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger("short_volume")

CDN_TEMPLATE = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
CACHE_TTL_SECONDS = 3600  # 1 hora — el archivo solo cambia 1 vez al día
UA = "Mozilla/5.0 (compatible; SmallCapsBot/7.2; +https://github.com)"

# Caché en memoria: { "YYYYMMDD": { "AAPL": {...}, ... } }
_day_cache: dict[str, dict[str, dict]] = {}
_day_cache_ts: dict[str, float] = {}
_last_good_date: Optional[str] = None
_failed_dates: dict[str, float] = {}  # date -> ts of last 403/fail
_FAILED_TTL = 1800  # 30 min: no reintentar el mismo día fallido


def _candidate_dates(max_lookback: int = 7) -> list[str]:
    """Fechas candidatas (hoy hacia atrás, solo días hábiles aproximados)."""
    now = datetime.now(timezone.utc)
    # FINRA publica ~18:00 ET; si es temprano en el día, el de "hoy" puede no existir
    dates = []
    for i in range(0, max_lookback + 1):
        d = now - timedelta(days=i)
        if d.weekday() >= 5:  # sáb/dom
            continue
        dates.append(d.strftime("%Y%m%d"))
    return dates


def _fetch_day(date_str: str) -> Optional[dict[str, dict]]:
    """Descarga y parsea el archivo consolidado de un día. None si no existe."""
    now = time.time()
    if date_str in _day_cache and (now - _day_cache_ts.get(date_str, 0)) < CACHE_TTL_SECONDS:
        return _day_cache[date_str]

    url = CDN_TEMPLATE.format(date=date_str)
    # No martillar el mismo día si ya dio 403/error
    if date_str in _failed_dates and (time.time() - _failed_dates[date_str]) < _FAILED_TTL:
        return None
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": UA})
        if resp.status_code in (404, 403):
            _failed_dates[date_str] = time.time()
            if resp.status_code == 403:
                logger.debug(f"ShortVolume FINRA {date_str}: {resp.status_code} (no reintentar 30min)")
            return None
        resp.raise_for_status()
        text = resp.text.strip()
        lines = text.splitlines()
        if not lines:
            return None

        mapping: dict[str, dict] = {}
        for line in lines[1:]:  # skip header
            parts = line.split("|")
            if len(parts) < 5:
                continue
            symbol = parts[1].strip().upper()
            try:
                short_vol = float(parts[2]) if parts[2] else 0.0
                short_exempt = float(parts[3]) if parts[3] else 0.0
                total_vol = float(parts[4]) if parts[4] else 0.0
            except ValueError:
                continue
            short_pct = round((short_vol / total_vol) * 100, 2) if total_vol > 0 else None
            mapping[symbol] = {
                "date": date_str,
                "short_volume": short_vol,
                "short_exempt_volume": short_exempt,
                "total_volume": total_vol,
                "short_pct": short_pct,
                "source": "finra_cdn_cnms",
            }

        _day_cache[date_str] = mapping
        _day_cache_ts[date_str] = now
        logger.info(f"ShortVolume FINRA: cargados {len(mapping)} símbolos para {date_str}.")
        return mapping
    except Exception as e:
        logger.warning(f"ShortVolume FINRA ({date_str}) falló: {e}")
        return None


def _resolve_latest_day() -> Optional[str]:
    """Busca el día hábil más reciente con archivo disponible."""
    global _last_good_date
    if _last_good_date and _last_good_date in _day_cache:
        age = time.time() - _day_cache_ts.get(_last_good_date, 0)
        if age < CACHE_TTL_SECONDS:
            return _last_good_date

    for date_str in _candidate_dates():
        data = _fetch_day(date_str)
        if data is not None and len(data) > 0:
            _last_good_date = date_str
            return date_str
    return None


def get_short_volume(symbol: str) -> dict:
    """
    Devuelve short volume del día más reciente disponible para el símbolo.

    Campos:
      short_volume, short_exempt_volume, total_volume, short_pct, date, source
      available (bool)
    """
    symbol = (symbol or "").strip().upper()
    empty = {
        "short_volume": None,
        "short_exempt_volume": None,
        "total_volume": None,
        "short_pct": None,
        "date": None,
        "source": "finra_cdn_cnms",
        "available": False,
    }
    if not symbol:
        return empty

    date_str = _resolve_latest_day()
    if not date_str:
        return empty

    data = _day_cache.get(date_str) or _fetch_day(date_str) or {}
    row = data.get(symbol)
    if not row:
        return {**empty, "date": date_str, "available": False}

    return {**row, "available": True}


def classify_short_pressure(short_pct: Optional[float]) -> str:
    """
    Clasificación orientativa del % short volume FINRA (off-exchange).

    Valores típicos del archivo suelen estar entre 30-55% en muchos nombres
    (market makers). Umbrales orientativos para small-cap momentum:
      < 35  → BAJO
      35-55 → NORMAL
      55-70 → ALTO
      > 70  → MUY_ALTO
    """
    if short_pct is None:
        return "N/D"
    if short_pct < 35:
        return "BAJO"
    if short_pct < 55:
        return "NORMAL"
    if short_pct < 70:
        return "ALTO"
    return "MUY_ALTO"
