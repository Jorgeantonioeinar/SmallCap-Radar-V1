"""
sec_shield.py
-------------
Anti-Offering Shield: consulta DIRECTAMENTE la API pública y gratuita de
SEC EDGAR (el regulador de EE.UU.) para detectar riesgo de dilución
inminente antes de aprobar una entrada en largo.

No requiere API key, pero la SEC SÍ exige un header User-Agent con nombre
y correo de contacto real en cada petición (configurable en
config.SEC_EDGAR_USER_AGENT) — si no lo mandas, la SEC devuelve 403.

Lógica:
  1. Mapea el ticker a su CIK (identificador único de la SEC) usando el
     catálogo oficial `company_tickers.json` (se cachea en memoria).
  2. Consulta los filings recientes de esa empresa
     (`data.sec.gov/submissions/CIK{cik}.json`).
  3. Si hay un 424B4/424B5 (oferta YA en curso) en los últimos
     SEC_DILUTION_LOOKBACK_DAYS días -> BLOQUEO TOTAL.
  4. Si hay un S-1/S-3 (shelf registration, dilución pendiente pero no
     confirmada) -> PENALIZACIÓN fuerte, no bloqueo.
  5. Si la consulta falla por cualquier motivo (red, ticker no encontrado,
     etc.) -> no bloquea ni penaliza, simplemente no hay información
     (nunca debe tumbar el bot).
"""

import logging
from datetime import datetime, timedelta

import requests

import config

logger = logging.getLogger("sec_shield")

_CIK_MAP_CACHE = None  # se descarga una sola vez por sesión


def _get_headers():
    return {"User-Agent": config.SEC_EDGAR_USER_AGENT}


def _load_cik_map():
    """Descarga (una sola vez) el catálogo oficial ticker -> CIK de la SEC."""
    global _CIK_MAP_CACHE
    if _CIK_MAP_CACHE is not None:
        return _CIK_MAP_CACHE

    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_get_headers(), timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # Formato: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        _CIK_MAP_CACHE = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}
        return _CIK_MAP_CACHE
    except Exception as e:
        logger.warning(f"No se pudo descargar el catálogo CIK de la SEC: {e}")
        _CIK_MAP_CACHE = {}
        return _CIK_MAP_CACHE


def get_cik_for_symbol(symbol: str):
    cik_map = _load_cik_map()
    return cik_map.get(symbol.upper())


def check_dilution_risk(symbol: str):
    """
    Devuelve un dict:
        {
          "blocked": bool,          # True = HARD BLOCK, no comprar
          "penalty_points": float,  # puntos a restar del score (0 si no aplica)
          "reason": str,            # explicación legible
          "recent_forms": [...],    # formularios encontrados en la ventana
        }
    Nunca lanza excepción; si algo falla, devuelve "sin riesgo detectado"
    (no penaliza por falta de información, solo por evidencia real).
    """
    result = {"blocked": False, "penalty_points": 0.0, "reason": "", "recent_forms": [], "risk_level": "BAJO"}

    cik = get_cik_for_symbol(symbol)
    if not cik:
        result["risk_level"] = "DESCONOCIDO"
        result["reason"] = "Ticker no encontrado en el catálogo de la SEC (sin penalización)"
        return result

    try:
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_get_headers(), timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])

        cutoff = datetime.utcnow() - timedelta(days=config.SEC_DILUTION_LOOKBACK_DAYS)

        found_hard_block = []
        found_penalty = []

        for form, date_str in zip(forms, dates):
            try:
                filing_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
            if filing_date < cutoff:
                continue

            if form in config.SEC_HARD_BLOCK_FORMS:
                found_hard_block.append(f"{form} ({date_str})")
            elif form in config.SEC_PENALTY_FORMS:
                found_penalty.append(f"{form} ({date_str})")

        result["recent_forms"] = found_hard_block + found_penalty

        if found_hard_block:
            result["blocked"] = True
            result["risk_level"] = "ALTO"
            result["reason"] = (
                f"🚫 Oferta de acciones ACTIVA detectada en SEC EDGAR: {', '.join(found_hard_block)}"
            )
        elif found_penalty:
            result["penalty_points"] = config.SEC_PENALTY_POINTS
            result["risk_level"] = "MEDIO"
            result["reason"] = (
                f"⚠️ Registro de dilución potencial (shelf) en SEC EDGAR: {', '.join(found_penalty)}"
            )
        else:
            result["risk_level"] = "BAJO"
            result["reason"] = "Sin filings de dilución recientes en SEC EDGAR"

        return result
    except Exception as e:
        logger.warning(f"[{symbol}] Error consultando SEC EDGAR: {e}")
        result["risk_level"] = "DESCONOCIDO"
        result["reason"] = "No se pudo consultar SEC EDGAR (sin penalización por falta de datos)"
        return result
