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

logger = logging.getLogger("screener")


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
    for symbol in config.DEFAULT_WATCHLIST:
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


def score_candidate(symbol: str, fetcher: DataFetcher, float_override=None, rvol_override=None):
    """Despachador: usa el motor clásico o el TitonSmartEngine según config.SCORING_ENGINE."""
    if config.SCORING_ENGINE == "smart":
        return score_candidate_smart(symbol, fetcher, float_override, rvol_override)
    return _score_candidate_classic(symbol, fetcher, float_override, rvol_override)


def score_candidate_smart(symbol: str, fetcher: DataFetcher, float_override=None, rvol_override=None):
    """
    Arma el diccionario de datos que espera TitonSmartEngine.evaluate()
    y traduce su veredicto de vuelta al mismo formato que usa el resto
    del dashboard (mismas claves que la función clásica), para que la
    tabla/gráficos/compra funcionen sin cambios.
    """
    price = fetcher.get_latest_price(symbol)
    fundamentals = fetcher.get_fundamentals(symbol)
    bars = fetcher.get_bars(symbol, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)

    float_shares = float_override if float_override is not None else fundamentals.get("float_shares")
    shares_outstanding = fundamentals.get("shares_outstanding")

    # Fallback matemático: si ninguna fuente dio market_cap directamente pero
    # sí tenemos precio (Alpaca) y shares_outstanding (FMP/AlphaVantage/Yahoo),
    # lo calculamos nosotros mismos en vez de dejarlo en None.
    market_cap = fundamentals.get("market_cap")
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
    if not premarket_volume:
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
    if not afterhours_volume:
        afterhours_volume = fetcher.get_afterhours_volume_twelvedata(symbol)

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
        session_volume = premarket_volume
    elif current_session == "afterhours":
        session_volume = afterhours_volume
    else:
        session_volume = float(bars["volume"].sum()) if not bars.empty else None

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
    return {
        "symbol": symbol,
        "price": price,
        "gap_pct": verdict["gap_pct"],
        "rvol": structural_rvol,
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
        "notes": verdict["reasons"] + [f"Float Market Cap: ${verdict['float_market_cap']:,.0f}"] if verdict.get("float_market_cap") else verdict["reasons"],
    }


def _score_candidate_classic(symbol: str, fetcher: DataFetcher, float_override=None, rvol_override=None):
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
    fundamentals = fetcher.get_fundamentals(symbol)
    bars = fetcher.get_bars(symbol, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)

    result["price"] = price
    result["float_shares"] = float_override if float_override is not None else fundamentals.get("float_shares")
    if float_override is not None:
        result["notes"].append("Float puesto a mano (override manual)")

    # --- Validaciones básicas de rango de precio ---
    if price is None:
        result["notes"].append("Sin precio disponible")
        return result
    if not (config.PRICE_MIN <= price <= config.PRICE_MAX):
        result["notes"].append(f"Precio fuera de rango (${price})")
        return result

    score = 0.0

    # --- 1) Gap / momentum del día ---
    gap_pct = fetcher.get_premarket_change_pct(symbol)
    result["gap_pct"] = gap_pct
    if gap_pct is not None:
        if gap_pct >= config.GAP_MIN_PCT:
            # Escala: +15% -> 1.5pt, +30% -> 2.5pt, +50%+ -> 3.0pt (con techo)
            gap_score = min(3.0, 1.0 + (gap_pct - config.GAP_MIN_PCT) / 20.0)
            score += gap_score
        else:
            result["notes"].append(f"Gap insuficiente ({gap_pct}% < {config.GAP_MIN_PCT}%)")

    # --- 2) RVOL (override manual tiene prioridad) ---
    if rvol_override is not None:
        rvol = rvol_override
        result["notes"].append("RVOL puesto a mano (override manual)")
    else:
        rvol = fetcher.get_relative_volume(symbol)
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
            return result
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
    dilution = sec_shield.check_dilution_risk(symbol)
    result["dilution_reason"] = dilution["reason"]
    result["dilution_risk"] = dilution["risk_level"]
    if dilution["blocked"]:
        result["notes"].append(dilution["reason"])
        result["score"] = 0.0
        result["signal"] = "DESCARTAR"
        return result
    if dilution["penalty_points"] > 0:
        score -= dilution["penalty_points"]
        result["notes"].append(dilution["reason"])

    score = max(0.0, min(10.0, round(score, 2)))
    result["score"] = score
    result["signal"] = "COMPRA_LARGO" if score >= config.SCORE_MIN_TO_BUY else (
        "VIGILAR" if score >= config.SCORE_MIN_TO_BUY - 2 else "DESCARTAR"
    )

    # --- Entry Score / Chase Status (separado del Quality Score de arriba) ---
    from strategy import get_premarket_high, compute_vwap, compute_entry_score, classify_chase_risk, compute_extension_metrics
    pmh = get_premarket_high(bars)
    vwap = compute_vwap(bars)
    result["entry_score"] = compute_entry_score(bars, pmh, vwap)
    result["chase_status"] = classify_chase_risk(compute_extension_metrics(bars, pmh, vwap))

    return result


def rank_candidates(fetcher: DataFetcher, tickers=None):
    """
    Puntúa todos los tickers del universo y devuelve el top N ordenado.
    `tickers`, si se pasa, debe ser una lista de dicts como las que
    devuelve get_universe(): {"symbol":..., "float_override":..., "rvol_override":...}
    También acepta una lista simple de strings por compatibilidad.
    """
    if tickers is None:
        tickers = get_universe()

    results = []
    for entry in tickers:
        if isinstance(entry, str):
            symbol, float_override, rvol_override = entry, None, None
        else:
            symbol = entry["symbol"]
            float_override = entry.get("float_override")
            rvol_override = entry.get("rvol_override")

        try:
            results.append(score_candidate(symbol, fetcher, float_override, rvol_override))
        except Exception as e:
            logger.warning(f"[{symbol}] Error calculando score: {e}")

    results.sort(key=lambda r: r["score"], reverse=True)
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
