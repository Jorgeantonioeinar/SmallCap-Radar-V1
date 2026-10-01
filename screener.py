"""
screener.py
-----------
1. Combina tu watchlist manual (archivo de texto) con la lista base.
2. Para cada ticker calcula: gap %, RVOL, float, RSI, volumen.
3. Genera una calificación de 1 a 10 según las reglas de la estrategia.
4. Devuelve el top N candidatos ordenados, marcando >= SCORE_MIN_TO_BUY
   como señal de compra en largo.
"""

import logging
import os

import numpy as np
import pandas as pd
import ta

import config
import sec_shield
from data_fetcher import DataFetcher
from smart_engine import TitonSmartEngine

try:
    from halt_engine import get_halt_engine
except ImportError:
    get_halt_engine = None

try:
    from short_volume import get_short_volume, classify_short_pressure
except ImportError:
    get_short_volume = None
    classify_short_pressure = None

logger = logging.getLogger("screener")


def compute_data_confidence(result: dict, pmh=None, has_prev_close: bool = True) -> dict:
    """
    Calcula un Data Confidence Score (0-100) y un flag de calidad.
    Cada dato crítico que falte o sea de baja calidad resta puntos.
    """
    score = 100
    notes = []

    if result.get("price") is None:
        score -= 30
        notes.append("sin precio")
    if result.get("gap_pct") is None or not has_prev_close:
        score -= 20
        notes.append("prev_close débil")
    if result.get("float_shares") is None:
        score -= 15
        notes.append("sin float")
    if result.get("rvol") is None:
        score -= 10
        notes.append("sin rvol")
    if pmh is None:
        score -= 15
        notes.append("sin PMH")
        pmh_quality = "LOW"
    else:
        pmh_quality = "HIGH"

    halt = result.get("halt_status") or {}
    if halt.get("halted"):
        score -= 40
        notes.append(f"HALT ({halt.get('reason')})")
    if halt.get("quality") == "LOW":
        score -= 5
        notes.append("halt feed débil")

    score = max(0, min(100, score))
    if score >= 90:
        level = "HIGH"
    elif score >= 75:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "data_confidence": score,
        "data_quality": level,
        "pmh_quality": pmh_quality,
        "confidence_notes": notes,
    }




def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = (s or "00:00").strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def resolve_active_session(session_mode: str = "auto") -> str:
    """
    session_mode de la UI:
      auto | premarket | regular | afterhours | off
    Devuelve la sesión de scoring a usar: premarket | regular | afterhours
    (si off → regular para umbrales, pero sin bloquear por hora).
    """
    mode = (session_mode or "auto").lower()
    if mode in ("premarket", "regular", "afterhours"):
        return mode
    # auto u off: detectar reloj NY
    try:
        from strategy import get_current_session
        sess = get_current_session()
        if sess in ("premarket", "regular", "afterhours"):
            return sess
    except Exception:
        pass
    return "regular"


def is_clock_in_session(session: str) -> bool:
    """True si el reloj NY está físicamente en esa sesión de bolsa."""
    try:
        from strategy import get_current_session
        return get_current_session() == session
    except Exception:
        return True


def compute_scalp_ready(result: dict, session_mode: str = "off") -> dict:
    """
    Señal unificada LISTO / VIGILAR / NO según perfil de la sesión activa.

    session_mode: auto | premarket | regular | afterhours | off
    """
    reasons = []
    scoring_session = resolve_active_session(session_mode)
    profile = config.get_session_scoring_profile(scoring_session)

    halted = bool(result.get("halted"))
    conf = result.get("data_confidence")
    try:
        conf = float(conf) if conf is not None else None
    except Exception:
        conf = None
    quality = result.get("score")
    try:
        quality = float(quality) if quality is not None else 0.0
    except Exception:
        quality = 0.0
    entry = result.get("entry_score")
    try:
        entry = float(entry) if entry is not None else None
    except Exception:
        entry = None
    chase = (result.get("chase_status") or "").upper()
    dil = (result.get("dilution_risk") or "").upper()
    signal = (result.get("signal") or "").upper()
    gap = result.get("gap_pct")
    try:
        gap = float(gap) if gap is not None else None
    except Exception:
        gap = None

    q_min = float(profile.get("score_min_listo", getattr(config, "SCORE_MIN_TO_BUY", 9.0)))
    e_min = float(profile.get("entry_min_listo", getattr(config, "ENTRY_SCORE_MIN_FOR_READY", 7.0)))
    conf_min = float(profile.get("confidence_min", getattr(config, "DATA_CONFIDENCE_MIN_FOR_BUY", 70)))
    gap_min = float(profile.get("gap_min_pct", getattr(config, "GAP_MIN_PCT", 15.0)))

    # ¿El reloj coincide con la sesión de scoring?
    # - mode off: no bloquear por hora
    # - mode auto: sesión = reloj; siempre "en ventana" si no closed
    # - mode forzado (premarket/regular/afterhours): LISTO solo si el reloj está en esa sesión
    #   (permite estudiar premarket en fin de semana como VIGILAR, no LISTO falso)
    mode = (session_mode or "off").lower()
    if mode == "off":
        in_window = True
    elif mode == "auto":
        try:
            from strategy import get_current_session
            in_window = get_current_session() in ("premarket", "regular", "afterhours")
        except Exception:
            in_window = True
    else:
        in_window = is_clock_in_session(scoring_session)

    result_meta = {
        "scoring_session": scoring_session,
        "session_label": profile.get("label", scoring_session),
        "in_session_window": in_window,
    }

    if halted:
        return {"scalp_ready": "NO", "scalp_reasons": ["HALT"], **result_meta}
    if signal in ("DESCARTAR", "SIN COBERTURA"):
        return {"scalp_ready": "NO", "scalp_reasons": [signal], **result_meta}
    if dil in ("ALTO", "HIGH"):
        return {"scalp_ready": "NO", "scalp_reasons": ["Dilución ALTA"], **result_meta}
    if chase in ("NO_CHASE", "MUY_EXTENDIDO"):
        return {"scalp_ready": "NO", "scalp_reasons": [f"Estado {chase}"], **result_meta}
    if conf is not None and conf < conf_min:
        return {"scalp_ready": "NO", "scalp_reasons": [f"Confianza {conf:.0f}<{conf_min:.0f}"], **result_meta}

    # Fuera de la sesión forzada / mercado cerrado → no LISTO
    if mode != "off" and not in_window:
        reasons.append(f"Fuera de {profile.get('label', scoring_session)}")
        if quality >= q_min - 2:
            return {"scalp_ready": "VIGILAR", "scalp_reasons": reasons, **result_meta}
        return {"scalp_ready": "NO", "scalp_reasons": reasons, **result_meta}

    # Gap: en premarket/AH un gap decente ayuda a LISTO; no es veto absoluto si Quality alto
    gap_ok = gap is None or gap >= gap_min or gap >= (gap_min * 0.6)

    hard_ok = (
        quality >= q_min
        and entry is not None and entry >= e_min
        and chase in ("NORMAL", "", "SIN_DATOS")
        and (conf is None or conf >= conf_min)
        and gap_ok
    )
    if hard_ok:
        return {
            "scalp_ready": "LISTO",
            "scalp_reasons": [f"{scoring_session}: Q+Entry+NORMAL"],
            **result_meta,
        }

    soft_ok = quality >= (q_min - 2) or (entry is not None and entry >= e_min - 1)
    if soft_ok or chase == "EXTENDIDO" or signal == "VIGILAR":
        if chase == "EXTENDIDO":
            reasons.append("EXTENDIDO")
        if quality < q_min:
            reasons.append(f"Quality {quality:.1f}<{q_min}")
        if entry is None or entry < e_min:
            reasons.append("Entry bajo")
        if gap is not None and gap < gap_min:
            reasons.append(f"Gap {gap:.1f}%<{gap_min}")
        return {"scalp_ready": "VIGILAR", "scalp_reasons": reasons or ["Casi listo"], **result_meta}

    return {"scalp_ready": "NO", "scalp_reasons": reasons or ["Score insuficiente"], **result_meta}



def _attach_entry_metrics(result: dict, bars) -> dict:
    """RSI / Entry / Estado aunque el ticker se descarte (halt, float bajo, etc.)."""
    try:
        from strategy import (
            get_premarket_high, compute_vwap, compute_entry_score,
            classify_chase_risk, compute_extension_metrics,
        )
        if bars is None or getattr(bars, "empty", True):
            result.setdefault("rsi", result.get("rsi"))
            result.setdefault("entry_score", None)
            result.setdefault("chase_status", "SIN_DATOS")
            result.setdefault("atr", None)
            return result
        if result.get("rsi") is None:
            result["rsi"] = compute_rsi(bars)
        if result.get("atr") is None:
            result["atr"] = compute_atr(bars)
        pmh = get_premarket_high(bars)
        vwap = compute_vwap(bars)
        if result.get("entry_score") is None:
            result["entry_score"] = compute_entry_score(bars, pmh, vwap)
        if not result.get("chase_status"):
            result["chase_status"] = classify_chase_risk(
                compute_extension_metrics(bars, pmh, vwap)
            )
        result["_pmh"] = pmh
        return result
    except Exception as e:
        logger.debug(f"entry metrics: {e}")
        result.setdefault("entry_score", None)
        result.setdefault("chase_status", "SIN_DATOS")
        return result


def _finalize_candidate(result: dict, bars=None, pmh=None) -> dict:
    """Siempre: métricas de entrada + halt + short + confianza + scalp."""
    result = _attach_entry_metrics(result, bars)
    if pmh is None:
        pmh = result.pop("_pmh", None)
    else:
        result.pop("_pmh", None)
    return enrich_with_halt_and_confidence(result, bars=bars, pmh=pmh)


def enrich_with_halt_and_confidence(result: dict, bars=None, pmh=None) -> dict:
    """Añade halt_status + short volume + data_confidence a cualquier resultado."""
    if get_halt_engine is not None:
        try:
            he = get_halt_engine()
            halt = he.get_halt_status(result.get("symbol", ""))
        except Exception as e:
            logger.warning(f"HaltEngine falló: {e}")
            halt = {"halted": False, "reason": None, "quality": "LOW", "source": "error"}
    else:
        halt = {"halted": False, "reason": None, "quality": "LOW", "source": "unavailable"}

    result["halt_status"] = halt
    result["halted"] = bool(halt.get("halted"))

    # Si está en halt, forzar señal a DESCARTAR / no comprar
    if result["halted"]:
        result["signal"] = "DESCARTAR"
        result["notes"] = (result.get("notes") or []) + [f"HALT activo: {halt.get('reason')}"]

    # FINRA Short Volume (contexto, no bloquea por sí solo)
    if get_short_volume is not None and not getattr(config, "FAST_SCREENING", False):
        try:
            sv = get_short_volume(result.get("symbol", ""))
            result["short_pct"] = sv.get("short_pct")
            result["short_volume"] = sv.get("short_volume")
            result["short_date"] = sv.get("date")
            if classify_short_pressure is not None:
                result["short_pressure"] = classify_short_pressure(sv.get("short_pct"))
            else:
                result["short_pressure"] = "N/D"
        except Exception as e:
            logger.warning(f"ShortVolume falló para {result.get('symbol')}: {e}")
            result["short_pct"] = None
            result["short_pressure"] = "N/D"
    else:
        result["short_pct"] = None
        result["short_pressure"] = "N/D"

    conf = compute_data_confidence(result, pmh=pmh, has_prev_close=result.get("gap_pct") is not None)
    result.update(conf)

    # Regla de seguridad: confianza baja → no COMPRA_LARGO automática
    if result.get("data_quality") == "LOW" and result.get("signal") == "COMPRA_LARGO":
        result["signal"] = "VIGILAR"
        result["notes"] = (result.get("notes") or []) + ["Data confidence LOW → solo vigilancia"]

    # Señal unificada + perfil de sesión (premarket / regular / afterhours)
    session_mode = getattr(config, "_SESSION_MODE_RUNTIME", getattr(config, "_SESSION_FILTER_RUNTIME", "off"))
    # map legacy filter values
    legacy = {"strong": "regular", "premarket_strong": "premarket"}
    session_mode = legacy.get(session_mode, session_mode)
    scalp = compute_scalp_ready(result, session_mode=session_mode)
    result["scalp_ready"] = scalp["scalp_ready"]
    result["scalp_reasons"] = scalp.get("scalp_reasons") or []
    result["in_session_window"] = scalp.get("in_session_window", True)
    result["scoring_session"] = scalp.get("scoring_session", "regular")
    result["session_label"] = scalp.get("session_label", "")

    # Fuera de sesión activa (modo auto/forzado): no COMPRA_LARGO automática
    if session_mode not in ("off", None) and not result["in_session_window"] and result.get("signal") == "COMPRA_LARGO":
        result["signal"] = "VIGILAR"
        result["notes"] = (result.get("notes") or []) + [f"Fuera de sesión {result.get('session_label') or result.get('scoring_session')}"]

    return result


# ---------------------------------------------------------------------------
# ENTRADA MANUAL DE TICKERS
# ---------------------------------------------------------------------------
def load_manual_tickers():
    """
    Lee config.MANUAL_TICKERS_FILE. Formato por línea:
        TICKER
        TICKER,FLOAT
        TICKER,FLOAT,RVOL

    Ejemplos válidos:
        IMRN
        IMRN,2500000
        IMRN,2500000,5.2

    El float/RVOL puestos a mano tienen PRIORIDAD sobre lo que devuelva
    Yahoo Finance/Alpaca — útil cuando ya los viste en Momo Screener o
    Webull y no quieres depender de que Yahoo responda.

    Devuelve una lista de dicts: [{"symbol": "IMRN", "float_override": 2500000, "rvol_override": 5.2}, ...]
    """
    path = config.MANUAL_TICKERS_FILE
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(
                "# Un ticker por línea. Formato: TICKER  o  TICKER,FLOAT  o  TICKER,FLOAT,RVOL\n"
                "# Ejemplo: IMRN,2500000,5.2  (float y RVOL puestos a mano tienen prioridad)\n"
                "# Las líneas que empiecen con # se ignoran.\n"
            )
        return []

    entries = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            symbol = parts[0].upper()
            if not symbol:
                continue

            entry = {"symbol": symbol, "float_override": None, "rvol_override": None}
            try:
                if len(parts) >= 2 and parts[1]:
                    entry["float_override"] = float(parts[1])
                if len(parts) >= 3 and parts[2]:
                    entry["rvol_override"] = float(parts[2])
            except ValueError:
                logger.warning(f"Formato inválido en manual_tickers.txt: '{line}' (se ignoran los overrides)")

            entries.append(entry)

    # --- Deduplicar por símbolo: si hay varias líneas del mismo ticker, ---
    # --- nos quedamos con la que tenga MÁS información (float/RVOL). ---
    deduped = {}
    for e in entries:
        existing = deduped.get(e["symbol"])
        if existing is None:
            deduped[e["symbol"]] = e
        else:
            # Si la nueva entrada aporta un dato que la existente no tenía, se completa
            if existing["float_override"] is None and e["float_override"] is not None:
                existing["float_override"] = e["float_override"]
            if existing["rvol_override"] is None and e["rvol_override"] is not None:
                existing["rvol_override"] = e["rvol_override"]

    return list(deduped.values())


def replace_manual_watchlist(symbols: list) -> list:
    """Reemplaza la watchlist manual completa (import Webull/Moomoo)."""
    path = config.MANUAL_TICKERS_FILE
    cleaned = []
    seen = set()
    for s in symbols or []:
        t = (s or "").strip().upper()
        t = t.split(".")[-1]
        t = "".join(c for c in t if c.isalpha())
        if len(t) < 2 or len(t) > 5 or t in seen:
            continue
        seen.add(t)
        cleaned.append(t)
    with open(path, "w") as f:
        f.write("# Watchlist importada (Webull/Moomoo/texto)\n")
        for t in cleaned:
            f.write(t + "\n")
    logger.info(f"Watchlist manual reemplazada: {len(cleaned)} símbolos")
    return cleaned


def add_manual_ticker(symbol: str, float_override=None, rvol_override=None):

    """Agrega un ticker (con overrides opcionales de float/RVOL) al archivo manual."""
    symbol = symbol.strip().upper()
    parts = [symbol]
    if float_override is not None:
        parts.append(str(float_override))
    if rvol_override is not None:
        parts.append(str(rvol_override))
    line = ",".join(parts)
    with open(config.MANUAL_TICKERS_FILE, "a") as f:
        f.write(f"{line}\n")
    logger.info(f"Ticker manual agregado: {line}")


def prompt_manual_tickers_cli():
    """
    Entrada manual interactiva por consola. Formato: TICKER o TICKER,FLOAT
    separados por coma dentro de la misma línea, y varias líneas separadas
    por punto y coma (;) si se pegan varias de una vez.
    """
    raw = input(
        "\n➡️  Ingresa tickers manuales (formato TICKER o TICKER,FLOAT), "
        "separa varios con ';' o Enter para omitir: "
    ).strip()
    if not raw:
        return []
    added = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        symbol = parts[0].upper()
        float_override = float(parts[1]) if len(parts) >= 2 and parts[1] else None
        rvol_override = float(parts[2]) if len(parts) >= 3 and parts[2] else None
        add_manual_ticker(symbol, float_override, rvol_override)
        added.append(symbol)
    return added


def get_universe():
    """
    Combina: watchlist manual (archivo, con posibles overrides de
    float/RVOL) + lista base de config. Devuelve una lista de dicts:
    [{"symbol": ..., "float_override": ..., "rvol_override": ...}, ...]
    """
    manual_entries = load_manual_tickers()
    manual_symbols = {e["symbol"] for e in manual_entries}

    combined = list(manual_entries)
    for symbol in (config.DEFAULT_WATCHLIST or []):
        if symbol not in manual_symbols:
            combined.append({"symbol": symbol, "float_override": None, "rvol_override": None})
    return combined


# ---------------------------------------------------------------------------
# INDICADORES TÉCNICOS
# ---------------------------------------------------------------------------
def compute_rsi(bars: pd.DataFrame, period=config.RSI_PERIOD):
    if bars.empty or len(bars) < period + 1:
        return None
    rsi_series = ta.momentum.RSIIndicator(close=bars["close"], window=period).rsi()
    return round(rsi_series.iloc[-1], 2)


def compute_atr(bars: pd.DataFrame, period=config.ATR_PERIOD):
    if bars.empty or len(bars) < period + 1:
        return None
    atr_series = ta.volatility.AverageTrueRange(
        high=bars["high"], low=bars["low"], close=bars["close"], window=period
    ).average_true_range()
    return round(atr_series.iloc[-1], 4)


# ---------------------------------------------------------------------------
# MOTOR DE SCORING (1-10)
# ---------------------------------------------------------------------------
_smart_engine = TitonSmartEngine()


def score_candidate(
    symbol: str, fetcher: DataFetcher, float_override=None, rvol_override=None,
    gap_override=None, price_override=None, market_cap_override=None,
    volume_override=None, premarket_volume_override=None, afterhours_volume_override=None,
):
    """Despachador: usa el motor clásico o el TitonSmartEngine según config.SCORING_ENGINE."""
    if config.SCORING_ENGINE == "smart":
        return score_candidate_smart(
            symbol, fetcher, float_override, rvol_override, gap_override, price_override,
            market_cap_override, volume_override, premarket_volume_override, afterhours_volume_override,
        )
    return _score_candidate_classic(
        symbol, fetcher, float_override, rvol_override, gap_override, price_override
    )


def score_candidate_smart(
    symbol: str, fetcher: DataFetcher, float_override=None, rvol_override=None,
    gap_override=None, price_override=None, market_cap_override=None,
    volume_override=None, premarket_volume_override=None, afterhours_volume_override=None,
):
    """
    Arma el diccionario de datos que espera TitonSmartEngine.evaluate()
    y traduce su veredicto de vuelta al mismo formato que usa el resto
    del dashboard (mismas claves que la función clásica), para que la
    tabla/gráficos/compra funcionen sin cambios.
    """
    price = fetcher.get_latest_price(symbol)
    used_price_override = (price is None or price <= 0) and price_override is not None and price_override > 0
    if used_price_override:
        price = price_override
    fundamentals = fetcher.get_fundamentals(symbol)
    bars = fetcher.get_bars(symbol, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)

    float_shares = float_override if float_override is not None else fundamentals.get("float_shares")
    shares_outstanding = fundamentals.get("shares_outstanding")

    # Fallback matemático: si ninguna fuente dio market_cap directamente pero
    # sí tenemos precio (Alpaca) y shares_outstanding (FMP/AlphaVantage/Yahoo),
    # lo calculamos nosotros mismos en vez de dejarlo en None.
    market_cap = market_cap_override if market_cap_override is not None else fundamentals.get("market_cap")
    if market_cap is None and price and shares_outstanding:
        market_cap = price * shares_outstanding

    # Cierre OFICIAL de la última sesión regular (bug corregido: antes se
    # usaba "la primera barra disponible de la ventana", que con tickers de
    # datos escasos —halts, baja liquidez— podía ser de días atrás a un
    # precio distinto. Confirmado con WHLR: causaba un gap de 1978%.)
    prev_close = fetcher.get_previous_regular_close(symbol)

    # Volumen premarket aproximado: suma de barras entre 4:00-9:30am hora NY
    premarket_volume = None
    try:
        from strategy import NY_TZ
        from datetime import time as dtime
        if not bars.empty:
            idx_ny = bars.index.tz_convert(NY_TZ) if bars.index.tz is not None else bars.index
            most_recent_date = idx_ny[-1].date()
            mask = (idx_ny.date == most_recent_date) & (idx_ny.time >= dtime(4, 0)) & (idx_ny.time < dtime(9, 30))
            premarket_bars = bars[mask]
            if not premarket_bars.empty:
                premarket_volume = float(premarket_bars["volume"].sum())
    except Exception:
        pass

    # Respaldo: Alpaca/IEX no opera en premarket, así que lo anterior da
    # None casi siempre con el feed gratuito. Si es el caso, se intenta
    # Twelve Data como fuente alterna de volumen premarket.
    if premarket_volume_override is not None:
        premarket_volume = premarket_volume_override
    elif not premarket_volume and not getattr(config, "FAST_SCREENING", False):
        premarket_volume = fetcher.get_premarket_volume_twelvedata(symbol)

    # Volumen after-hours (4:00-8:00pm hora NY): mismo hueco estructural
    # que premarket (IEX tampoco opera en after-hours), mismo respaldo.
    # Por ahora solo se EXPONE el dato (no se usa todavía en el scoring
    # del Motor Smart) — la Pieza 3 lo incorporará al perfil de "Sesión".
    afterhours_volume = None
    try:
        from strategy import NY_TZ
        from datetime import time as dtime
        if not bars.empty:
            idx_ny = bars.index.tz_convert(NY_TZ) if bars.index.tz is not None else bars.index
            most_recent_date = idx_ny[-1].date()
            mask_ah = (idx_ny.date == most_recent_date) & (idx_ny.time >= dtime(16, 0)) & (idx_ny.time < dtime(20, 0))
            afterhours_bars = bars[mask_ah]
            if not afterhours_bars.empty:
                afterhours_volume = float(afterhours_bars["volume"].sum())
    except Exception:
        pass
    if not afterhours_volume and not getattr(config, "FAST_SCREENING", False):
        afterhours_volume = fetcher.get_afterhours_volume_twelvedata(symbol)

    if getattr(config, "FAST_SCREENING", False):
        sentiment = None
        catalyst_verified = None
        dilution = {"reason": "Fast mode", "risk_level": "DESCONOCIDO", "blocked": False, "penalty_points": 0}
    else:
        sentiment = fetcher.get_news_sentiment(symbol)
        catalyst_verified = sentiment["score"] > 0.15 if sentiment else None
        dilution = sec_shield.check_dilution_risk(symbol)

    # RVOL Estructural session-aware: en regular usamos el volumen acumulado
    # normal (bars["volume"].sum()); en premarket/after-hours, el volumen de
    # esa ventana específica. El float no cambia según la hora, así que la
    # misma fórmula (session_volume / float) es válida en las 3 sesiones.
    from strategy import get_current_session
    current_session = get_current_session()
    if current_session == "premarket":
        session_volume = (
            premarket_volume_override
            if premarket_volume_override is not None
            else volume_override if volume_override is not None else premarket_volume
        )
    elif current_session == "afterhours":
        session_volume = (
            afterhours_volume_override
            if afterhours_volume_override is not None
            else volume_override if volume_override is not None else afterhours_volume
        )
    else:
        session_volume = volume_override if volume_override is not None else (float(bars["volume"].sum()) if not bars.empty else None)

    data = {
        "ticker": symbol,
        "current_price": price,
        "prev_close": prev_close,
        "market_cap": market_cap,
        "float_shares": float_shares,
        "shares_outstanding": shares_outstanding,
        "session_volume": session_volume,
        "premarket_volume": premarket_volume,   # se conserva para compatibilidad/inspección
        "afterhours_volume": afterhours_volume,
        "catalyst_verified": catalyst_verified,
        "sec_dilution_blocked": dilution["blocked"],
        "gap_pct": gap_override,
    }
    verdict = _smart_engine.evaluate(data)

    # RVOL Estructural (premarket_volume / float) — antes esta columna mostraba
    # siempre "rvol_override" (None salvo entrada manual), aunque el motor Smart
    # SÍ calcula esta relación internamente para el score. Ahora se expone el
    # mismo número que ya usa el motor, como un múltiplo "x" comparable al RVOL
    # del Motor Clásico (que es volumen/promedio en vez de volumen/float, así
    # que los dos "RVOL" miden cosas relacionadas pero no idénticas — ver nota
    # en el dashboard/README).
    structural_rvol = None
    if rvol_override is not None:
        structural_rvol = rvol_override
    elif premarket_volume and float_shares:
        structural_rvol = round(premarket_volume / float_shares, 2)

    # Métricas de volumen separadas (V7.2) — con fallback si el método no existe
    vol_metrics = {}
    try:
        if hasattr(fetcher, "get_volume_metrics"):
            vol_metrics = fetcher.get_volume_metrics(symbol) or {}
        else:
            # Compatibilidad: data_fetcher antiguo sin V7.2
            r = fetcher.get_relative_volume(symbol) if hasattr(fetcher, "get_relative_volume") else None
            vol_metrics = {"rvol_session": r, "rvol_daily": r}
    except Exception as e:
        logger.warning(f"[{symbol}] volume metrics: {e}")
        vol_metrics = {}
    rvol_daily = vol_metrics.get("rvol_daily")
    rvol_session = vol_metrics.get("rvol_session")
    float_turnover = vol_metrics.get("float_turnover")
    if float_turnover is None and premarket_volume and float_shares:
        float_turnover = round(premarket_volume / float_shares, 4)

    # --- Entry Score / Chase Status (separado del Quality Score de arriba) ---
    # "score" (verdict["score"]) responde "¿qué tan bueno es este candidato?"
    # Esto responde "¿es buen MOMENTO para entrar ahora mismo?" — un ticker
    # puede tener Quality 10 y estar demasiado extendido para perseguirlo.
    from strategy import get_premarket_high, compute_vwap, compute_entry_score, classify_chase_risk, compute_extension_metrics
    pmh = get_premarket_high(bars)
    vwap = compute_vwap(bars)
    entry_score = compute_entry_score(bars, pmh, vwap)
    chase_status = classify_chase_risk(compute_extension_metrics(bars, pmh, vwap))

    # Traducir al formato común del resto del dashboard
    signal_map = {"Single-Bullet Aprobado": "COMPRA_LARGO", "EN OBSERVACIÓN": "VIGILAR", "RECHAZADO": "DESCARTAR"}
    result = {
        "symbol": symbol,
        "price": price,
        "gap_pct": verdict["gap_pct"],
        "rvol": structural_rvol,
        "rvol_daily": rvol_daily,
        "rvol_session": rvol_session,
        "float_turnover": float_turnover,
        "float_shares": float_shares,
        "rsi": compute_rsi(bars),
        "atr": compute_atr(bars),
        "country": None,
        "dilution_reason": dilution["reason"],
        "dilution_risk": dilution["risk_level"],
        "score": verdict["score"],
        "entry_score": entry_score,
        "chase_status": chase_status,
        "signal": signal_map.get(verdict["signal"], "DESCARTAR"),
        "notes": verdict["reasons"]
        + ([f"Gap desde Moomoo/import ({gap_override}%)"] if gap_override is not None else [])
        + (["Precio de respaldo desde Moomoo/import"] if used_price_override else [])
        + ([f"Float Market Cap: ${verdict['float_market_cap']:,.0f}"] if verdict.get("float_market_cap") else []),
    }
    return _finalize_candidate(result, bars=bars, pmh=pmh)


def _score_candidate_classic(
    symbol: str, fetcher: DataFetcher, float_override=None, rvol_override=None,
    gap_override=None, price_override=None,
):
    """
    Calcula todas las métricas de un ticker y devuelve un diccionario con
    el detalle + la calificación final de 1 a 10.

    Si se pasan float_override / rvol_override (desde la entrada manual),
    tienen PRIORIDAD sobre lo que devuelvan Yahoo Finance/Alpaca — así el
    bot sigue funcionando aunque Yahoo esté bloqueando peticiones.

    Ponderación (10 puntos en total):
      - Gap / Momentum del día ............ hasta 3.0 pts
      - RVOL (volumen relativo) ............ hasta 3.0 pts
      - Float bajo .......................... hasta 2.5 pts
      - RSI en zona saludable (no sobrecomprado extremo) .. hasta 1.5 pts
      Penalizaciones: precio fuera de rango, RSI > 90 (riesgo de "backside"),
      falta de datos críticos.
    """
    result = {
        "symbol": symbol,
        "price": None,
        "gap_pct": None,
        "rvol": None,
        "float_shares": None,
        "rsi": None,
        "atr": None,
        "country": None,
        "dilution_reason": None,
        "score": 0.0,
        "signal": "DESCARTAR",
        "notes": [],
    }

    price = fetcher.get_latest_price(symbol)
    if (price is None or price <= 0) and price_override is not None and price_override > 0:
        price = price_override
        result["notes"].append("Precio de respaldo desde Moomoo/import")
    fundamentals = fetcher.get_fundamentals(symbol)
    bars = fetcher.get_bars(symbol, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)

    result["price"] = price
    result["float_shares"] = float_override if float_override is not None else fundamentals.get("float_shares")
    if float_override is not None:
        result["notes"].append("Float puesto a mano (override manual)")

    # --- Validaciones básicas de rango de precio ---
    if price is None:
        result["notes"].append("Sin precio disponible (ticker sin cobertura o OTC)")
        result["signal"] = "SIN_COBERTURA"
        result["chase_status"] = "Sin datos"
        result["score"] = 0.0
        return _finalize_candidate(result, bars=bars)
    if not (config.PRICE_MIN <= price <= config.PRICE_MAX):
        result["notes"].append(f"Precio fuera de rango (${price})")
        result["signal"] = "DESCARTAR"
        return _finalize_candidate(result, bars=bars)

    score = 0.0

    # --- 1) Gap / momentum del día (umbral según sesión: PM / regular / AH) ---
    # Prioridad: dato del CSV Moomoo/Webull (gap_override) > API Alpaca
    if gap_override is not None:
        try:
            gap_pct = float(gap_override)
            result["notes"].append(f"Gap desde Moomoo/import ({gap_pct}%)")
        except Exception:
            gap_pct = fetcher.get_premarket_change_pct(symbol)
    else:
        gap_pct = fetcher.get_premarket_change_pct(symbol)
    result["gap_pct"] = gap_pct
    _sess_mode = getattr(config, "_SESSION_MODE_RUNTIME", "auto")
    if _sess_mode in ("strong", "premarket_strong"):
        _sess_mode = {"strong": "regular", "premarket_strong": "premarket"}.get(_sess_mode, _sess_mode)
    try:
        _scoring_sess = resolve_active_session(_sess_mode)
        _gap_min = float(config.get_session_scoring_profile(_scoring_sess).get("gap_min_pct", config.GAP_MIN_PCT))
    except Exception:
        _gap_min = config.GAP_MIN_PCT
        _scoring_sess = "regular"
    if gap_pct is not None:
        if gap_pct >= _gap_min:
            # Escala relativa al umbral de sesión
            gap_score = min(3.0, 1.0 + (gap_pct - _gap_min) / 20.0)
            score += gap_score
        else:
            result["notes"].append(f"Gap insuficiente ({gap_pct}% < {_gap_min}% [{_scoring_sess}])")

    # --- 2) RVOL separado (V7.2): session / daily / float_turnover ---
    vol_metrics = {}
    try:
        if hasattr(fetcher, "get_volume_metrics"):
            vol_metrics = fetcher.get_volume_metrics(symbol) or {}
        else:
            r = fetcher.get_relative_volume(symbol) if hasattr(fetcher, "get_relative_volume") else None
            vol_metrics = {"rvol_session": r, "rvol_daily": r}
    except Exception as e:
        logger.warning(f"[{symbol}] volume metrics: {e}")
        vol_metrics = {}
    result["rvol_daily"] = vol_metrics.get("rvol_daily")
    result["rvol_session"] = vol_metrics.get("rvol_session")
    result["float_turnover"] = vol_metrics.get("float_turnover")

    if rvol_override is not None:
        rvol = rvol_override
        result["notes"].append("RVOL puesto a mano (override manual)")
    else:
        # Preferimos session-aware para scalping; si no hay, daily
        rvol = vol_metrics.get("rvol_session") or vol_metrics.get("rvol_daily")
        if rvol is None and hasattr(fetcher, "get_relative_volume"):
            try:
                rvol = fetcher.get_relative_volume(symbol)
            except Exception:
                rvol = None
    result["rvol"] = rvol
    if rvol is not None:
        if rvol >= config.RVOL_MIN:
            rvol_score = min(3.0, (rvol / config.RVOL_MIN) * 1.5)
            score += rvol_score
        else:
            result["notes"].append(f"RVOL bajo ({rvol}x < {config.RVOL_MIN}x)")

    # --- 3) Float bajo (override manual tiene prioridad) ---
    float_shares = result["float_shares"]
    if float_shares:
        if float_shares < config.FLOAT_MIN_SHARES:
            result["notes"].append(
                f"Float DEMASIADO bajo ({float_shares:,.0f} < {config.FLOAT_MIN_SHARES:,.0f}) "
                f"- riesgo de manipulación extrema, se descarta"
            )
            result["score"] = 0.0
            result["signal"] = "DESCARTAR"
            # Igual calculamos RSI/Entry/Halt para no dejar la fila en None
            return _finalize_candidate(result, bars=bars)
        elif float_shares > config.FLOAT_MAX_SHARES:
            result["notes"].append(f"Float alto ({float_shares:,.0f} > {config.FLOAT_MAX_SHARES:,.0f})")
        elif float_shares <= config.FLOAT_LOW_BONUS_SHARES:
            score += 2.5
        else:
            score += 1.3
    else:
        result["notes"].append("Float no disponible (agrégalo a mano si Yahoo Finance falla)")

    # --- 4) RSI ---
    rsi = compute_rsi(bars)
    result["rsi"] = rsi
    if rsi is not None:
        if config.RSI_SWEET_SPOT_LOW <= rsi <= config.RSI_SWEET_SPOT_HIGH:
            score += 1.5
        elif rsi > config.RSI_OVERBOUGHT:
            score -= 1.0  # penalización: riesgo de comprar en el pico ("backside")
            result["notes"].append(f"RSI muy sobrecomprado ({rsi}) - riesgo de backside")
        elif rsi > config.RSI_SWEET_SPOT_HIGH:
            score += 0.7

    # --- ATR (para uso posterior en el stop dinámico, no puntúa) ---
    result["atr"] = compute_atr(bars)

    # --- 5) País de la sede: informativo únicamente, ya NO penaliza puntos ---
    # (ver nota en config.GEOGRAPHIC_PENALTY_ENABLED — el país es un proxy
    # indirecto; el riesgo real ya se mide directo vía liquidez/dilución/float)
    country = fetcher.get_company_country(symbol)
    result["country"] = country
    if config.GEOGRAPHIC_PENALTY_ENABLED and country and any(
        c.lower() in country.lower() for c in config.GEOGRAPHIC_PENALTY_COUNTRIES
    ):
        score -= config.GEOGRAPHIC_PENALTY_POINTS
        result["notes"].append(f"Penalización geográfica: sede en {country} (-{config.GEOGRAPHIC_PENALTY_POINTS} pts)")

    # --- 6) SEC EDGAR Anti-Offering Shield (dilución) ---
    if getattr(config, "FAST_SCREENING", False):
        dilution = {"reason": "Fast mode (SEC omitido)", "risk_level": "DESCONOCIDO", "blocked": False, "penalty_points": 0}
    else:
        dilution = sec_shield.check_dilution_risk(symbol)
    result["dilution_reason"] = dilution["reason"]
    result["dilution_risk"] = dilution["risk_level"]
    if dilution.get("blocked"):  # FAST_SCREENING dilution skip
        result["notes"].append(dilution["reason"])
        result["score"] = 0.0
        result["signal"] = "DESCARTAR"
        return _finalize_candidate(result, bars=bars)
    if dilution["penalty_points"] > 0:
        score -= dilution["penalty_points"]
        result["notes"].append(dilution["reason"])

    score = max(0.0, min(10.0, round(score, 2)))
    result["score"] = score
    result["signal"] = "COMPRA_LARGO" if score >= config.SCORE_MIN_TO_BUY else (
        "VIGILAR" if score >= config.SCORE_MIN_TO_BUY - 2 else "DESCARTAR"
    )

    # Entry / Halt / Scalp (siempre, también si el score es bajo)
    return _finalize_candidate(result, bars=bars)



def _rank_key(r: dict):
    """Prioridad para el lunes: LISTO primero, luego VIGILAR, luego score."""
    scalp_order = {"LISTO": 0, "VIGILAR": 1, "NO": 2}.get(
        (r.get("scalp_ready") or "NO"), 3
    )
    signal_order = {"COMPRA_LARGO": 0, "VIGILAR": 1, "DESCARTAR": 2, "SIN_COBERTURA": 3}.get(
        (r.get("signal") or "DESCARTAR"), 4
    )
    try:
        score = float(r.get("score") or 0)
    except Exception:
        score = 0.0
    try:
        entry = float(r.get("entry_score") or 0)
    except Exception:
        entry = 0.0
    return (scalp_order, signal_order, -score, -entry)


def rank_candidates(fetcher: DataFetcher, tickers=None):
    """
    Puntúa todos los tickers del universo y devuelve el top N ordenado.
    `tickers`, si se pasa, debe ser una lista de dicts como las que
    devuelve get_universe(): {"symbol":..., "float_override":..., "rvol_override":...}
    También acepta una lista simple de strings por compatibilidad.
    """
    if tickers is None:
        tickers = get_universe()

    mode = "RÁPIDO" if getattr(config, "FAST_SCREENING", False) else "COMPLETO"
    logger.info(f"rank_candidates: modo {mode}, {len(tickers)} símbolos")

    def _one(entry):
        if isinstance(entry, str):
            symbol, float_override, rvol_override, gap_override = entry, None, None, None
            price_override = market_cap_override = volume_override = premarket_volume_override = afterhours_volume_override = None
        else:
            symbol = entry["symbol"]
            float_override = entry.get("float_override")
            rvol_override = entry.get("rvol_override")
            gap_override = entry.get("gap_override")
            price_override = entry.get("price_hint")
            market_cap_override = entry.get("market_cap_override")
            volume_override = entry.get("volume_override")
            premarket_volume_override = entry.get("premarket_volume_override")
            afterhours_volume_override = entry.get("afterhours_volume_override")
        try:
            scored = score_candidate(
                symbol, fetcher, float_override, rvol_override, gap_override,
                price_override, market_cap_override, volume_override, premarket_volume_override,
                afterhours_volume_override,
            )
            if not isinstance(entry, str) and entry.get("momo_data"):
                scored["momo_data"] = entry["momo_data"]
            return scored
        except Exception as e:
            logger.warning(f"[{symbol}] Error calculando score: {e}")
            return {
                "symbol": symbol,
                "price": None,
                "gap_pct": None,
                "rvol": None,
                "float_shares": None,
                "rsi": None,
                "score": 0.0,
                "entry_score": 0.0,
                "signal": "ERROR",
                "notes": [str(e)[:120]],
                "chase_status": "Error",
            }

    results = []
    # Precalentar HaltEngine 1 vez (evita 15 descargas RSS en paralelo)
    if get_halt_engine is not None:
        try:
            get_halt_engine().get_halt_status("__warmup__")
        except Exception:
            pass

    use_parallel = getattr(config, "FAST_SCREENING", False) and len(tickers) >= 2
    if use_parallel:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        workers = min(
            int(getattr(config, "FAST_SCREENING_WORKERS", 4) or 4),
            max(2, len(tickers)),
        )
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_one, e) for e in tickers]
            for f in as_completed(futs):
                r = f.result()
                if r is not None:
                    results.append(r)
    else:
        for entry in tickers:
            r = _one(entry)
            if r is not None:
                results.append(r)

    results.sort(key=_rank_key)
    # limit_n=None -> TOP_N; limit_n=0 o "all" -> devolver todos (watchlist manual)
    limit_n = getattr(config, "_RANK_LIMIT_OVERRIDE", None)
    if limit_n == 0 or limit_n == "all":
        return results
    if isinstance(limit_n, int) and limit_n > 0:
        return results[:limit_n]
    return results[: config.TOP_N_CANDIDATOS]


def print_ranking_table(ranked):
    """Imprime el ranking en formato de tabla legible en consola."""
    header = f"{'#':<3}{'TICKER':<8}{'PRECIO':<9}{'GAP%':<8}{'RVOL':<7}{'FLOAT':<12}{'RSI':<7}{'SCORE':<7}{'SEÑAL'}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(ranked, 1):
        float_str = f"{r['float_shares']:,.0f}" if r["float_shares"] else "N/D"
        price_str = f"${r['price']:.2f}" if r["price"] else "N/D"
        gap_str = f"{r['gap_pct']}%" if r["gap_pct"] is not None else "N/D"
        rvol_str = f"{r['rvol']}x" if r["rvol"] is not None else "N/D"
        rsi_str = str(r["rsi"]) if r["rsi"] is not None else "N/D"
        print(
            f"{i:<3}{r['symbol']:<8}{price_str:<9}{gap_str:<8}{rvol_str:<7}"
            f"{float_str:<12}{rsi_str:<7}{r['score']:<7}{r['signal']}"
        )
