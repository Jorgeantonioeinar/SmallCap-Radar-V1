"""
notifier.py
-----------
Envío de alertas a Telegram. Las credenciales se leen (vía config.py) en
este orden: Streamlit Secrets -> variables de entorno / .env. Si no están
configuradas, las alertas simplemente se registran en el log en vez de
romper el bot.

Cómo obtener tus credenciales:
  1. Habla con @BotFather en Telegram -> /newbot -> te da el TOKEN.
  2. Escríbele un mensaje a tu bot recién creado.
  3. Visita: https://api.telegram.org/bot<TU_TOKEN>/getUpdates
     y busca el campo "chat":{"id": ...} -> ese es tu TELEGRAM_CHAT_ID.
"""

import logging

import requests

import config

logger = logging.getLogger("notifier")


def send_telegram_alert(message: str) -> bool:
    """
    Envía un mensaje (formato Markdown) a Telegram usando
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (Streamlit Secrets o variables
    de entorno, vía config.py). Devuelve True si se envió correctamente,
    False si no hay credenciales configuradas o si falló el envío
    (nunca lanza una excepción que pueda tumbar el bot).
    """
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.info(f"[Telegram no configurado] {message}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Error enviando alerta de Telegram: {e}")
        return False


# ---------------------------------------------------------------------------
# Alertas específicas del bot, ya formateadas en Markdown
# ---------------------------------------------------------------------------
def alert_high_score(symbol: str, score: float, price: float, gap_pct: float):
    send_telegram_alert(
        f"🎯 *Score alto detectado*\n"
        f"*{symbol}* — Score: `{score:.2f}/10`\n"
        f"Precio: `${price:.2f}` · Gap: `{gap_pct:+.1f}%`\n"
        f"_Candidato listo para evaluar entrada en largo._"
    )


def alert_order_executed(symbol: str, qty: int, entry_price: float, stop_price: float):
    risk_pct = ((entry_price - stop_price) / entry_price) * 100 if entry_price else 0
    send_telegram_alert(
        f"🟢 *Compra ejecutada (Paper Trading)*\n"
        f"*{symbol}* x`{qty}`\n"
        f"Entrada: `${entry_price:.2f}` · Stop inicial: `${stop_price:.2f}` "
        f"(`{risk_pct:.1f}%` riesgo)"
    )


def alert_position_closed(symbol: str, reason: str, pnl_pct: float):
    emoji = "💰" if pnl_pct >= 0 else "🔴"
    send_telegram_alert(
        f"{emoji} *Posición cerrada*\n"
        f"*{symbol}* — Resultado: `{pnl_pct:+.1f}%`\n"
        f"Motivo: _{reason}_"
    )


def alert_kill_switch(daily_pnl_pct: float, max_loss_pct: float):
    send_telegram_alert(
        f"🛑 *KILL-SWITCH ACTIVADO*\n"
        f"Pérdida del día: `{daily_pnl_pct:+.2f}%` (límite: `-{max_loss_pct:.1f}%`)\n"
        f"_Las compras nuevas quedan bloqueadas hasta el día siguiente._"
    )


# ---------------------------------------------------------------------------
# Clase de compatibilidad (usada por execution.py) - reutiliza la función
# estándar de arriba, así toda la lógica de envío vive en un solo lugar.
# ---------------------------------------------------------------------------
class TelegramNotifier:
    def __init__(self):
        self.enabled = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
        if not self.enabled:
            logger.warning(
                "Telegram no configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). "
                "Las alertas se mostrarán solo en consola."
            )

    def send(self, message: str):
        sent = send_telegram_alert(message)
        if not sent:
            print(f"[NOTIFICACIÓN - fallback consola] {message}")
