"""
main.py
-------
Orquestador principal.

Flujo de cada ejecución:
  1. Pide (opcionalmente) tickers manuales por consola y los combina con
     tu watchlist guardada + la lista base.
  2. Puntúa todos los candidatos (1-10) y muestra el ranking Top N.
  3. Para los candidatos con score >= SCORE_MIN_TO_BUY, evalúa el breakout
     ORB en tiempo real.
  4. Si hay señal de entrada, ejecuta la compra en PAPER TRADING con
     stop-loss dinámico (ATR).
  5. Actualiza posiciones abiertas: take-profit escalonado + trailing stop.

Uso recomendado:
  - Ejecuta este script en un loop (cron, GitHub Actions programado, o un
    `while True` con `time.sleep`) durante el horario de mercado.
  - Empieza SIEMPRE con config.PAPER_TRADING = True.
"""

import logging
import os
import time

import config

# En GitHub Actions (u otro CI) no hay consola interactiva disponible.
# Detectamos el entorno automáticamente para no bloquear la ejecución
# esperando un input() que nunca llegará.
RUNNING_IN_CI = os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"
from data_fetcher import DataFetcher
from screener import get_universe, rank_candidates, print_ranking_table, prompt_manual_tickers_cli
from strategy import evaluate_candidates_for_entry, is_within_extended_session, get_current_session
from execution import PositionManager
from notifier import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("main")


def run_screening_cycle(fetcher, position_manager, notifier, interactive=True):
    if interactive:
        prompt_manual_tickers_cli()

    universe = get_universe()
    logger.info(f"Universo de tickers a evaluar ({len(universe)}): {universe}")

    ranked = rank_candidates(fetcher, tickers=universe)

    print("\n" + "=" * 90)
    print(f"TOP {len(ranked)} CANDIDATOS - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)
    print_ranking_table(ranked)
    print("=" * 90 + "\n")

    buy_signals = [r for r in ranked if r["signal"] == "COMPRA_LARGO"]
    if buy_signals:
        tickers_str = ", ".join(f"{r['symbol']} ({r['score']})" for r in buy_signals)
        notifier.send(f"📊 Candidatos con score >= {config.SCORE_MIN_TO_BUY}: {tickers_str}")

    # Evaluar breakout ORB en tiempo real para los candidatos con score alto
    entries = evaluate_candidates_for_entry(fetcher, ranked)

    # --- Perfil de riesgo según la sesión ACTUAL (premarket/regular/after-hours) ---
    # IMPORTANTE (corrección de seguridad): este candado no existía antes en el
    # camino de GitHub Actions — is_within_trading_window() solo se usaba en la
    # UI de Streamlit. Con el cron ahora cubriendo sesión extendida (Pieza 1),
    # sin este chequeo el bot habría intentado entrar en premarket/after-hours
    # con las mismas reglas de horario regular, sin ninguna de las protecciones
    # de liquidez reducida. Se agrega aquí, en el único lugar que de verdad
    # ejecuta órdenes.
    session = get_current_session()
    risk_profile = config.get_session_risk_profile(session)
    candidates_by_symbol = {c["symbol"]: c for c in ranked}

    if not risk_profile.get("allow_auto_entry", False):
        logger.info(f"Sesión '{session}': entradas automáticas deshabilitadas para este perfil.")
        return ranked

    for entry in entries:
        if not entry["entry_signal"]:
            continue
        symbol = entry["symbol"]
        candidate = candidates_by_symbol.get(symbol, {})

        min_score = risk_profile.get("min_score_override")
        if min_score is not None and entry.get("score", 0) < min_score:
            logger.info(
                f"[{symbol}] Score {entry.get('score')} por debajo del umbral de sesión "
                f"'{session}' ({min_score}) — se omite la entrada."
            )
            continue

        if risk_profile.get("require_catalyst") and not candidate.get("catalyst_verified"):
            logger.info(
                f"[{symbol}] Sesión '{session}' exige catalizador verificado y no se confirmó "
                f"— se omite la entrada (protección contra ruido de baja liquidez)."
            )
            continue

        # No-Chase: Quality alto no basta si el precio ya está muy extendido
        # (lejos del VWAP, volumen cayendo) — eso es justo el momento de MÁS
        # riesgo de comprar en el pico, no de menos.
        chase_status = candidate.get("chase_status")
        if chase_status in ("MUY_EXTENDIDO", "NO_CHASE"):
            logger.info(
                f"[{symbol}] Quality Score alto ({entry.get('score')}) pero Chase Status "
                f"'{chase_status}' — candidato excelente, momento equivocado. Se omite la entrada."
            )
            continue

        max_spread = risk_profile.get("max_spread_pct")
        if max_spread is not None:
            spread_pct = fetcher.get_quote_spread_pct(symbol)
            if spread_pct is None or spread_pct > max_spread:
                logger.info(
                    f"[{symbol}] Spread ({spread_pct}%) excede el máximo de sesión '{session}' "
                    f"({max_spread}%) o no se pudo confirmar — se omite la entrada."
                )
                continue

        size_multiplier = risk_profile.get("position_size_multiplier", 1.0)
        stop_price = position_manager.calculate_dynamic_stop(entry["entry_price"], entry.get("atr"))
        base_qty = position_manager.calculate_qty(entry["entry_price"], stop_price)
        adjusted_qty = max(1, int(base_qty * size_multiplier)) if base_qty else None

        order_type = "limit" if risk_profile.get("force_limit_orders") else "market"

        position_manager.enter_long(
            symbol=symbol,
            entry_price=entry["entry_price"],
            atr=entry.get("atr"),
            qty=adjusted_qty,
            order_type=order_type,
        )

    return ranked


def update_open_positions(fetcher, position_manager):
    for symbol in list(position_manager.positions.keys()):
        price = fetcher.get_latest_price(symbol)
        if price:
            position_manager.update_position(symbol, price)


def main(loop=False, interval_seconds=60):
    fetcher = DataFetcher()
    notifier = TelegramNotifier()
    position_manager = PositionManager(fetcher.trading_client, notifier=notifier)

    logger.info(
        f"Bot iniciado. PAPER_TRADING={config.PAPER_TRADING}. "
        f"Score mínimo de compra: {config.SCORE_MIN_TO_BUY}"
    )

    # En modo continuo local (loop=True), conectamos el WebSocket de Alpaca
    # como fuente principal de precio/volumen en tiempo real (más rápido
    # que consultar REST cada ciclo). Si el WebSocket falla o se cae, el
    # propio data_fetcher.py cae solo a REST, y como último recurso usa el
    # último dato recibido por WebSocket aunque esté viejo.
    realtime_feed = None
    if loop:
        try:
            from realtime_feed import RealtimeFeed
            universe_symbols = [e["symbol"] for e in get_universe()]
            if universe_symbols:
                realtime_feed = RealtimeFeed(universe_symbols)
                realtime_feed.start()
                fetcher.set_realtime_feed(realtime_feed)
                logger.info(f"WebSocket en tiempo real iniciado para: {universe_symbols}")
        except Exception as e:
            logger.warning(f"No se pudo iniciar el WebSocket en tiempo real, se sigue solo con REST: {e}")

    interactive_default = not RUNNING_IN_CI

    if not loop:
        if RUNNING_IN_CI and not is_within_extended_session():
            # El cron de GitHub Actions corre con un margen de seguridad de
            # ±15-20 min (ver .github/workflows/screening.yml, comentario
            # sobre horario de verano/invierno). Fuera de premarket/regular/
            # after-hours, se descarta rápido sin gastar llamadas a las APIs.
            logger.info("Fuera de la ventana 4:00am-8:00pm ET (o fin de semana) — se omite este ciclo.")
            return
        run_screening_cycle(fetcher, position_manager, notifier, interactive=interactive_default)
        update_open_positions(fetcher, position_manager)
        return

    # Modo continuo: útil para correrlo durante toda la sesión de mercado
    first_cycle = True
    while True:
        try:
            run_screening_cycle(
                fetcher, position_manager, notifier,
                interactive=(first_cycle and interactive_default),
            )
            first_cycle = False
            update_open_positions(fetcher, position_manager)
        except KeyboardInterrupt:
            logger.info("Bot detenido manualmente.")
            if realtime_feed:
                realtime_feed.stop()
            break
        except Exception as e:
            logger.exception(f"Error en el ciclo principal: {e}")

        time.sleep(interval_seconds)


if __name__ == "__main__":
    # Cambia loop=True para correrlo en modo continuo durante la sesión.
    main(loop=False)
