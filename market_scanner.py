"""
market_scanner.py
-------------------
Escáner automático del Top 30 de small caps con mayor Momentum, Gap % y
Spike de volumen del día, SIN necesidad de escribir tickers a mano.

Cadena de redundancia:
  1. TradingView Scanner API (endpoint público no oficial, gratis, sin
     necesidad de clave) - fuente principal, muy rápida y confiable.
  2. Finviz (vía la librería `finvizfinance`) - respaldo si TradingView
     falla o devuelve una lista vacía.
  3. Si ambas fallan: lista vacía [] (nunca revienta el bot).

El resultado siempre se devuelve en el mismo formato que espera
screener.rank_candidates(): una lista de dicts
    {"symbol": "IMRN", "float_override": None, "rvol_override": None}
así se puede usar exactamente igual que la watchlist manual.
"""

import logging

import requests

import config

logger = logging.getLogger("market_scanner")


def get_top30_gappers_spikes():
    """Punto de entrada único: intenta TradingView, si falla cae a Finviz."""
    results = _get_top30_tradingview()
    if not results:
        logger.info("TradingView no devolvió resultados, probando con Finviz...")
        results = _get_top30_finviz()
    return results[:30]


def _to_universe_format(symbols):
    return [{"symbol": s, "float_override": None, "rvol_override": None} for s in symbols]


# ---------------------------------------------------------------------------
# FUENTE PRINCIPAL: TradingView Scanner API
# ---------------------------------------------------------------------------
def _get_top30_tradingview():
    try:
        url = "https://scanner.tradingview.com/america/scan"
        payload = {
            "filter": [
                {"left": "close", "operation": "in_range", "right": [config.SCANNER_PRICE_MIN, config.SCANNER_PRICE_MAX]},
                {"left": "market_cap_basic", "operation": "less", "right": config.SCANNER_MAX_MARKET_CAP},
                {"left": "volume", "operation": "greater", "right": config.SCANNER_MIN_VOLUME},
                {"left": "change", "operation": "greater", "right": config.SCANNER_MIN_CHANGE_PCT},
            ],
            "options": {"lang": "en"},
            "markets": ["america"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": ["name", "close", "change", "volume", "change_from_open", "market_cap_basic"],
            "sort": {"sortBy": "change", "sortOrder": "desc"},
            "range": [0, 30],
        }
        resp = requests.post(
            url, json=payload, timeout=10,
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])

        symbols = []
        for row in rows:
            symbol_full = row.get("s", "")  # ej. "NASDAQ:IMRN"
            symbol = symbol_full.split(":")[-1] if ":" in symbol_full else symbol_full
            if symbol:
                symbols.append(symbol)

        if symbols:
            logger.info(f"TradingView scanner devolvió {len(symbols)} candidatos.")
        return _to_universe_format(symbols)
    except Exception as e:
        logger.warning(f"TradingView scanner falló: {e}")
        return []


# ---------------------------------------------------------------------------
# RESPALDO: Finviz (vía finvizfinance)
# ---------------------------------------------------------------------------
def _get_top30_finviz():
    try:
        from finvizfinance.screener.overview import Overview

        overview = Overview()
        overview.set_filter(filters_dict={
            "Market Cap.": "Under $2Blrn",
            "Price": "Under $10",
            "Current Volume": "Over 500K",
            "Change": "Up",
        })
        # order='Change', ascend=False -> los que más subieron primero
        # verbose=0 -> evita que finvizfinance imprima progreso en los logs
        df = overview.screener_view(order="Change", ascend=False, limit=30, verbose=0)

        if df is None or df.empty or "Ticker" not in df.columns:
            return []

        symbols = df["Ticker"].tolist()[:30]
        logger.info(f"Finviz (respaldo) devolvió {len(symbols)} candidatos.")
        return _to_universe_format(symbols)
    except Exception as e:
        logger.warning(f"Finviz (respaldo) falló: {e}")
        return []
