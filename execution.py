"""
execution.py
------------
Gestión de órdenes en Alpaca (paper trading por defecto):

  - Tamaño de posición basado en riesgo (% de la cuenta) y en la distancia
    del stop calculada con ATR.
  - Stop-loss inicial dinámico: entrada - (ATR * multiplicador), acotado a
    un % máximo de riesgo (config.STOP_LOSS_MAX_PCT).
  - Take-profit escalonado: vende fracciones de la posición en los niveles
    definidos en config.TAKE_PROFIT_TIERS.
  - Trailing stop dinámico para el remanente: el stop solo puede subir
    (nunca bajar), siguiendo al precio a una distancia de
    config.TRAILING_STOP_PCT una vez que la posición ya está en ganancia.

Todas las posiciones se guardan en memoria en `PositionManager.positions`
mientras el bot está corriendo. Para producción real se recomienda
persistir este estado (ej. en un archivo JSON o base de datos) para
sobrevivir reinicios del proceso.
"""

import csv
import logging
import os
import time
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

import config
from notifier import alert_order_executed, alert_position_closed

logger = logging.getLogger("execution")


def _write_journal_record(symbol, qty, entry_price, exit_price, reason):
    """Agrega una operación cerrada (o parcial) al diario CSV de trading."""
    pnl_dollars = round((exit_price - entry_price) * qty, 2)
    pnl_pct = round(((exit_price - entry_price) / entry_price) * 100, 2) if entry_price else 0.0

    path = config.TRADE_JOURNAL_FILE
    file_exists = os.path.exists(path)
    try:
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp", "symbol", "qty", "entry_price", "exit_price",
                    "pnl_dollars", "pnl_pct", "reason",
                ])
            writer.writerow([
                datetime.utcnow().isoformat(), symbol, qty, entry_price, exit_price,
                pnl_dollars, pnl_pct, reason,
            ])
    except Exception as e:
        logger.warning(f"No se pudo escribir en el diario de operaciones: {e}")


class Position:
    def __init__(self, symbol, qty, entry_price, stop_price, atr, tp_tiers=None, trailing_pct=None):
        self.symbol = symbol
        self.original_qty = qty
        self.qty_remaining = qty
        self.entry_price = entry_price
        self.stop_price = stop_price          # stop actual (dinámico, solo sube)
        self.highest_price = entry_price
        self.atr = atr
        self.tiers_hit = set()                # índices de tp_tiers ya ejecutados
        # Take-profit y trailing PERSONALIZABLES por operación. Si no se
        # pasan (None), se usan los valores por defecto de config.py.
        self.tp_tiers = tp_tiers if tp_tiers is not None else config.get_active_exit_profile()["take_profit_tiers"]
        self.trailing_pct = trailing_pct if trailing_pct is not None else config.get_active_exit_profile()["trailing_stop_pct"]
        # Time-stop: si no toca NINGÚN take-profit dentro de esta ventana, se
        # cierra por pérdida de momentum (idea del "Resumen Ejecutivo": el
        # trade dejó de ser el setup que motivó la entrada). Se captura al
        # ABRIR la posición, igual que tp_tiers/trailing_pct — cambiar el
        # perfil después no afecta retroactivamente posiciones ya abiertas.
        self.time_stop_minutes = config.get_active_exit_profile()["time_stop_minutes"]
        self.stop_order_id = None
        self.opened_at = datetime.utcnow()
        self.closed = False

    def minutes_open(self) -> float:
        return (datetime.utcnow() - self.opened_at).total_seconds() / 60.0

    def gain_pct(self, current_price):
        return ((current_price - self.entry_price) / self.entry_price) * 100


class PositionManager:
    def __init__(self, trading_client: TradingClient, notifier=None):
        self.client = trading_client
        self.notifier = notifier
        self.positions = {}  # symbol -> Position

    # ------------------------------------------------------------------
    # TAMAÑO DE POSICIÓN
    # ------------------------------------------------------------------
    def calculate_qty(self, entry_price, stop_price):
        """
        Dos modos, según config.EXECUTION_MODE:
          - "single_bullet": una sola compra con un monto FIJO en dólares
            (config.TRADE_AMOUNT_USD), sin importar el stop. Simple y
            predecible mientras se aprende/prueba la estrategia.
          - "risk_based": el modo original, calcula la cantidad según el
            % de riesgo de la cuenta y la distancia del stop.
        """
        if config.EXECUTION_MODE == "single_bullet":
            if entry_price <= 0:
                return 0
            qty = int(config.TRADE_AMOUNT_USD / entry_price)
            return max(qty, 0)

        # --- modo risk_based (original) ---
        account = self.client.get_account()
        equity = float(account.equity)

        risk_amount = equity * (config.RISK_PER_TRADE_PCT / 100.0)
        stop_distance = max(entry_price - stop_price, 0.01)

        qty = int(risk_amount / stop_distance)
        return max(qty, 0)

    def calculate_dynamic_stop(self, entry_price, atr):
        """
        Stop-loss dinámico basado en ATR, pero acotado entre un piso y un
        techo (en % del precio) — los tres valores vienen del perfil de
        salida ACTIVO (config.EXIT_MODE: "swing" o "scalping"), no de
        constantes fijas, para que el modo scalping use un stop mucho
        más ajustado automáticamente:
          - Piso (stop_loss_min_pct): nunca más ajustado que esto, para no
            saltar por simple ruido de una vela en un ticker volátil.
          - Techo (stop_loss_max_pct): nunca arriesgar más que esto, aunque
            el ATR sea muy grande.
        """
        profile = config.get_active_exit_profile()

        if atr and atr > 0:
            atr_distance = atr * profile["atr_stop_multiplier"]
        else:
            atr_distance = entry_price * (profile["stop_loss_max_pct"] / 100.0)

        min_distance = entry_price * (profile["stop_loss_min_pct"] / 100.0)
        max_distance = entry_price * (profile["stop_loss_max_pct"] / 100.0)

        stop_distance = min(max(atr_distance, min_distance), max_distance)
        return round(entry_price - stop_distance, 2)

    # ------------------------------------------------------------------
    # ENTRADA
    # ------------------------------------------------------------------
    def enter_long(self, symbol, entry_price, atr, qty=None, stop_price=None,
                    tp_tiers=None, trailing_pct=None, order_type="market", limit_price=None):
        """
        Todos los parámetros extra son OPCIONALES — si no se pasan, el bot
        usa sus cálculos automáticos de siempre (stop dinámico por ATR,
        cantidad por single-bullet/riesgo, TP/trailing de config.py).

        Parámetros para personalizar la orden a mano:
          qty:          cantidad de acciones (si no se pasa, se calcula)
          stop_price:   stop-loss inicial en $ (si no se pasa, se calcula)
          tp_tiers:     lista [{"gain_pct":15,"sell_fraction":0.5}, ...]
          trailing_pct: % de trailing stop para el remanente
          order_type:   "market" (por defecto) o "limit"
          limit_price:  precio límite (solo si order_type="limit"; si no
                        se pasa, se usa entry_price como límite)
        """
        if symbol in self.positions:
            logger.info(f"[{symbol}] Ya existe una posición abierta, se omite nueva entrada.")
            return None

        if len(self.positions) >= config.MAX_POSITIONS_OPEN:
            logger.info("Máximo de posiciones abiertas alcanzado, se omite entrada.")
            return None

        final_stop_price = stop_price if stop_price is not None else self.calculate_dynamic_stop(entry_price, atr)
        final_qty = qty if qty is not None else self.calculate_qty(entry_price, final_stop_price)

        if not final_qty or final_qty <= 0:
            logger.warning(f"[{symbol}] Tamaño de posición calculado/indicado es 0, se omite orden.")
            return None

        if order_type == "limit":
            order_req = LimitOrderRequest(
                symbol=symbol,
                qty=final_qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price if limit_price is not None else entry_price, 2),
            )
        else:
            order_req = MarketOrderRequest(
                symbol=symbol,
                qty=final_qty,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
            )

        order = self.client.submit_order(order_req)
        logger.info(
            f"🟢 Orden de COMPRA enviada: {symbol} x{final_qty} "
            f"({order_type}, paper={config.PAPER_TRADING})"
        )

        # --- Confirmar el FILL REAL antes de fijar stop/TP (bug corregido) ---
        # Antes se creaba la posición directo con `entry_price` (el precio
        # ESTIMADO al momento de la señal), ignorando la respuesta real de
        # Alpaca. En small caps con spreads anchos el precio de ejecución
        # real puede ser distinto, desalineando el stop-loss calculado.
        # Se espera brevemente (Alpaca paper suele llenar en <1-2s) y si no
        # confirma a tiempo, se usa el estimado como respaldo, con log claro.
        fill_price, fill_qty = self._wait_for_fill(order.id, symbol)
        if fill_price is not None:
            actual_entry_price = fill_price
            actual_qty = fill_qty if fill_qty else final_qty
            slippage_pct = round(((fill_price - entry_price) / entry_price) * 100, 3) if entry_price else 0
            logger.info(
                f"[{symbol}] Fill real confirmado: ${fill_price:.4f} x{actual_qty:g} "
                f"(estimado era ${entry_price:.4f}, slippage {slippage_pct:+.3f}%)"
            )
            # Si el stop no fue fijado a mano, se recalcula con el precio REAL,
            # no con el estimado — así el % de riesgo real queda correcto.
            if stop_price is None:
                final_stop_price = self.calculate_dynamic_stop(actual_entry_price, atr)
        else:
            actual_entry_price = entry_price
            actual_qty = final_qty
            logger.warning(
                f"[{symbol}] No se pudo confirmar el fill real a tiempo — "
                f"se usa el precio estimado (${entry_price:.4f}) provisionalmente."
            )

        position = Position(symbol, actual_qty, actual_entry_price, final_stop_price, atr,
                             tp_tiers=tp_tiers, trailing_pct=trailing_pct)
        position.estimated_entry_price = entry_price  # se conserva para medir slippage en el journal
        self.positions[symbol] = position

        # Stop-loss inicial como orden STOP separada (se irá reemplazando
        # a medida que el trailing stop sube).
        position.stop_order_id = self._submit_stop_order(symbol, final_qty, final_stop_price)

        if self.notifier:
            alert_order_executed(symbol, actual_qty, actual_entry_price, final_stop_price)

        return position

    def _wait_for_fill(self, order_id, symbol, timeout_seconds=5, poll_interval=0.5):
        """
        Espera brevemente a que Alpaca confirme el llenado real de la orden.
        Devuelve (precio_real, cantidad_real) o (None, None) si no confirma
        dentro del timeout — en ese caso el llamador debe usar un estimado
        como respaldo, nunca asumir que "None" significa error fatal.
        """
        elapsed = 0.0
        while elapsed < timeout_seconds:
            try:
                order = self.client.get_order_by_id(order_id)
                if str(order.status).lower() in ("filled", "orderstatus.filled") and order.filled_avg_price:
                    return float(order.filled_avg_price), float(order.filled_qty)
            except Exception as e:
                logger.info(f"[{symbol}] Error consultando estado de la orden {order_id}: {e}")
            time.sleep(poll_interval)
            elapsed += poll_interval
        return None, None

    def _submit_stop_order(self, symbol, qty, stop_price):
        try:
            stop_req = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                stop_price=round(stop_price, 2),
            )
            order = self.client.submit_order(stop_req)
            return order.id
        except Exception as e:
            logger.error(f"[{symbol}] Error enviando orden de stop-loss: {e}")
            return None

    def _replace_stop_order(self, position: Position, new_stop_price, new_qty):
        """Cancela el stop anterior y coloca uno nuevo (así se logra el trailing dinámico)."""
        if position.stop_order_id:
            try:
                self.client.cancel_order_by_id(position.stop_order_id)
            except Exception as e:
                logger.warning(f"[{position.symbol}] No se pudo cancelar el stop anterior: {e}")

        if new_qty > 0:
            position.stop_order_id = self._submit_stop_order(
                position.symbol, new_qty, new_stop_price
            )
        position.stop_price = new_stop_price

    # ------------------------------------------------------------------
    # ACTUALIZACIÓN CONTINUA: take-profit escalonado + trailing stop
    # ------------------------------------------------------------------
    def update_position(self, symbol, current_price):
        """
        Debe llamarse periódicamente (ej. cada 30-60 segundos) para cada
        posición abierta. Ejecuta:
          1. Ventas parciales al alcanzar los niveles de TAKE_PROFIT_TIERS.
          2. Ajuste del trailing stop (solo hacia arriba).
        """
        position = self.positions.get(symbol)
        if not position or position.closed:
            return

        position.highest_price = max(position.highest_price, current_price)
        gain_pct = position.gain_pct(current_price)

        # --- 1) Take-profit escalonado ---
        for i, tier in enumerate(position.tp_tiers):
            if i in position.tiers_hit:
                continue
            if gain_pct >= tier["gain_pct"]:
                self._execute_partial_take_profit(position, tier, i)

        # --- 2) Trailing stop dinámico (solo sube) ---
        if gain_pct > 0:
            trailing_candidate = position.highest_price * (1 - position.trailing_pct / 100.0)
            if trailing_candidate > position.stop_price:
                logger.info(
                    f"[{symbol}] Subiendo stop dinámico: "
                    f"${position.stop_price:.2f} -> ${trailing_candidate:.2f}"
                )
                self._replace_stop_order(position, trailing_candidate, position.qty_remaining)
                if self.notifier:
                    self.notifier.send(
                        f"🔼 {symbol}: stop-loss ajustado a ${trailing_candidate:.2f} "
                        f"(ganancia actual {gain_pct:.1f}%)"
                    )

        # Si el stop actual quedó por encima del precio actual, se considera cerrada
        if current_price <= position.stop_price:
            self._close_position(position, exit_price=current_price, reason="Stop-loss ejecutado")
            return

        # --- 3) Time-stop: el setup dejó de ser válido si tarda demasiado ---
        # Solo aplica si TODAVÍA no se tocó ningún take-profit (si ya tocó
        # TP1, el trade ya demostró que el movimiento es real, y el trailing
        # stop de arriba pasa a gestionar la salida en su lugar).
        if not position.tiers_hit and position.minutes_open() >= position.time_stop_minutes:
            self._close_position(
                position, exit_price=current_price,
                reason=f"Time-stop: sin TP1 en {position.time_stop_minutes} min (pérdida de momentum)",
            )

    def _execute_partial_take_profit(self, position: Position, tier: dict, tier_index: int):
        qty_to_sell = int(round(position.original_qty * tier["sell_fraction"]))
        qty_to_sell = min(qty_to_sell, position.qty_remaining)
        if qty_to_sell <= 0:
            return

        order_req = MarketOrderRequest(
            symbol=position.symbol,
            qty=qty_to_sell,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        try:
            self.client.submit_order(order_req)
            position.qty_remaining -= qty_to_sell
            position.tiers_hit.add(tier_index)

            exit_price = position.entry_price * (1 + tier["gain_pct"] / 100.0)
            _write_journal_record(
                position.symbol, qty_to_sell, position.entry_price, exit_price,
                reason=f"Take-profit parcial +{tier['gain_pct']}%",
            )

            logger.info(
                f"💰 Take-profit parcial ejecutado: {position.symbol} "
                f"vendidas {qty_to_sell} en +{tier['gain_pct']}%"
            )
            if self.notifier:
                self.notifier.send(
                    f"💰 TAKE-PROFIT {position.symbol}\n"
                    f"Vendidas {qty_to_sell} acciones en +{tier['gain_pct']}%\n"
                    f"Quedan {position.qty_remaining} con trailing stop activo"
                )

            # Reemplazar el stop para que cubra solo la cantidad remanente
            if position.qty_remaining > 0:
                self._replace_stop_order(position, position.stop_price, position.qty_remaining)
            else:
                self._close_position(position, exit_price=exit_price, reason="Posición cerrada por take-profit total")
        except Exception as e:
            logger.error(f"[{position.symbol}] Error ejecutando take-profit parcial: {e}")

    def _close_position(self, position: Position, exit_price: float, reason: str):
        position.closed = True
        gain_pct = round(((exit_price - position.entry_price) / position.entry_price) * 100, 2)

        if position.qty_remaining > 0:
            _write_journal_record(
                position.symbol, position.qty_remaining, position.entry_price, exit_price, reason
            )

        logger.info(f"🔴 Posición cerrada: {position.symbol} ({reason})")
        if self.notifier:
            alert_position_closed(position.symbol, reason, gain_pct)
        del self.positions[position.symbol]

    def close_position_market(self, symbol: str, current_price: float):
        """
        Cierre de EMERGENCIA: cancela el stop activo y vende toda la
        cantidad remanente a mercado de inmediato. Pensado para el botón
        "Cierre de emergencia" del dashboard.
        """
        position = self.positions.get(symbol)
        if not position or position.closed:
            return False

        if position.stop_order_id:
            try:
                self.client.cancel_order_by_id(position.stop_order_id)
            except Exception as e:
                logger.warning(f"[{symbol}] No se pudo cancelar el stop antes del cierre de emergencia: {e}")

        try:
            order_req = MarketOrderRequest(
                symbol=symbol,
                qty=position.qty_remaining,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            self.client.submit_order(order_req)
            self._close_position(position, exit_price=current_price, reason="Cierre manual de emergencia")
            return True
        except Exception as e:
            logger.error(f"[{symbol}] Error en el cierre de emergencia: {e}")
            return False
