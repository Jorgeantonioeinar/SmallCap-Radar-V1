"""
data_fetcher.py
----------------
Capa única de acceso a datos. Combina:
  - Alpaca Market Data (barras intradía en tiempo real / cotizaciones)
  - Yahoo Finance (float, acciones en circulación, capitalización, volumen
    promedio histórico) vía yfinance

Todas las demás partes del bot (screener, strategy, execution) deberían
pedir los datos a través de este módulo, nunca directamente a las APIs.
"""

import csv
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient

import config

logger = logging.getLogger("data_fetcher")


class DataFetcher:
    def __init__(self):
        if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
            raise RuntimeError(
                "Faltan las variables de entorno ALPACA_API_KEY / ALPACA_SECRET_KEY. "
                "Revisa config.py para instrucciones."
            )

        # Cliente de datos de mercado (funciona igual en paper y en live)
        self.data_client = StockHistoricalDataClient(
            config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
        )

        # Cliente de trading (paper o live según config.PAPER_TRADING)
        self.trading_client = TradingClient(
            config.ALPACA_API_KEY,
            config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        )

        # Cache simple en memoria para no golpear yfinance repetidamente
        self._fundamentals_cache = {}
        self._yahoo_disabled_until = 0.0  # circuit breaker por tiempo (epoch seconds), no permanente
        self._finviz_disabled_until = 0.0
        self._twelvedata_disabled_until = 0.0

        # Feed de streaming en tiempo real (opcional). Se conecta desde
        # fuera con set_realtime_feed() - normalmente en main.py cuando
        # el bot corre en modo continuo local (loop=True). Si no se
        # configura, get_latest_price() sigue funcionando 100% con REST.
        self.realtime_feed = None
        self._fmp_disabled = False

    def set_realtime_feed(self, feed):
        """Conecta un RealtimeFeed (realtime_feed.py) como fuente principal de precio."""
        self.realtime_feed = feed

    # ------------------------------------------------------------------
    # DIAGNÓSTICO: prueba real de conexión (cuenta, cotización, barras)
    # ------------------------------------------------------------------
    def test_connection(self, probe_symbol: str = "AAPL") -> dict:
        """
        Corre 3 pruebas reales contra Alpaca y devuelve el resultado exacto
        de cada una (éxito + detalle, o el mensaje de error real). Pensado
        para mostrarse directo en la UI, sin tener que buscar en los logs.
        """
        result = {"account": None, "quote": None, "bars": None}

        # 1) Cuenta (confirma que las credenciales son válidas y el tipo paper/live)
        try:
            account = self.trading_client.get_account()
            result["account"] = {
                "ok": True,
                "detail": f"Cuenta OK — status={account.status}, paper={config.PAPER_TRADING}, "
                          f"buying_power=${float(account.buying_power):,.2f}",
            }
        except Exception as e:
            result["account"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

        # 2) Cotización en vivo (feed IEX) de un ticker líquido conocido (AAPL)
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=probe_symbol, feed=DataFeed.IEX)
            quote = self.data_client.get_stock_latest_quote(req)[probe_symbol]
            result["quote"] = {
                "ok": bool(quote.bid_price or quote.ask_price),
                "detail": f"bid={quote.bid_price} ask={quote.ask_price} (símbolo de prueba: {probe_symbol})",
            }
        except Exception as e:
            result["quote"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

        # 3) Barras intradía (mismo feed y cliente que usa todo el screener)
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=120)
            req = StockBarsRequest(
                symbol_or_symbols=probe_symbol, timeframe=TimeFrame.Minute,
                start=start, end=end, feed=DataFeed.IEX,
            )
            bars = self.data_client.get_stock_bars(req).df
            result["bars"] = {
                "ok": not bars.empty,
                "detail": f"{len(bars)} barras recibidas para {probe_symbol}" if not bars.empty
                          else f"0 barras (puede ser normal si el mercado está cerrado)",
            }
        except Exception as e:
            result["bars"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}

        return result

    # ------------------------------------------------------------------
    # ALPACA: precios y barras intradía
    # ------------------------------------------------------------------
    def get_bars(self, symbol: str, minutes_back: int = 60, timeframe=TimeFrame.Minute):
        """Devuelve un DataFrame con barras recientes (OHLCV) de Alpaca."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes_back * 2)  # margen extra

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            feed=DataFeed.IEX,   # feed gratuito; evita ambigüedad de entitlement con Alpaca
        )
        try:
            bars = self.data_client.get_stock_bars(request)
            df = bars.df
            if df.empty:
                return pd.DataFrame()
            # Si viene multi-index (symbol, timestamp), aplanar
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(symbol, level=0)
            return df.tail(minutes_back)
        except Exception as e:
            logger.warning(f"[{symbol}] Error obteniendo barras de Alpaca: {e}")
            return pd.DataFrame()

    def get_latest_price(self, symbol: str):
        """
        Cadena de redundancia para el precio:
          1. WebSocket en tiempo real (si está conectado y el dato es reciente)
          2. REST de Alpaca (latest quote) - lo de siempre
          3. Último dato del WebSocket aunque esté "viejo" (stale) - último recurso
        """
        # --- 1) WebSocket (fuente principal, si está activo) ---
        if self.realtime_feed is not None:
            cached = self.realtime_feed.get_latest(symbol, max_age_seconds=15)
            if cached and cached.get("price") is not None and not cached["stale"]:
                return cached["price"]

        # --- 2) REST de Alpaca (respaldo 1) ---
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            response = self.data_client.get_stock_latest_quote(req)
            if symbol not in response:
                logger.warning(
                    f"[{symbol}] Sin cotización en el feed IEX (probable ticker OTC o no cubierto "
                    f"por este feed gratuito, no es un error de credenciales)."
                )
                raise KeyError(symbol)
            quote = response[symbol]
            # Usamos el punto medio entre bid/ask como precio de referencia
            if quote.bid_price and quote.ask_price:
                return (quote.bid_price + quote.ask_price) / 2
            return quote.ask_price or quote.bid_price
        except KeyError:
            pass  # ya se logueó arriba con un mensaje claro
        except Exception as e:
            logger.warning(f"[{symbol}] Error obteniendo cotización REST: {e}")

        # --- 3) Caché vieja del WebSocket (respaldo 2, último recurso) ---
        if self.realtime_feed is not None:
            cached = self.realtime_feed.get_latest(symbol, max_age_seconds=15)
            if cached and cached.get("price") is not None:
                logger.info(
                    f"[{symbol}] REST falló, usando último precio del WebSocket "
                    f"(tiene {cached['age_seconds']}s de antigüedad)"
                )
                return cached["price"]

        return None

    def get_quote_spread_pct(self, symbol: str):
        """
        Spread bid-ask como % del precio medio — usado para el filtro de
        liquidez en sesión extendida (SESSION_RISK_PROFILES.max_spread_pct
        en config.py). En premarket/after-hours, con pocos market makers
        activos, este spread puede ser varias veces más ancho que en
        horario regular; una orden a mercado ahí puede ejecutarse muy
        lejos del precio que se ve en pantalla.

        Devuelve None si no hay cotización disponible (no se asume un
        spread "seguro" por default — mejor bloquear la entrada que
        adivinar liquidez que no se pudo confirmar).
        """
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
            response = self.data_client.get_stock_latest_quote(req)
            if symbol not in response:
                return None
            quote = response[symbol]
            bid, ask = quote.bid_price, quote.ask_price
            if not bid or not ask or bid <= 0 or ask <= 0:
                return None
            mid = (bid + ask) / 2
            return round(((ask - bid) / mid) * 100, 3)
        except Exception as e:
            logger.info(f"[{symbol}] No se pudo calcular el spread: {e}")
            return None

    def get_previous_regular_close(self, symbol: str):
        """
        Cierre OFICIAL de la última sesión regular completa — corrige un bug
        real (confirmado con WHLR: daba un gap de 1978%) donde se usaba
        "la primera vela disponible en la ventana descargada" como si fuera
        el cierre anterior. Con tickers de datos escasos (halts, baja
        liquidez, post-reverse-split) esa primera vela puede ser de varios
        días atrás a un precio totalmente distinto.

        Se usan barras DIARIAS (no de 1 minuto) específicamente por esto:
        son robustas a huecos en el intradía y dan directamente el cierre
        de cada sesión, sin tener que reconstruir a mano dónde terminó el
        día anterior a partir de barras de 1 minuto potencialmente incompletas.
        """
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=10)  # margen amplio: fines de semana + feriados + baja liquidez
            req = StockBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=start, end=end, feed=DataFeed.IEX,
            )
            daily = self.data_client.get_stock_bars(req).df
            if daily.empty:
                return None
            if isinstance(daily.index, pd.MultiIndex):
                daily = daily.loc[symbol]
            daily = daily.sort_index()  # por si acaso; el resto del método depende del orden

            # BUG CORREGIDO: la versión anterior convertía el índice a hora de
            # Nueva York antes de comparar fechas. Una barra diaria de Alpaca
            # marcada a medianoche UTC puede "correrse" un día hacia atrás al
            # convertir a NY (UTC-4/5) — eso hacía que la barra de HOY se
            # confundiera con la de AYER, dando gap 0.00% en símbolo tras
            # símbolo. Ahora se compara la fecha en UTC (tal cual la entrega
            # Alpaca), sin conversión de huso horario: una barra diaria
            # representa un día de sesión completo, no un instante preciso,
            # así que comparar en UTC es más seguro que reconvertir zonas.
            today_utc = datetime.now(timezone.utc).date()
            fechas_utc = daily.index.date
            barras_previas = daily[fechas_utc < today_utc]
            if barras_previas.empty:
                return None
            return float(barras_previas["close"].iloc[-1])
        except Exception as e:
            logger.info(f"[{symbol}] No se pudo obtener el cierre de la sesión regular anterior: {e}")
            return None

    def get_premarket_change_pct(self, symbol: str):
        """
        Calcula el % de cambio actual vs. el cierre OFICIAL de la última
        sesión regular (ver get_previous_regular_close). Sirve tanto en
        pre-market como durante la sesión regular.
        """
        bars = self.get_bars(symbol, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)
        if bars.empty or "close" not in bars.columns:
            return None

        current_price = bars["close"].iloc[-1]

        prev_close = self.get_previous_regular_close(symbol)
        if prev_close is None or prev_close == 0:
            return None

        return round(((current_price - prev_close) / prev_close) * 100, 2)

    def get_relative_volume(self, symbol: str, avg_daily_volume: float = None):
        """
        RVOL = volumen acumulado hoy / volumen promedio de los últimos 10
        días hábiles. Ya NO depende de Yahoo Finance para el promedio:

          1. Alpaca (barras DIARIAS, últimos 20 días naturales -> últimos
             10 días hábiles) - fuente principal, sin límite de cuota extra
             ya que usa el mismo cliente de datos que el resto del bot.
          2. Twelve Data (respaldo) - si Alpaca no devuelve datos.

        `avg_daily_volume` se acepta por compatibilidad hacia atrás pero
        ya no se usa como fuente (antes venía de Yahoo).
        """
        bars = self.get_bars(symbol, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)
        if bars.empty or "volume" not in bars.columns:
            return None
        today_volume = float(bars["volume"].sum())

        avg_volume_10d = self._get_avg_daily_volume_alpaca(symbol)
        if avg_volume_10d is None:
            avg_volume_10d = self._get_avg_daily_volume_twelvedata(symbol)

        if not avg_volume_10d or avg_volume_10d <= 0:
            return None

        # Normalizamos el promedio diario a la fracción de sesión transcurrida
        minutes_elapsed = min(len(bars), 390)  # sesión regular = 390 min
        expected_volume_by_now = avg_volume_10d * (minutes_elapsed / 390)
        if expected_volume_by_now <= 0:
            return None

        return round(today_volume / expected_volume_by_now, 2)

    def _get_avg_daily_volume_alpaca(self, symbol: str):
        """Fuente principal del RVOL: barras diarias de Alpaca (últimos ~10 días hábiles)."""
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=20)  # 20 días naturales -> ~10-14 hábiles
            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=DataFeed.IEX,
            )
            bars = self.data_client.get_stock_bars(request).df
            if bars.empty:
                return None
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.xs(symbol, level=0)

            # Excluir la barra de HOY si está incompleta (sesión en curso)
            today = datetime.now(timezone.utc).date()
            bars = bars[bars.index.date < today]

            last_10 = bars.tail(10)
            if last_10.empty:
                return None
            return float(last_10["volume"].mean())
        except Exception as e:
            logger.warning(f"[{symbol}] RVOL vía Alpaca (barras diarias) falló: {e}")
            return None

    def _get_avg_daily_volume_twelvedata(self, symbol: str):
        """Respaldo del RVOL: Twelve Data, si Alpaca no devolvió nada."""
        if not config.TWELVE_DATA_API_KEY:
            return None
        try:
            url = (
                f"https://api.twelvedata.com/time_series?symbol={symbol}"
                f"&interval=1day&outputsize=10&apikey={config.TWELVE_DATA_API_KEY}"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            values = data.get("values")
            if not values:
                return None
            volumes = [float(v["volume"]) for v in values if v.get("volume")]
            if not volumes:
                return None
            return sum(volumes) / len(volumes)
        except Exception as e:
            logger.warning(f"[{symbol}] RVOL vía Twelve Data falló: {e}")
            return None

    # ------------------------------------------------------------------
    # CACHÉ LOCAL DE FLOAT (evita gastar cuota de FMP/Alpha Vantage two veces
    # el mismo día, y sirve como respaldo si TODAS las APIs fallan)
    # ------------------------------------------------------------------
    def _read_float_cache(self, symbol: str):
        path = config.FLOAT_CACHE_FILE
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", newline="") as f:
                for row in csv.DictReader(f):
                    if row["symbol"] == symbol:
                        cached_at = datetime.fromisoformat(row["timestamp"])
                        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
                        if age_hours <= config.FLOAT_CACHE_TTL_HOURS:
                            return float(row["float_shares"])
        except Exception as e:
            logger.warning(f"Error leyendo caché de float: {e}")
        return None

    def _write_float_cache(self, symbol: str, float_shares: float):
        path = config.FLOAT_CACHE_FILE
        file_exists = os.path.exists(path)
        try:
            with open(path, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["symbol", "float_shares", "timestamp"])
                writer.writerow([symbol, float_shares, datetime.now(timezone.utc).isoformat()])
        except Exception as e:
            logger.warning(f"Error escribiendo caché de float: {e}")

    def _get_fmp_shares_data(self, symbol: str) -> dict:
        """
        Fuente primaria: Financial Modeling Prep - float real, 250 peticiones/día
        gratis (aunque en la práctica su plan gratis solo cubre acciones grandes/
        medianas, no siempre micro-caps u OTC — ver Finviz como respaldo abajo).
        También trae `outstandingShares` en la misma respuesta, así que
        aprovechamos esa llamada para shares_outstanding sin gastar una
        petición extra.
        """
        if not config.FMP_API_KEY or self._fmp_disabled:
            return {}
        try:
            url = f"https://financialmodelingprep.com/stable/shares-float?symbol={symbol}&apikey={config.FMP_API_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                if resp.status_code in (402, 429):
                    # Plan gratuito sin acceso a este ticker/endpoint, o límite
                    # de tasa alcanzado - se desactiva el resto de la sesión.
                    self._fmp_disabled = True
                logger.info(f"[{symbol}] FMP devolvió status {resp.status_code} (se sigue con el siguiente respaldo).")
                return {}
            data = resp.json()
            if isinstance(data, list) and data:
                row = data[0]
                if not row.get("floatShares") and not row.get("outstandingShares"):
                    logger.info(f"[{symbol}] FMP respondió 200 pero sin datos de float/shares para este ticker.")
                return {
                    "float_shares": row.get("floatShares"),
                    "shares_outstanding": row.get("outstandingShares"),
                }
            logger.info(f"[{symbol}] FMP no devolvió ningún registro para este ticker.")
        except Exception as e:
            logger.info(f"[{symbol}] FMP falló: {e}")
        return {}

    def _get_finviz_shares_data(self, symbol: str) -> dict:
        """
        Respaldo especializado en micro-caps/OTC: Finviz publica Float, Shares
        Outstanding y Market Cap directamente en la ficha de cada ticker
        (finviz.com/quote.ashx?t=SYMBOL), con mucha mejor cobertura de
        penny stocks/small caps que el plan gratuito de FMP. No es una API
        oficial (es la misma librería `finvizfinance` que ya usa
        market_scanner.py como respaldo del escáner), así que se trata con
        cuidado: sin ráfagas agresivas, y con enfriamiento si empieza a fallar
        seguido (mismo patrón que ya usamos para Yahoo).
        """
        if time.time() < self._finviz_disabled_until:
            return {}
        try:
            from finvizfinance.quote import finvizfinance as FinvizQuote
            from finvizfinance.util import number_convert

            time.sleep(0.3)  # pequeña pausa, buen ciudadano con el sitio
            stock = FinvizQuote(symbol)
            if not stock.flag:
                logger.info(f"[{symbol}] Finviz no encontró este ticker.")
                return {}

            fundament = stock.ticker_fundament()
            result = {}
            float_val = number_convert(fundament.get("Shs Float", ""))
            if float_val:
                result["float_shares"] = float_val
            so_val = number_convert(fundament.get("Shs Outstand", ""))
            if so_val:
                result["shares_outstanding"] = so_val
            mc_val = number_convert(fundament.get("Market Cap", ""))
            if mc_val:
                result["market_cap"] = mc_val
            if not result:
                logger.info(f"[{symbol}] Finviz respondió pero sin Float/Shares/Market Cap legibles.")
            return result
        except Exception as e:
            logger.info(f"[{symbol}] Finviz (float) falló: {e}")
            msg = str(e).lower()
            if "429" in msg or "too many requests" in msg or "blocked" in msg:
                self._finviz_disabled_until = time.time() + 60
                logger.warning("Finviz parece estar limitando peticiones — se pausa esta fuente 60s.")
            return {}

    def _get_alphavantage_shares_data(self, symbol: str) -> dict:
        """
        Fuente secundaria: Alpha Vantage - SOLO 25 peticiones/día gratis.

        IMPORTANTE (bug corregido): Alpha Vantage NO da el float real, sólo
        SharesOutstanding (acciones totales). Antes se usaba ese valor como
        aproximación de "float_shares", lo cual hacía que el Float Turnover
        Ratio del Motor Smart diera SIEMPRE 1.0 (float = total de acciones)
        cuando esta fuente se activaba — inflando artificialmente ese
        componente del score justo cuando menos datos reales había. Ahora
        sólo se usa para `shares_outstanding` y `market_cap`, nunca para float.
        """
        if not config.ALPHAVANTAGE_API_KEY:
            return {}
        try:
            url = (
                f"https://www.alphavantage.co/query?function=OVERVIEW"
                f"&symbol={symbol}&apikey={config.ALPHAVANTAGE_API_KEY}"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            result = {}
            shares_outstanding = data.get("SharesOutstanding")
            if shares_outstanding and shares_outstanding not in ("None", ""):
                result["shares_outstanding"] = float(shares_outstanding)
            market_cap = data.get("MarketCapitalization")
            if market_cap and market_cap not in ("None", ""):
                result["market_cap"] = float(market_cap)
            return result
        except Exception as e:
            logger.warning(f"[{symbol}] Alpha Vantage falló: {e}")
        return {}

    # ------------------------------------------------------------------
    # YAHOO FINANCE: fundamentales (float, dilución, volumen promedio)
    # ------------------------------------------------------------------
    def get_fundamentals(self, symbol: str, use_cache: bool = True):
        """
        Devuelve un diccionario con: float_shares, shares_outstanding,
        market_cap, avg_volume_10d, avg_volume_3m.

        Cadena de redundancia (Yahoo queda como ÚLTIMO recurso real, no
        se llama si las demás fuentes ya resolvieron todo lo necesario
        — esto es lo que evita el bloqueo 429 masivo de Yahoo):
          1. Caché local del día (float_cache.csv) para el float.
          2. Financial Modeling Prep: float_shares + shares_outstanding
             (mejor para acciones medianas/grandes).
          3. Finviz: float_shares + shares_outstanding + market_cap
             (mejor cobertura específicamente para micro-caps/OTC).
          4. Alpha Vantage: shares_outstanding + market_cap (nunca float).
          5. Yahoo Finance: SOLO si después de 1-4 todavía falta algún campo.
        Si Yahoo devuelve un error de límite de tasa (429), se pausa 60
        segundos (no toda la sesión) antes de volver a intentarse.
        """
        if use_cache and symbol in self._fundamentals_cache:
            return self._fundamentals_cache[symbol]

        float_shares = self._read_float_cache(symbol)
        float_source = "cache" if float_shares else None
        shares_outstanding = None
        market_cap = None

        fmp_data = self._get_fmp_shares_data(symbol)
        if float_shares is None and fmp_data.get("float_shares"):
            float_shares = fmp_data["float_shares"]
            float_source = "fmp"
        if fmp_data.get("shares_outstanding"):
            shares_outstanding = fmp_data["shares_outstanding"]

        if float_shares is None or shares_outstanding is None or market_cap is None:
            finviz_data = self._get_finviz_shares_data(symbol)
            if float_shares is None and finviz_data.get("float_shares"):
                float_shares = finviz_data["float_shares"]
                float_source = "finviz"
            if shares_outstanding is None and finviz_data.get("shares_outstanding"):
                shares_outstanding = finviz_data["shares_outstanding"]
            if market_cap is None and finviz_data.get("market_cap"):
                market_cap = finviz_data["market_cap"]

        needs_alphavantage = shares_outstanding is None or market_cap is None
        if needs_alphavantage:
            av_data = self._get_alphavantage_shares_data(symbol)
            if shares_outstanding is None:
                shares_outstanding = av_data.get("shares_outstanding")
            if market_cap is None:
                market_cap = av_data.get("market_cap")

        info = {}
        still_missing = float_shares is None or shares_outstanding is None or market_cap is None
        yahoo_cooling_down = time.time() < self._yahoo_disabled_until

        if still_missing and not yahoo_cooling_down:
            time.sleep(0.6)  # pequeña pausa preventiva: reduce la chance de disparar el 429 desde el 1er ticker
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info or {}
            except Exception as e:
                msg = str(e)
                logger.warning(f"[{symbol}] Error obteniendo fundamentales de Yahoo Finance: {e}")
                if "429" in msg or "Expecting value" in msg or "Too Many Requests" in msg:
                    self._yahoo_disabled_until = time.time() + 60  # enfriamiento de 60s, no toda la sesión
                    logger.warning(
                        "Yahoo Finance devolvió límite de tasa (429) — se pausa como fuente "
                        "durante 60s (no toda la sesión) para no seguir golpeándolo símbolo por "
                        "símbolo. Configura FMP_API_KEY/ALPHAVANTAGE_API_KEY para depender mucho "
                        "menos de Yahoo en primer lugar."
                    )
                info = {}
        elif still_missing and yahoo_cooling_down:
            logger.debug(f"[{symbol}] Yahoo en enfriamiento tras 429 reciente; se omite la llamada.")

        if float_shares is None:
            float_shares = info.get("floatShares")
            float_source = "yahoo" if float_shares else None
        if shares_outstanding is None:
            shares_outstanding = info.get("sharesOutstanding")
        if market_cap is None:
            market_cap = info.get("marketCap")

        if float_shares:
            self._write_float_cache(symbol, float_shares)
            logger.info(f"[{symbol}] Float obtenido de: {float_source}")

        data = {
            "float_shares": float_shares,
            "shares_outstanding": shares_outstanding,
            "market_cap": market_cap,
            "avg_volume_10d": info.get("averageVolume10days"),
            "avg_volume_3m": info.get("averageVolume"),
            "short_pct_float": info.get("shortPercentOfFloat"),
            "name": info.get("shortName", symbol),
        }
        self._fundamentals_cache[symbol] = data
        return data

    # ------------------------------------------------------------------
    # SENTIMIENTO DE NOTICIAS: Finnhub (principal) -> Alpha Vantage (respaldo)
    # ------------------------------------------------------------------
    def get_company_country(self, symbol: str):
        """
        Devuelve el país de la sede de la empresa (para la penalización
        geográfica) usando el perfil de FMP. Best-effort: si falla o no
        hay clave configurada, devuelve None (sin penalización, nunca
        bloquea el bot por falta de este dato).
        """
        if not config.FMP_API_KEY or self._fmp_disabled:
            return None
        try:
            url = f"https://financialmodelingprep.com/stable/profile?symbol={symbol}&apikey={config.FMP_API_KEY}"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0].get("country")
        except Exception as e:
            logger.debug(f"[{symbol}] País de la empresa no disponible (silenciado): {e}")
        return None

    def get_news_sentiment(self, symbol: str):
        """
        Devuelve dict: {"score": float entre -1 y 1, "headlines": [...], "source": str}
        score > 0.15 se considera positivo, < -0.15 negativo, entre medio neutral.
        Si ambas fuentes fallan, devuelve score=0.0 (neutral) para no bloquear
        la ejecución del bot.
        """
        # --- Finnhub: noticias recientes + heurística simple de palabras clave ---
        if config.FINNHUB_API_KEY:
            try:
                end = datetime.now(timezone.utc).date()
                start = end - timedelta(days=3)
                url = (
                    f"https://finnhub.io/api/v1/company-news?symbol={symbol}"
                    f"&from={start}&to={end}&token={config.FINNHUB_API_KEY}"
                )
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                news = resp.json() or []
                headlines = [n.get("headline", "") for n in news[:5]]
                if headlines:
                    positive_words = ["surge", "soars", "beats", "fda approval", "approval",
                                       "contract", "strategic contract", "partnership", "record",
                                       "upgrade", "breakthrough", "patent granted", "patent",
                                       "outperform", "earnings beat"]
                    negative_words = ["dilution", "shareholder dilution", "offering",
                                       "public offering", "letter of intent", "loi", "mou",
                                       "lawsuit", "recall", "downgrade", "delist", "bankruptcy",
                                       "investigation", "halt", "shelf registration"]
                    text = " ".join(headlines).lower()
                    pos = sum(text.count(w) for w in positive_words)
                    neg = sum(text.count(w) for w in negative_words)
                    total = pos + neg
                    score = 0.0 if total == 0 else (pos - neg) / total
                    return {"score": round(score, 2), "headlines": headlines, "source": "finnhub"}
            except Exception as e:
                logger.warning(f"[{symbol}] Finnhub news falló: {e}")

        # --- Alpha Vantage News Sentiment (respaldo, cuidado con el límite de 25/día) ---
        if config.ALPHAVANTAGE_API_KEY:
            try:
                url = (
                    f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT"
                    f"&tickers={symbol}&apikey={config.ALPHAVANTAGE_API_KEY}"
                )
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                feed = data.get("feed", [])
                if feed:
                    scores = [float(item.get("overall_sentiment_score", 0)) for item in feed[:5]]
                    headlines = [item.get("title", "") for item in feed[:5]]
                    avg_score = sum(scores) / len(scores) if scores else 0.0
                    return {"score": round(avg_score, 2), "headlines": headlines, "source": "alphavantage"}
            except Exception as e:
                logger.warning(f"[{symbol}] Alpha Vantage News Sentiment falló: {e}")

        # --- Ninguna fuente disponible: neutral por defecto, no bloquea el bot ---
        return {"score": 0.0, "headlines": [], "source": "neutral_default"}

    def get_news_headlines(self, symbol: str, limit: int = 3):
        """Titulares recientes de Yahoo Finance, útil como proxy de 'catalizador'."""
        try:
            ticker = yf.Ticker(symbol)
            news = ticker.news or []
            return [n.get("title", "") for n in news[:limit]]
        except Exception as e:
            logger.warning(f"[{symbol}] Error obteniendo noticias: {e}")
            return []

    # ------------------------------------------------------------------
    # TWELVE DATA: volumen premarket (respaldo cuando Alpaca/IEX da 0)
    # ------------------------------------------------------------------
    @staticmethod
    def _last_trading_day_ny() -> "datetime.date":
        """
        Último día hábil (lunes-viernes) en hora de Nueva York. NO tiene en
        cuenta feriados de mercado (Acción de Gracias, etc.) — es una
        aproximación simple; si se necesita exactitud total ahí, se podría
        cruzar con el calendario de mercado de Alpaca (get_calendar()).
        """
        from strategy import NY_TZ
        today_ny = datetime.now(NY_TZ).date()
        # weekday(): lunes=0 ... domingo=6
        if today_ny.weekday() == 5:      # sábado -> viernes
            return today_ny - timedelta(days=1)
        if today_ny.weekday() == 6:      # domingo -> viernes
            return today_ny - timedelta(days=2)
        return today_ny

    def _get_session_volume_twelvedata(self, symbol: str, start_hms: str, end_hms: str, session_label: str):
        """
        Helper compartido: suma el volumen de Twelve Data entre `start_hms`
        y `end_hms` (hora NY) del último día hábil. Usado tanto por
        premarket (4:00-9:30) como por after-hours (16:00-20:00) — misma
        API, mismo límite de tasa, mismo circuit breaker; solo cambia la
        ventana horaria y la etiqueta para los logs.
        """
        if not config.TWELVE_DATA_API_KEY:
            return None
        if time.time() < self._twelvedata_disabled_until:
            return None

        try:
            day = self._last_trading_day_ny()
            start = f"{day} {start_hms}"
            end = f"{day} {end_hms}"
            url = (
                "https://api.twelvedata.com/time_series"
                f"?symbol={symbol}&interval=1min&start_date={start}&end_date={end}"
                f"&timezone=America/New_York&prepost=true&apikey={config.TWELVE_DATA_API_KEY}"
            )
            # NOTA: la documentación oficial de Twelve Data dice que el dato
            # de pre/post-market requiere plan Pro o superior (ver
            # https://support.twelvedata.com/en/articles/5195429). Si tu
            # cuenta es Free/Basic, esta llamada puede seguir sin traer
            # nada para after-hours específicamente, incluso con el
            # parámetro — eso sería una limitación real del plan, no un
            # bug de este código.
            time.sleep(0.3)  # buen ciudadano: no ráfaga contra el límite por minuto
            resp = requests.get(url, timeout=10)
            data = resp.json()

            if isinstance(data, dict) and data.get("status") == "error":
                msg = data.get("message", "")
                msg_lower = msg.lower()
                logger.info(f"[{symbol}] Twelve Data ({session_label}): {msg}")
                if "run out of api credits" in msg_lower or "limit" in msg_lower:
                    self._twelvedata_disabled_until = time.time() + 60
                    logger.warning("Twelve Data alcanzó su límite de peticiones — se pausa 60s.")
                return None

            values = data.get("values", [])
            if not values:
                logger.info(
                    f"[{symbol}] Twelve Data no devolvió velas de {session_label} para {day} "
                    f"(feriado/sin actividad todavía, o la sesión aún no ocurre hoy)."
                )
                return None

            total_volume = sum(float(v.get("volume", 0) or 0) for v in values)
            logger.info(f"[{symbol}] Volumen de {session_label} obtenido de Twelve Data: {total_volume:,.0f}")
            return total_volume

        except Exception as e:
            logger.info(f"[{symbol}] Twelve Data ({session_label}) falló: {e}")
            return None

    def get_premarket_volume_twelvedata(self, symbol: str):
        """
        Respaldo de volumen premarket (4:00-9:30 AM hora NY) usando Twelve
        Data, para cuando Alpaca/IEX no tiene datos de esa ventana (IEX no
        opera en premarket, así que esto pasa SIEMPRE con el feed gratuito
        de Alpaca, no solo a veces).
        """
        return self._get_session_volume_twelvedata(symbol, "04:00:00", "09:30:00", "premarket")

    def get_afterhours_volume_twelvedata(self, symbol: str):
        """
        Respaldo de volumen after-hours (4:00-8:00 PM hora NY) usando
        Twelve Data — mismo problema estructural que premarket: IEX
        (feed gratuito de Alpaca) tampoco opera en after-hours, así que
        Alpaca da 0/None ahí siempre, no solo a veces.

        Si se llama ANTES de las 4:00pm ET del día en curso, Twelve Data
        simplemente no tendrá velas todavía para esa ventana (la sesión
        no ha ocurrido aún hoy) — se devuelve None con un log claro, no
        es un error.
        """
        return self._get_session_volume_twelvedata(symbol, "16:00:00", "20:00:00", "after-hours")
