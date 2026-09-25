"""
realtime_feed.py
-----------------
Streaming en tiempo real de precio/volumen vía Alpaca WebSockets, con
reconexión automática (heartbeat + backoff progresivo ante excepciones
de red), tal como pediste en la arquitectura de redundancia:

    Precios/Volumen:
      Fuente principal ......... Alpaca WebSockets (este módulo)
      Fallback 1 ................ REST de Alpaca (ya existe en data_fetcher.py)
      Fallback 2 ................ Caché del último dato recibido por WebSocket,
                                   aunque esté "viejo" (stale)

Pensado principalmente para EJECUCIÓN LOCAL CONTINUA (main.py con loop=True
corriendo en tu computadora/VS Code) — una conexión WebSocket persistente
no encaja bien con el modelo de Streamlit (que re-ejecuta el script en
cada interacción), así que en el dashboard de Streamlit se sigue usando
solo REST, que ya funciona bien para ese caso de uso.

Uso típico:
    feed = RealtimeFeed(["IMRN", "CDTG"])
    feed.start()
    ...
    cached = feed.get_latest("IMRN")   # {"price":, "volume_last_bar":, "stale": bool}
    ...
    feed.stop()
"""

import logging
import threading
import time
from datetime import datetime, timezone

from alpaca.data.live import StockDataStream

import config

logger = logging.getLogger("realtime_feed")


class RealtimeFeed:
    # Backoff progresivo entre reintentos de reconexión (segundos)
    RECONNECT_BACKOFF_SECONDS = [2, 5, 10, 30, 60]

    def __init__(self, symbols):
        self.symbols = [s.upper() for s in symbols]
        self._cache = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._stream = None

    # ------------------------------------------------------------------
    # Handlers de los eventos de WebSocket (actualizan la caché en memoria)
    # ------------------------------------------------------------------
    def _update_cache(self, symbol, price=None, volume=None):
        with self._lock:
            entry = self._cache.get(symbol, {})
            if price is not None:
                entry["price"] = price
            if volume is not None:
                entry["volume_last_bar"] = volume
            entry["last_update"] = datetime.now(timezone.utc)
            self._cache[symbol] = entry

    async def _on_trade(self, trade):
        self._update_cache(trade.symbol, price=float(trade.price))

    async def _on_bar(self, bar):
        self._update_cache(bar.symbol, price=float(bar.close), volume=int(bar.volume))

    # ------------------------------------------------------------------
    # Conexión con reconexión automática (heartbeat implícito: si el
    # stream se cae o lanza una excepción de red, se reconecta solo con
    # backoff progresivo, sin tirar el programa completo)
    # ------------------------------------------------------------------
    def _run_stream_with_reconnect(self):
        attempt = 0
        while not self._stop_event.is_set():
            try:
                logger.info(f"Conectando WebSocket de Alpaca para: {self.symbols}")
                self._stream = StockDataStream(
                    config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, feed="iex"
                )
                self._stream.subscribe_trades(self._on_trade, *self.symbols)
                self._stream.subscribe_bars(self._on_bar, *self.symbols)
                attempt = 0  # se reconectó bien, resetea el backoff
                self._stream.run()  # bloqueante hasta que falle, se caiga o se detenga
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"WebSocket de Alpaca se desconectó ({e}), reintentando...")

            if self._stop_event.is_set():
                break

            wait = self.RECONNECT_BACKOFF_SECONDS[min(attempt, len(self.RECONNECT_BACKOFF_SECONDS) - 1)]
            attempt += 1
            time.sleep(wait)

        logger.info("WebSocket de Alpaca detenido.")

    def start(self):
        """Arranca el streaming en un hilo de fondo (no bloquea el resto del bot)."""
        self._thread = threading.Thread(target=self._run_stream_with_reconnect, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        try:
            if self._stream is not None:
                self._stream.stop()
        except Exception:
            pass

    def get_latest(self, symbol, max_age_seconds=15):
        """
        Devuelve el último precio/volumen recibido por WebSocket para el
        símbolo, o None si nunca se recibió nada. Incluye "stale": True
        si el dato tiene más de `max_age_seconds` (quien lo use decide si
        lo usa igual como último recurso, o prefiere ir a REST).
        """
        with self._lock:
            entry = self._cache.get(symbol.upper())
        if not entry:
            return None
        age = (datetime.now(timezone.utc) - entry["last_update"]).total_seconds()
        return {
            "price": entry.get("price"),
            "volume_last_bar": entry.get("volume_last_bar"),
            "age_seconds": round(age, 1),
            "stale": age > max_age_seconds,
        }
