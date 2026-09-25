"""
strategy.py
-----------
Lógica de decisión de ENTRADA, separada de la ejecución de órdenes.

Implementa el Opening Range Breakout (ORB) sobre los candidatos que ya
pasaron el filtro de scoring (score >= config.SCORE_MIN_TO_BUY).
"""

import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import ta

import config

logger = logging.getLogger("strategy")

NY_TZ = ZoneInfo("America/New_York")


def is_within_trading_window():
    """
    Las 4 estrategias analizadas coinciden en que la mejor volatilidad para
    Gap & Go ocurre entre la apertura y la 1:00pm ET — después de eso el
    volumen suele apagarse. Devuelve True solo dentro de esa ventana.

    OJO: esto gobierna solo la ENTRADA automática de compras (horario
    regular). Para saber si estamos en cualquier sesión válida para
    ESCANEAR/ALERTAR (incluye premarket y after-hours), usar
    `is_within_extended_session()` en su lugar.
    """
    now_ny = datetime.now(NY_TZ).time()
    start = dtime.fromisoformat(config.TRADING_WINDOW_START_ET + ":00")
    end = dtime.fromisoformat(config.TRADING_WINDOW_END_ET + ":00")
    return start <= now_ny <= end


def is_within_extended_session():
    """
    True si la hora actual (NY) cae dentro de premarket + regular +
    after-hours (4:00am - 8:00pm ET). Se usa como guardia rápida al
    inicio de main.py: el cron de GitHub Actions corre con un margen de
    seguridad de ±15-20 min por el cambio de horario de verano/invierno
    (ver .github/workflows/screening.yml), así que esta función descarta
    esos minutos de margen sin gastar llamadas a las APIs de datos.
    """
    now_ny_full = datetime.now(NY_TZ)
    if now_ny_full.weekday() >= 5:  # sábado=5, domingo=6 -> mercado cerrado
        return False
    return dtime(4, 0) <= now_ny_full.time() <= dtime(20, 0)


def get_current_session() -> str:
    """
    Devuelve la sesión exacta en la que estamos ahora (hora NY):
    "premarket", "regular", "afterhours" o "closed".

    Los límites exactos (4:00/9:30/16:00/20:00) son los estándar de
    mercado en EE.UU. — no son configurables por config.py porque son
    horarios fijos de la bolsa, a diferencia de TRADING_WINDOW_START_ET/
    END_ET (que sí son una elección de estrategia, no un horario de bolsa).
    """
    now_ny_full = datetime.now(NY_TZ)
    if now_ny_full.weekday() >= 5:
        return "closed"

    now_time = now_ny_full.time()
    if dtime(4, 0) <= now_time < dtime(9, 30):
        return "premarket"
    if dtime(9, 30) <= now_time < dtime(16, 0):
        return "regular"
    if dtime(16, 0) <= now_time <= dtime(20, 0):
        return "afterhours"
    return "closed"


def get_premarket_high(bars):
    """
    PMH (Premarket High): el máximo alcanzado entre las 4:00am y las
    9:30am hora de Nueva York, del día de trading más reciente presente
    en `bars`. Es la referencia clave del setup Gap & Go — distinta del
    rango de apertura (ORB), que se mide DESPUÉS de que abre el mercado.
    """
    if bars.empty:
        return None
    try:
        idx_ny = bars.index.tz_convert(NY_TZ) if bars.index.tz is not None else bars.index
        most_recent_date = idx_ny[-1].date()
        same_day_mask = idx_ny.date == most_recent_date
        premarket_mask = (idx_ny.time >= dtime(4, 0)) & (idx_ny.time < dtime(9, 30))
        pmh_bars = bars[same_day_mask & premarket_mask]
        if pmh_bars.empty:
            return None
        return float(pmh_bars["high"].max())
    except Exception as e:
        logger.warning(f"Error calculando PMH: {e}")
        return None


def compute_vwap(bars):
    """
    VWAP (precio promedio ponderado por volumen) ACUMULADO DESDE LA
    APERTURA DE LA SESIÓN DEL DÍA — es el indicador #1 que usan los
    traders profesionales de small caps para confirmar entradas en largo.

    IMPORTANTE: no usamos `ta.volume.VolumeWeightedAveragePrice` porque
    esa implementación calcula una ventana MÓVIL de 14 velas (rolling),
    no el VWAP real de trading (acumulado desde la apertura), y además
    da NaN con pocas barras. Lo calculamos manualmente para que coincida
    con lo que un trader ve en su plataforma.
    """
    if bars.empty or len(bars) < 2:
        return None
    try:
        idx_ny = bars.index.tz_convert(NY_TZ) if bars.index.tz is not None else bars.index
        most_recent_date = idx_ny[-1].date()
        today_bars = bars[idx_ny.date == most_recent_date]
        if today_bars.empty:
            today_bars = bars  # respaldo si no se pudo filtrar por fecha

        typical_price = (today_bars["high"] + today_bars["low"] + today_bars["close"]) / 3
        cumulative_pv = (typical_price * today_bars["volume"]).cumsum()
        cumulative_vol = today_bars["volume"].cumsum()

        if cumulative_vol.iloc[-1] == 0:
            return None
        vwap_series = cumulative_pv / cumulative_vol
        return float(vwap_series.iloc[-1])
    except Exception as e:
        logger.warning(f"Error calculando VWAP: {e}")
        return None


def compute_macd_histogram(bars):
    """
    MACD (12,26,9) sobre velas de 1 minuto. Un histograma positivo y
    creciente confirma que el momentum de corto plazo sigue siendo
    alcista - es un segundo filtro independiente del ORB para evitar
    entrar justo cuando el impulso ya se está agotando.
    """
    if bars.empty or len(bars) < 26:
        return None
    try:
        macd_ind = ta.trend.MACD(close=bars["close"])
        histogram = macd_ind.macd_diff()
        return float(histogram.iloc[-1])
    except Exception as e:
        logger.warning(f"Error calculando MACD: {e}")
        return None


def get_opening_range(bars, window_minutes=config.ORB_WINDOW_MINUTES):
    """
    Dado un DataFrame de barras de 1 minuto (ordenado cronológicamente),
    devuelve (rango_alto, rango_bajo) de los primeros `window_minutes`
    minutos de sesión regular presentes en el DataFrame.

    Se asume que `bars` ya está filtrado para incluir solo la sesión
    regular (>= 9:30am ET) en el flujo real; aquí simplemente tomamos
    las primeras `window_minutes` filas.
    """
    if bars.empty or len(bars) < window_minutes:
        return None, None

    opening_slice = bars.iloc[:window_minutes]
    range_high = opening_slice["high"].max()
    range_low = opening_slice["low"].min()
    return range_high, range_low


def check_gap_and_go_retest(bars, pmh: float, candidate: dict):
    """
    Lógica de entrada "Gap & Go" basada en RETEST del PMH — la lección
    más repetida en las 4 estrategias analizadas: "no persigas la vela
    verde, compra el retroceso".

    Secuencia exigida:
      1. Ruptura confirmada: alguna vela ya cerró por encima del PMH con
         volumen alto (no una mecha, un cierre sólido).
      2. Retroceso (pullback): velas rojas DESPUÉS de la ruptura, con
         volumen decreciente (toma de ganancias, no presión de venta real).
      3. Reclamo: la vela más reciente vuelve a cerrar en verde, cerca del
         PMH (dentro de ±3%), y por encima del VWAP.
    Solo si las 3 se cumplen, se genera la señal de entrada.
    """
    decision = {
        "symbol": candidate["symbol"], "entry_signal": False, "entry_price": None,
        "pmh": pmh, "reason": "",
    }

    if bars.empty or pmh is None or len(bars) < 6:
        decision["reason"] = "Datos insuficientes para evaluar el retest del PMH"
        return decision

    vwap = compute_vwap(bars)
    if vwap is None:
        decision["reason"] = "VWAP no disponible todavía"
        return decision

    # --- 1) Ruptura confirmada (cierre sólido sobre el PMH, con volumen) ---
    avg_volume = bars["volume"].mean()
    breakout_idx = None
    for i in range(len(bars)):
        if bars["close"].iloc[i] > pmh and bars["volume"].iloc[i] > avg_volume * 1.5:
            breakout_idx = i
            break

    if breakout_idx is None:
        decision["reason"] = "Aún no hay ruptura confirmada del PMH con volumen"
        return decision

    post_breakout = bars.iloc[breakout_idx + 1:]
    if len(post_breakout) < 2:
        decision["reason"] = "Ruptura reciente, esperando a que se forme el retroceso"
        return decision

    # --- 2) Retroceso con volumen decreciente ---
    pullback = post_breakout[post_breakout["close"] < post_breakout["open"]]
    if pullback.empty:
        decision["reason"] = "Sin retroceso todavía — no perseguir la vela verde"
        return decision

    volumes = pullback["volume"].tolist()
    volume_declining = all(volumes[i] >= volumes[i + 1] for i in range(len(volumes) - 1)) if len(volumes) > 1 else True

    # --- 3) Reclamo: vela verde, cerca del PMH, sobre VWAP ---
    last_bar = bars.iloc[-1]
    near_pmh = abs(last_bar["close"] - pmh) / pmh <= 0.03
    holding_above_vwap = last_bar["close"] > vwap
    reclaiming_green = last_bar["close"] > last_bar["open"]

    if volume_declining and near_pmh and holding_above_vwap and reclaiming_green:
        decision["entry_signal"] = True
        decision["entry_price"] = float(last_bar["close"])
        decision["reason"] = "Retest del PMH confirmado: volumen decreciente + reclamo sobre VWAP"
    else:
        reasons = []
        if not volume_declining:
            reasons.append("el volumen del retroceso no está decreciendo")
        if not near_pmh:
            reasons.append("el precio aún no está cerca del PMH")
        if not holding_above_vwap:
            reasons.append("precio por debajo del VWAP")
        if not reclaiming_green:
            reasons.append("todavía no hay vela verde de reclamo")
        decision["reason"] = "; ".join(reasons)

    return decision


def compute_extension_metrics(bars, pmh, vwap):
    """
    Mide qué tan "extendido" está el precio respecto a sus referencias
    clave (VWAP y PMH) y si el volumen se está acelerando o apagando en
    las últimas velas. Es la base del No-Chase Engine: separar "es un
    buen candidato" (Quality) de "es buen momento para entrar ahora"
    (Entry) — una acción puede tener Quality alto pero estar ya muy
    extendida, con el volumen cayendo, justo cuando menos conviene
    perseguirla.
    """
    metrics = {"pct_from_vwap": None, "pct_from_pmh": None, "volume_rising": None}
    if bars.empty:
        return metrics

    last_price = float(bars["close"].iloc[-1])

    if vwap:
        metrics["pct_from_vwap"] = round(((last_price - vwap) / vwap) * 100, 2)
    if pmh:
        metrics["pct_from_pmh"] = round(((last_price - pmh) / pmh) * 100, 2)

    # Tendencia del volumen: últimas 3 velas vs. las 3 anteriores a esas.
    if len(bars) >= 6:
        recent = bars["volume"].iloc[-3:].mean()
        previous = bars["volume"].iloc[-6:-3].mean()
        if previous > 0:
            metrics["volume_rising"] = recent >= previous

    return metrics


def classify_chase_risk(extension_metrics: dict) -> str:
    """
    Clasifica el riesgo de "perseguir el precio" en 4 niveles, según la
    idea del Resumen Ejecutivo: NORMAL / EXTENDIDO / MUY_EXTENDIDO /
    NO_CHASE. Los umbrales de abajo son puntos de partida razonables,
    NO verdades absolutas — el propio documento que los inspiró dice
    explícitamente que deben calibrarse con datos reales de operación.

    NO_CHASE es la señal más fuerte: "candidato excelente, pero no
    entrar ahora" — protege contra comprar justo en el pico.
    """
    pct_vwap = extension_metrics.get("pct_from_vwap")
    volume_rising = extension_metrics.get("volume_rising")

    if pct_vwap is None:
        return "SIN_DATOS"

    # OJO: "is False" falla con numpy.bool_ (identidad, no valor) — se usa
    # "not volume_rising" con chequeo explícito de None primero.
    if pct_vwap >= 25 and volume_rising is not None and not volume_rising:
        return "NO_CHASE"
    if pct_vwap >= 25:
        return "MUY_EXTENDIDO"
    if pct_vwap >= 12:
        return "EXTENDIDO"
    return "NORMAL"


def compute_entry_score(bars, pmh, vwap) -> float:
    """
    ENTRY SCORE (0-10): separado del score de calidad del ticker (Gap,
    Float, RVOL, etc. — eso sigue siendo "score"/Quality Score). Este
    mide específicamente si AHORA es buen momento para entrar:
      - Chase risk (peso más alto): NORMAL=10, EXTENDIDO=6,
        MUY_EXTENDIDO=3, NO_CHASE=0.
      - Momentum (MACD histogram positivo y volumen subiendo).
    Un Quality Score de 10 con Entry Score bajo significa: "excelente
    candidato, pero no es el momento — esperar pullback/retest".
    """
    if bars.empty:
        return 0.0

    ext = compute_extension_metrics(bars, pmh, vwap)
    chase = classify_chase_risk(ext)

    chase_points = {"NORMAL": 6.0, "EXTENDIDO": 3.5, "MUY_EXTENDIDO": 1.5, "NO_CHASE": 0.0, "SIN_DATOS": 3.0}
    score = chase_points.get(chase, 3.0)

    macd_hist = compute_macd_histogram(bars)
    if macd_hist is not None and macd_hist > 0:
        score += 2.0

    if ext.get("volume_rising"):
        score += 2.0

    return round(min(10.0, score), 2)


def check_orb_breakout(bars, candidate: dict):
    """
    Evalúa si el candidato (ya scoreado) confirma un breakout válido del
    rango de apertura, con volumen de confirmación.

    Devuelve un dict con la decisión y los niveles clave para la ejecución.
    """
    decision = {
        "symbol": candidate["symbol"],
        "entry_signal": False,
        "entry_price": None,
        "range_high": None,
        "range_low": None,
        "reason": "",
    }

    if bars.empty or len(bars) < config.ORB_WINDOW_MINUTES + 1:
        decision["reason"] = "Datos insuficientes para calcular el rango de apertura"
        return decision

    range_high, range_low = get_opening_range(bars)
    decision["range_high"] = range_high
    decision["range_low"] = range_low

    if range_high is None:
        decision["reason"] = "No se pudo calcular el rango de apertura"
        return decision

    # Barras posteriores al rango de apertura
    post_range = bars.iloc[config.ORB_WINDOW_MINUTES:]
    if post_range.empty:
        decision["reason"] = "Aún no hay barras después del rango de apertura"
        return decision

    last_bar = post_range.iloc[-1]
    avg_range_volume = bars.iloc[:config.ORB_WINDOW_MINUTES]["volume"].mean()

    breakout_price_ok = last_bar["close"] > range_high
    breakout_volume_ok = (
        avg_range_volume > 0 and last_bar["volume"] > avg_range_volume * 1.5
    )

    # --- Confirmaciones adicionales: VWAP y MACD ---
    vwap = compute_vwap(bars)
    decision["vwap"] = vwap
    vwap_ok = True
    if config.REQUIRE_VWAP_CONFIRMATION:
        vwap_ok = vwap is not None and last_bar["close"] > vwap

    macd_histogram = compute_macd_histogram(bars)
    decision["macd_histogram"] = macd_histogram
    macd_ok = True
    if config.REQUIRE_MACD_CONFIRMATION:
        macd_ok = macd_histogram is not None and macd_histogram > 0

    # Evitar comprar si el precio ya corrió demasiado desde el rango de
    # apertura sin ningún pullback (riesgo de comprar en el pico / "backside")
    extended_move_pct = ((last_bar["close"] - range_high) / range_high) * 100
    too_extended = extended_move_pct > 40

    if breakout_price_ok and breakout_volume_ok and vwap_ok and macd_ok and not too_extended:
        decision["entry_signal"] = True
        decision["entry_price"] = float(last_bar["close"])
        decision["reason"] = "Breakout confirmado (volumen + VWAP + MACD)"
    else:
        reasons = []
        if not breakout_price_ok:
            reasons.append("precio no rompe el rango")
        if not breakout_volume_ok:
            reasons.append("volumen insuficiente para confirmar")
        if not vwap_ok:
            reasons.append("precio por debajo del VWAP")
        if not macd_ok:
            reasons.append("MACD sin momentum alcista")
        if too_extended:
            reasons.append("movimiento ya muy extendido (riesgo de backside)")
        decision["reason"] = "; ".join(reasons)

    return decision


def evaluate_candidates_for_entry(fetcher, ranked_candidates):
    """
    Recorre los candidatos con señal COMPRA_LARGO y evalúa el breakout ORB
    en tiempo real. Devuelve la lista de decisiones de entrada.
    """
    entries = []
    for candidate in ranked_candidates:
        if candidate["signal"] != "COMPRA_LARGO":
            continue

        bars = fetcher.get_bars(candidate["symbol"], minutes_back=60)
        decision = check_orb_breakout(bars, candidate)
        decision["score"] = candidate["score"]
        decision["atr"] = candidate.get("atr")
        entries.append(decision)

        if decision["entry_signal"]:
            logger.info(f"✅ Señal de entrada LARGO: {candidate['symbol']} @ {decision['entry_price']}")

    return entries
