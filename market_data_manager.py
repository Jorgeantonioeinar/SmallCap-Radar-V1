"""
market_data_manager.py
----------------------
Fuente primaria: Interactive Brokers TWS (ib_insync) en localhost:7497 (paper).
Failover automático: Alpaca (y el resto del DataFetcher) si TWS está apagado.

Casos de uso:
  - PC local + TWS abierto  → datos IBKR (baja latencia, volumen real).
  - PC sin TWS / Streamlit Cloud / móvil → Alpaca REST sin interrumpir el bot.

La API de TradeZero NO se usa aquí (es solo ejecución, no market data).

Config esperada (config.py / .env):
  IBKR_HOST=127.0.0.1
  IBKR_PORT=7497          # paper TWS (7496 = live — no usar por defecto)
  IBKR_CLIENT_ID=1
  IBKR_ACCOUNT=DUR216049  # paper
  IBKR_CONNECT_TIMEOUT=3
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger("market_data_manager")


def is_local_tws_environment() -> bool:
    """
    IBKR TWS solo si el usuario lo fuerza explícitamente en local.

    En Streamlit Cloud NUNCA debe intentarse 127.0.0.1 (no hay TWS).
    En PC local: poner en .env  IBKR_FORCE=true  y tener TWS paper en 7497.
    """
    force = os.getenv("IBKR_FORCE", "").lower() in ("1", "true", "yes")
    enabled = os.getenv("IBKR_ENABLED", "true").lower() not in ("0", "false", "no")

    # Señales típicas de Streamlit Cloud / contenedor remoto
    cloud_signals = [
        os.getenv("IS_STREAMLIT_CLOUD"),
        os.getenv("STREAMLIT_SHARING_MODE"),
        os.getenv("STREAMLIT_CLOUD"),
        os.path.exists("/mount/src"),      # Streamlit Cloud repo mount
        os.path.exists("/home/appuser"),  # usuario típico Streamlit Cloud
    ]
    if any(cloud_signals):
        return False

    # Sin force explícito no intentamos TWS (evita spam en nube aunque falle la detección)
    if not force:
        return False
    if not enabled:
        return False

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False
    return True



# ---------------------------------------------------------------------------
# Estructura normalizada (misma para IBKR y Alpaca)
# ---------------------------------------------------------------------------
STANDARD_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


def empty_bars_df() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_OHLCV_COLS)


def normalize_bars_df(df: pd.DataFrame) -> pd.DataFrame:
    """Unifica nombres de columnas a open/high/low/close/volume con índice datetime."""
    if df is None or df.empty:
        return empty_bars_df()
    out = df.copy()
    rename = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    for col in STANDARD_OHLCV_COLS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[STANDARD_OHLCV_COLS].copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        try:
            out.index = pd.to_datetime(out.index, utc=True)
        except Exception:
            pass
    return out


@dataclass
class QuoteSnapshot:
    symbol: str
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[float] = None
    source: str = "unknown"
    ts: Optional[datetime] = None


@dataclass
class ProviderStatus:
    name: str
    active: bool
    connected: bool
    detail: str = ""
    account: str = ""


# ---------------------------------------------------------------------------
# Interfaz común
# ---------------------------------------------------------------------------
class MarketDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def connect(self) -> bool:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def get_latest_price(self, symbol: str) -> Optional[float]:
        ...

    @abstractmethod
    def get_bars(
        self, symbol: str, minutes_back: int = 390, bar_size: str = "1 min"
    ) -> pd.DataFrame:
        ...

    def get_quote(self, symbol: str) -> QuoteSnapshot:
        px = self.get_latest_price(symbol)
        return QuoteSnapshot(symbol=symbol, price=px, source=self.name, ts=datetime.utcnow())

    def scan_gainers(
        self, price_min: float = 0.5, price_max: float = 20.0, limit: int = 30
    ) -> list[str]:
        """Opcional: lista de símbolos gappers/gainers. Vacío si no soportado."""
        return []

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            active=False,
            connected=self.is_connected(),
            detail="",
        )


# ---------------------------------------------------------------------------
# Proveedor IBKR (primario)
# ---------------------------------------------------------------------------
class IBKRDataProvider(MarketDataProvider):
    name = "ibkr"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        account: str = "DUR216049",
        connect_timeout: float = 3.0,
    ):
        self.host = host
        self.port = int(port)
        self.client_id = int(client_id)
        self.expected_account = (account or "").strip()
        self.connect_timeout = float(connect_timeout)
        self._ib = None
        self._lock = threading.Lock()
        self._connected = False
        self._active_account = ""

    def connect(self) -> bool:
        try:
            from ib_insync import IB, util
        except ImportError:
            logger.warning(
                "ib_insync no instalado. pip install ib_insync  → IBKR deshabilitado."
            )
            return False

        with self._lock:
            try:
                # Streamlit / threads: arrancar loop si hace falta
                try:
                    util.startLoop()
                except Exception:
                    pass

                ib = IB()
                ib.connect(
                    self.host,
                    self.port,
                    clientId=self.client_id,
                    timeout=self.connect_timeout,
                )
                if not ib.isConnected():
                    logger.info("IBKR: connect() sin sesión activa.")
                    return False

                accounts = list(ib.managedAccounts() or [])
                self._active_account = accounts[0] if accounts else ""

                if self.expected_account and accounts:
                    if self.expected_account not in accounts:
                        logger.warning(
                            f"IBKR: cuenta esperada {self.expected_account} "
                            f"no está en managedAccounts={accounts}. "
                            "Se continúa con la sesión, revisa TWS."
                        )
                    else:
                        logger.info(f"IBKR: cuenta verificada {self.expected_account}")

                self._ib = ib
                self._connected = True
                logger.info(
                    f"IBKR TWS conectado {self.host}:{self.port} "
                    f"clientId={self.client_id} accounts={accounts}"
                )
                return True
            except Exception as e:
                self._ib = None
                self._connected = False
                logger.info(f"IBKR TWS no disponible ({self.host}:{self.port}): {e}")
                return False

    def disconnect(self) -> None:
        with self._lock:
            if self._ib is not None:
                try:
                    self._ib.disconnect()
                except Exception:
                    pass
            self._ib = None
            self._connected = False

    def is_connected(self) -> bool:
        if not self._connected or self._ib is None:
            return False
        try:
            return bool(self._ib.isConnected())
        except Exception:
            return False

    def _stock(self, symbol: str):
        from ib_insync import Stock

        return Stock(symbol.upper().strip(), "SMART", "USD")

    def get_latest_price(self, symbol: str) -> Optional[float]:
        if not self.is_connected():
            return None
        try:
            from ib_insync import Ticker

            contract = self._stock(symbol)
            self._ib.qualifyContracts(contract)
            tickers = self._ib.reqTickers(contract)
            if not tickers:
                return None
            t: Ticker = tickers[0]
            for val in (t.marketPrice(), t.last, t.close, t.bid, t.ask):
                if val is not None and val == val and val > 0:  # not NaN
                    return float(val)
            return None
        except Exception as e:
            logger.debug(f"IBKR price {symbol}: {e}")
            return None

    def get_bars(
        self, symbol: str, minutes_back: int = 390, bar_size: str = "1 min"
    ) -> pd.DataFrame:
        if not self.is_connected():
            return empty_bars_df()
        try:
            from ib_insync import util

            contract = self._stock(symbol)
            self._ib.qualifyContracts(contract)
            # duration string aproximada
            duration = f"{max(minutes_back, 30)} M"
            if minutes_back >= 60 * 24:
                duration = f"{max(minutes_back // (60 * 24), 1)} D"
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=False,  # incluir extended hours (PM/AH)
                formatDate=1,
            )
            if not bars:
                return empty_bars_df()
            df = util.df(bars)
            if df is None or df.empty:
                return empty_bars_df()
            if "date" in df.columns:
                df = df.set_index("date")
            return normalize_bars_df(df)
        except Exception as e:
            logger.debug(f"IBKR bars {symbol}: {e}")
            return empty_bars_df()

    def scan_gainers(
        self, price_min: float = 0.5, price_max: float = 20.0, limit: int = 30
    ) -> list[str]:
        if not self.is_connected():
            return []
        try:
            from ib_insync import ScannerSubscription

            sub = ScannerSubscription(
                instrument="STK",
                locationCode="STK.US.MAJOR",
                scanCode="TOP_PERC_GAIN",
            )
            # Filtros de precio vía scanCode; IB no siempre respeta min/max en todos los scans
            data = self._ib.reqScannerData(sub)
            symbols: list[str] = []
            for row in data or []:
                try:
                    sym = row.contractDetails.contract.symbol
                    if sym and sym not in symbols:
                        symbols.append(sym)
                    if len(symbols) >= limit:
                        break
                except Exception:
                    continue
            logger.info(f"IBKR scanner TOP_PERC_GAIN → {len(symbols)} símbolos")
            return symbols[:limit]
        except Exception as e:
            logger.warning(f"IBKR scanner falló: {e}")
            return []

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            active=False,
            connected=self.is_connected(),
            detail=f"{self.host}:{self.port}",
            account=self._active_account or self.expected_account,
        )


# ---------------------------------------------------------------------------
# Proveedor Alpaca (secundario / failover)
# ---------------------------------------------------------------------------
class AlpacaDataProvider(MarketDataProvider):
    name = "alpaca"

    def __init__(self):
        self._client = None
        self._connected = False

    def connect(self) -> bool:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            import config as cfg

            key = getattr(cfg, "ALPACA_API_KEY", "") or os.getenv("ALPACA_API_KEY", "")
            secret = getattr(cfg, "ALPACA_SECRET_KEY", "") or os.getenv(
                "ALPACA_SECRET_KEY", ""
            )
            if not key or not secret:
                logger.warning("Alpaca keys no configuradas — failover de datos limitado.")
                self._connected = False
                return False
            self._client = StockHistoricalDataClient(key, secret)
            self._StockBarsRequest = StockBarsRequest
            self._StockLatestQuoteRequest = StockLatestQuoteRequest
            self._TimeFrame = TimeFrame
            self._connected = True
            logger.info("Alpaca Data Provider listo (failover / nube).")
            return True
        except Exception as e:
            logger.warning(f"Alpaca provider no pudo inicializar: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        self._client = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    def get_latest_price(self, symbol: str) -> Optional[float]:
        if not self.is_connected():
            return None
        try:
            req = self._StockLatestQuoteRequest(symbol_or_symbols=symbol.upper())
            quotes = self._client.get_stock_latest_quote(req)
            q = quotes.get(symbol.upper()) if isinstance(quotes, dict) else None
            if q is None and quotes:
                q = list(quotes.values())[0]
            if q is None:
                return None
            for attr in ("ask_price", "bid_price", "ask", "bid"):
                val = getattr(q, attr, None)
                if val is not None and float(val) > 0:
                    return float(val)
            return None
        except Exception as e:
            logger.debug(f"Alpaca price {symbol}: {e}")
            return None

    def get_bars(
        self, symbol: str, minutes_back: int = 390, bar_size: str = "1 min"
    ) -> pd.DataFrame:
        if not self.is_connected():
            return empty_bars_df()
        try:
            end = datetime.utcnow()
            start = end - timedelta(minutes=max(minutes_back, 30))
            req = self._StockBarsRequest(
                symbol_or_symbols=symbol.upper(),
                timeframe=self._TimeFrame.Minute,
                start=start,
                end=end,
            )
            bars = self._client.get_stock_bars(req)
            df = bars.df if hasattr(bars, "df") else None
            if df is None or df.empty:
                return empty_bars_df()
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(symbol.upper(), level=0) if symbol.upper() in df.index.get_level_values(0) else df
            return normalize_bars_df(df)
        except Exception as e:
            logger.debug(f"Alpaca bars {symbol}: {e}")
            return empty_bars_df()

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            active=False,
            connected=self.is_connected(),
            detail="REST IEX/SIP según plan Alpaca",
        )


# ---------------------------------------------------------------------------
# Gestor con failover (Factory + Strategy)
# ---------------------------------------------------------------------------
class MarketDataManager:
    """
    Intenta IBKR primero; si falla, Alpaca.
    get_latest_price / get_bars siempre devuelven el mismo formato.
    """

    def __init__(self, prefer_ibkr: bool = True):
        self.prefer_ibkr = prefer_ibkr
        self.primary: Optional[MarketDataProvider] = None
        self.fallback: Optional[MarketDataProvider] = None
        self.active: Optional[MarketDataProvider] = None
        self.active_name: str = "none"
        self._initialized = False

    def initialize(self) -> str:
        """
        Conecta proveedores. Devuelve 'ibkr' | 'alpaca' | 'none'.
        Idempotente: en Streamlit no reintenta TWS en cada rerun.
        """
        if self._initialized:
            return self.active_name

        import config as cfg

        host = getattr(cfg, "IBKR_HOST", None) or os.getenv("IBKR_HOST", "127.0.0.1")
        port = int(getattr(cfg, "IBKR_PORT", None) or os.getenv("IBKR_PORT", "7497"))
        client_id = int(
            getattr(cfg, "IBKR_CLIENT_ID", None) or os.getenv("IBKR_CLIENT_ID", "1")
        )
        account = (
            getattr(cfg, "IBKR_ACCOUNT", None)
            or os.getenv("IBKR_ACCOUNT", "DUR216049")
        )
        timeout = float(
            getattr(cfg, "IBKR_CONNECT_TIMEOUT", None)
            or os.getenv("IBKR_CONNECT_TIMEOUT", "3")
        )

        # 1) Siempre preparar Alpaca (nube + local failover)
        self.fallback = AlpacaDataProvider()
        alpaca_ok = self.fallback.connect()

        # 2) IBKR solo en entorno local con TWS posible
        try_ibkr = self.prefer_ibkr and is_local_tws_environment()
        if not try_ibkr:
            logger.info("Fuente de datos: Alpaca/web (IBKR TWS no activo en este entorno).")
            self.primary = None
            self.active = self.fallback if alpaca_ok else None
            self.active_name = "alpaca" if self.active else "none"
        else:
            self.primary = IBKRDataProvider(
                host=host,
                port=port,
                client_id=client_id,
                account=account,
                connect_timeout=timeout,
            )
            logger.info(
                f"Intentando IBKR TWS {host}:{port} (paper account {account})..."
            )
            if self.primary.connect():
                self.active = self.primary
                self.active_name = "ibkr"
                logger.info("Fuente PRIMARIA activa: IBKR TWS")
            else:
                logger.warning(
                    "TWS inactivo o no alcanzable. Activando respaldo Alpaca..."
                )
                self.active = self.fallback if alpaca_ok else None
                self.active_name = "alpaca" if self.active else "none"

        if self.active_name == "none":
            logger.warning(
                "Ningún proveedor MDM conectado. "
                "DataFetcher usará Finviz/Yahoo/Twelve Data."
            )

        self._initialized = True
        return self.active_name

    def ensure(self) -> str:
        if not self._initialized:
            return self.initialize()
        # En nube nunca reintentar IBKR
        if self.active_name == "ibkr" and self.active and self.active.is_connected():
            return self.active_name
        if self.active_name == "alpaca":
            return self.active_name
        if self.active and self.active.is_connected():
            return self.active_name
        # Solo reintentar IBKR en entorno local (TWS pudo abrirse después)
        if (
            self.prefer_ibkr
            and self.primary is not None
            and is_local_tws_environment()
            and not self.primary.is_connected()
        ):
            if self.primary.connect():
                self.active = self.primary
                self.active_name = "ibkr"
                logger.info("IBKR recuperado — volviendo a fuente primaria.")
                return self.active_name
        if self.fallback and self.fallback.is_connected():
            self.active = self.fallback
            self.active_name = "alpaca"
            return self.active_name
        if self.fallback and self.fallback.connect():
            self.active = self.fallback
            self.active_name = "alpaca"
            return self.active_name
        return self.active_name

    def get_latest_price(self, symbol: str) -> Optional[float]:
        self.ensure()
        if self.active:
            px = self.active.get_latest_price(symbol)
            if px is not None:
                return px
        # cascada: si IBKR falló en un ticker, probar Alpaca
        if self.active_name == "ibkr" and self.fallback and self.fallback.is_connected():
            return self.fallback.get_latest_price(symbol)
        return None

    def get_bars(
        self, symbol: str, minutes_back: int = 390, bar_size: str = "1 min"
    ) -> pd.DataFrame:
        self.ensure()
        if self.active:
            df = self.active.get_bars(symbol, minutes_back=minutes_back, bar_size=bar_size)
            if df is not None and not df.empty:
                return df
        if self.active_name == "ibkr" and self.fallback and self.fallback.is_connected():
            return self.fallback.get_bars(symbol, minutes_back=minutes_back, bar_size=bar_size)
        return empty_bars_df()

    def scan_gainers(
        self, price_min: float = 0.5, price_max: float = 20.0, limit: int = 30
    ) -> list[str]:
        self.ensure()
        if self.active_name == "ibkr" and self.active:
            syms = self.active.scan_gainers(price_min, price_max, limit)
            if syms:
                return syms
        return []

    def get_status(self) -> dict[str, Any]:
        self.ensure()
        return {
            "active": self.active_name,
            "ibkr": self.primary.status().__dict__ if self.primary else None,
            "alpaca": self.fallback.status().__dict__ if self.fallback else None,
        }


# Singleton de proceso (un solo intento de init por worker de Streamlit)
_manager: Optional[MarketDataManager] = None
_manager_lock = threading.Lock()
_init_done = False


def get_market_data_manager(force_reinit: bool = False) -> MarketDataManager:
    global _manager, _init_done
    with _manager_lock:
        if _manager is not None and _init_done and not force_reinit:
            return _manager
        if _manager is None or force_reinit:
            # En nube: prefer_ibkr=False para no tocar TWS nunca
            prefer = is_local_tws_environment()
            _manager = MarketDataManager(prefer_ibkr=prefer)
            _manager.initialize()
            _init_done = True
        return _manager
