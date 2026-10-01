"""
halt_engine.py
--------------
Motor de detecciones de trading halts usando el RSS gratuito de Nasdaq Trader.

Fuente oficial (gratis, sin API key):
  http://www.nasdaqtrader.com/rss.aspx?feed=tradehalts

Se actualiza aproximadamente una vez por minuto. No consultar más de 1 vez/minuto.

Uso:
  from halt_engine import HaltEngine
  he = HaltEngine()
  status = he.get_halt_status("BTTC")
  # {"halted": False, "reason": None, "halt_time": None, "resumption": None, "quality": "HIGH"}
"""

from __future__ import annotations

import logging
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger("halt_engine")

NASDAQ_HALT_RSS = "http://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
CACHE_TTL_SECONDS = 55  # no golpear el RSS más de ~1 vez/minuto


class HaltEngine:
    """Caché en memoria del feed de halts + consulta por símbolo."""

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._cache_ts: float = 0.0
        self._last_error: Optional[str] = None
        self._refresh_lock = threading.Lock()


    def _refresh_if_needed(self) -> None:
        now = time.time()
        if now - self._cache_ts < CACHE_TTL_SECONDS and self._cache:
            return
        with self._refresh_lock:
            now = time.time()
            if now - self._cache_ts < CACHE_TTL_SECONDS and self._cache:
                return
            try:
                resp = requests.get(NASDAQ_HALT_RSS, timeout=8)
                resp.raise_for_status()
                root = ET.fromstring(resp.content)

                items = root.findall(".//item") or root.findall(".//{*}item")
                new_cache: dict[str, dict] = {}

                for item in items:
                    title = (item.findtext("title") or item.findtext("{*}title") or "").strip()
                    description = (item.findtext("description") or item.findtext("{*}description") or "").strip()
                    pub = (item.findtext("pubDate") or item.findtext("{*}pubDate") or "").strip()

                    symbol = None
                    for token in title.replace(",", " ").split():
                        tok = token.strip().upper().replace(":", "")
                        if 1 <= len(tok) <= 6 and tok.isalpha():
                            symbol = tok
                            break
                    if not symbol:
                        for part in description.replace("\n", " ").split():
                            if part.upper().startswith("SYMBOL") or len(part) <= 6:
                                cand = part.strip().upper()
                                if cand.isalpha() and 1 <= len(cand) <= 6:
                                    symbol = cand
                                    break
                    if not symbol:
                        continue

                    reason = None
                    for code in ("T1", "T2", "T3", "T5", "T6", "T12", "H4", "H9", "H10", "H11", "LUDP", "LUDS", "MWC"):
                        if code in description.upper() or code in title.upper():
                            reason = code
                            break
                    if not reason:
                        reason = "HALT"

                    new_cache[symbol] = {
                        "halted": True,
                        "reason": reason,
                        "title": title,
                        "description": description[:300],
                        "pub_date": pub,
                        "source": "nasdaq_trader_rss",
                    }

                self._cache = new_cache
                self._cache_ts = now
                self._last_error = None
                logger.info(f"HaltEngine: {len(new_cache)} símbolos en halt/pause activos (RSS Nasdaq).")
            except Exception as e:
                self._last_error = str(e)
                logger.warning(f"HaltEngine: no se pudo refrescar RSS de Nasdaq: {e}")

    def get_halt_status(self, symbol: str) -> dict:
        """
        Devuelve el estado de halt para un símbolo.

        Campos:
          halted (bool)
          reason (str|None)
          quality ("HIGH" si el feed respondió, "LOW" si falló el RSS)
          source
        """
        symbol = (symbol or "").strip().upper()
        self._refresh_if_needed()

        if self._last_error and not self._cache:
            return {
                "halted": False,
                "reason": None,
                "quality": "LOW",
                "source": "nasdaq_trader_rss",
                "note": f"RSS no disponible: {self._last_error}",
            }

        info = self._cache.get(symbol)
        if info:
            return {
                "halted": True,
                "reason": info.get("reason"),
                "quality": "HIGH",
                "source": "nasdaq_trader_rss",
                "title": info.get("title"),
                "pub_date": info.get("pub_date"),
            }

        return {
            "halted": False,
            "reason": None,
            "quality": "HIGH" if self._cache_ts > 0 else "LOW",
            "source": "nasdaq_trader_rss",
        }

    def is_halted(self, symbol: str) -> bool:
        return bool(self.get_halt_status(symbol).get("halted"))


# Singleton simple para reutilizar la caché entre llamadas del screener
_default_engine: Optional[HaltEngine] = None


def get_halt_engine() -> HaltEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = HaltEngine()
    return _default_engine
