"""
config.py
---------
Configuración central del bot de Small Caps Long.

IMPORTANTE DE SEGURIDAD:
Las claves de API NUNCA se escriben aquí directamente. Se leen desde
variables de entorno para que puedas subir este proyecto a GitHub sin
exponer tus credenciales.

Cómo definir las variables de entorno:

  Linux / Mac (terminal):
      export ALPACA_API_KEY="tu_api_key"
      export ALPACA_SECRET_KEY="tu_secret_key"
      export TELEGRAM_BOT_TOKEN="tu_token"       (opcional)
      export TELEGRAM_CHAT_ID="tu_chat_id"       (opcional)

  Windows (PowerShell):
      $env:ALPACA_API_KEY="tu_api_key"
      $env:ALPACA_SECRET_KEY="tu_secret_key"

  Google Colab / Replit / GitHub Codespaces:
      Usa la sección de "Secrets" / "Environment Variables" de la
      plataforma para definir las mismas variables de entorno.

  También puedes crear un archivo local ".env" (NUNCA lo subas a GitHub,
  agrégalo a tu .gitignore) y cargarlo con python-dotenv.
"""

import os

# Intenta cargar un archivo .env local si existe (no falla si no está instalado)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _get_secret(key: str, default: str = ""):
    """
    Busca la variable en este orden:
      1. Streamlit Secrets (st.secrets) - solo si existe un secrets.toml
         (evita el aviso "No secrets found" al correr localmente sin él).
      2. Variable de entorno normal (.env, GitHub Actions Secrets, etc.)
    Así el mismo config.py funciona igual en consola, GitHub Actions y
    Streamlit Cloud, sin duplicar credenciales.
    """
    try:
        from pathlib import Path
        secrets_candidates = [
            Path(".streamlit/secrets.toml"),
            Path.home() / ".streamlit" / "secrets.toml",
        ]
        if any(p.exists() for p in secrets_candidates):
            import streamlit as st
            if key in st.secrets:
                return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


# ---------------------------------------------------------------------------
# CREDENCIALES (Streamlit Secrets o variables de entorno)
# ---------------------------------------------------------------------------
ALPACA_API_KEY = _get_secret("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = _get_secret("ALPACA_SECRET_KEY", "")

# True = Paper Trading (cuenta simulada). SIEMPRE empezar en True.
PAPER_TRADING = _get_secret("ALPACA_PAPER", "True") == "True"

ALPACA_BASE_URL = (
    "https://paper-api.alpaca.markets" if PAPER_TRADING
    else "https://api.alpaca.markets"
)

TELEGRAM_BOT_TOKEN = _get_secret("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _get_secret("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# FUENTES DE FUNDAMENTALES CON REDUNDANCIA (evitar depender solo de Yahoo)
# ---------------------------------------------------------------------------
# Todas son gratis y opcionales. Si no configuras alguna, el sistema
# simplemente la salta y pasa a la siguiente de la cadena.
#
# Orden de prioridad para el FLOAT:
#   1) Caché local del día (float_cache.csv) - instantáneo, sin límite.
#   2) Financial Modeling Prep (FMP) - 250 peticiones/día gratis, da el
#      float real (no una aproximación). Regístrate en financialmodelingprep.com
#   3) Alpha Vantage - SOLO 25 peticiones/día gratis, y da "SharesOutstanding"
#      (acciones totales), no el float real - úsalo como último respaldo
#      automático, no como fuente principal. Regístrate en alphavantage.co
#   4) Yahoo Finance (yfinance) - gratis pero bloquea peticiones seguido
#      desde servidores compartidos; último recurso.
FMP_API_KEY = _get_secret("FMP_API_KEY", "")
ALPHAVANTAGE_API_KEY = _get_secret("ALPHAVANTAGE_API_KEY", "")
FINNHUB_API_KEY = _get_secret("FINNHUB_API_KEY", "")
TWELVE_DATA_API_KEY = _get_secret("TWELVE_DATA_API_KEY", "")

# Massive (antes Polygon.io) — plan gratis: llamadas ilimitadas pero datos
# RETRASADOS 15 minutos. Por eso solo se usa como ÚLTIMO respaldo de precio
# (nunca como fuente primaria) y como fuente extra de Market Cap/Shares.
MASSIVE_API_KEY = _get_secret("MASSIVE_API_KEY", "") or _get_secret("POLYGON_API_KEY", "")
MASSIVE_REST_BASE_URL = "https://api.massive.com"

# Tiingo — plan gratis: SOLO EOD (cierre diario) y fundamentales, sin tiempo
# real (eso requiere el add-on de pago "BOATS"). Se usa como respaldo de
# Previous Close y como otra fuente más de Float/Shares/Market Cap.
TIINGO_API_KEY = _get_secret("TIINGO_API_KEY", "")
TIINGO_REST_BASE_URL = "https://api.tiingo.com"

# ---------------------------------------------------------------------------
# SCANNER AUTOMÁTICO (Top 30 gappers/spikes vía TradingView -> Finviz)
# ---------------------------------------------------------------------------
SCANNER_PRICE_MIN = 0.50
SCANNER_PRICE_MAX = 30.0
SCANNER_MAX_MARKET_CAP = 2_000_000_000   # $2B
SCANNER_MIN_VOLUME = 500_000  # regular: mínimo razonable; Moomoo alineado a 500K
SCANNER_MIN_CHANGE_PCT = 5.0

FLOAT_CACHE_FILE = "float_cache.csv"       # caché local (símbolo, float, fecha)
FLOAT_CACHE_TTL_HOURS = 20                 # el float casi no cambia intradía

# Sentimiento de noticias: bonus/penalización pequeño en el score, nunca
# decide una entrada por sí solo. 0 = desactivado.
SENTIMENT_SCORE_WEIGHT = 0.5


# ---------------------------------------------------------------------------
# UNIVERSO Y ENTRADA MANUAL DE TICKERS
# ---------------------------------------------------------------------------
# Archivo de texto donde puedes pegar tickers sacados de Momo Screener,
# Webull, Trade Ideas, etc. Un ticker por línea. Se combina automáticamente
# con lo que el bot encuentre por su cuenta.
MANUAL_TICKERS_FILE = "manual_tickers.txt"

# Lista base opcional "de respaldo" si no tienes acceso a un escáner de
# mercado completo (plan gratuito). Puedes ampliarla con tus propios tickers
# frecuentes de small caps.
DEFAULT_WATCHLIST = [
    # Vacío a propósito: en Manual solo se usan tickers importados/pegados.
    # (Antes tenía "AAPL" y en modo Manual salía solo Apple si el import fallaba.)
]


# ---------------------------------------------------------------------------
# FILTROS DE LA ESTRATEGIA (Fase Pre-market)
# ---------------------------------------------------------------------------
PRICE_MIN = 0.01
PRICE_MAX = 30.0

GAP_MIN_PCT = 15.0          # variación mínima pre-market / del día (%)
PREMARKET_VOLUME_MIN = 300_000  # PM: menos liquidez estructural; 300K evita perder gaps buenos

FLOAT_MIN_SHARES = 1_000_000      # por debajo de esto: demasiado ilíquido/manipulable, se descarta
FLOAT_MAX_SHARES = 12_000_000     # float máximo aceptado (V7.6.6: más flexible, era 10M)
FLOAT_LOW_BONUS_SHARES = 12_000_000  # por debajo de esto, bonus de score (era 8M)

RVOL_MIN = 3.0               # baseline regular; perfiles por sesión pueden bajar/subir

RSI_PERIOD = 14
RSI_OVERBOUGHT = 80          # por encima de esto, penaliza (riesgo de "backside")
RSI_SWEET_SPOT_LOW = 55
RSI_SWEET_SPOT_HIGH = 75


# ---------------------------------------------------------------------------
# MODO DE EJECUCIÓN: SINGLE-BULLET (fase de aprendizaje en paper trading)
# ---------------------------------------------------------------------------
# "single_bullet": compra una sola vez con un monto FIJO en dólares
#                  (TRADE_AMOUNT_USD), sin importar el stop-loss. Simple y
#                  predecible mientras aprendes/pruebas la estrategia.
# "risk_based":    el modo original, calcula la cantidad según el % de
#                  riesgo de la cuenta y la distancia del stop (más
#                  sofisticado, mejor para cuando ya operes en real).
EXECUTION_MODE = "single_bullet"
TOTAL_BULLETS = 1                  # arquitectura preparada para escalar a 5 más adelante
TRADE_AMOUNT_USD = 1000.0          # monto fijo por operación en modo single-bullet


# ---------------------------------------------------------------------------
# PENALIZACIÓN GEOGRÁFICA (empresas asiáticas/chinas: mayor riesgo de
# manipulación de baja liquidez, según las 4 estrategias analizadas)
# ---------------------------------------------------------------------------
# Desactivada por defecto: el país de la sede es un proxy INDIRECTO de riesgo,
# no una variable objetiva. El riesgo real (liquidez, spread, dilución vía SEC
# Shield, float, volatilidad) ya se mide directamente en otras partes del
# score — penalizar por país además de eso penaliza dos veces el mismo riesgo
# de forma imprecisa. Se deja el país como dato informativo en la tabla
# (fetcher.get_company_country sigue corriendo), solo se quitó el descuento
# de puntos. Reactivable con GEOGRAPHIC_PENALTY_ENABLED = True si se prefiere.
GEOGRAPHIC_PENALTY_ENABLED = False
GEOGRAPHIC_PENALTY_COUNTRIES = ["CN", "HK", "China", "Hong Kong", "Taiwan", "TW"]
GEOGRAPHIC_PENALTY_POINTS = 1.0


# ---------------------------------------------------------------------------
# SEC EDGAR — ANTI-OFFERING SHIELD (dilución inminente)
# ---------------------------------------------------------------------------
# La SEC EXIGE un User-Agent con nombre y correo de contacto real en cada
# petición (si no, bloquea). Cámbialo por el tuyo.
SEC_EDGAR_USER_AGENT = _get_secret("SEC_EDGAR_USER_AGENT", "Titon Trading Bot contacto@ejemplo.com")

SEC_DILUTION_LOOKBACK_DAYS = 90

# Formularios que indican una oferta de acciones YA en curso (precio ya
# acordado con colocadores) -> bloqueo total, no se compra el ticker.
SEC_HARD_BLOCK_FORMS = ["424B4", "424B5"]

# Formularios que indican riesgo de dilución pendiente pero no confirmada
# -> penalización fuerte, no bloqueo total.
SEC_PENALTY_FORMS = ["S-1", "S-1/A", "S-3", "S-3/A"]
SEC_PENALTY_POINTS = 3.0


# ---------------------------------------------------------------------------
# VENTANA HORARIA DE OPERACIÓN (hora de Nueva York)
# ---------------------------------------------------------------------------
# Las 4 estrategias analizadas coinciden: la mejor volatilidad para Gap & Go
# ocurre entre la apertura y la primera hora y media / dos horas. Después
# de la 1:00pm ET el volumen suele apagarse y el riesgo de "chop" sube.
TRADING_WINDOW_START_ET = "09:30"
TRADING_WINDOW_END_ET = "13:00"


# ---------------------------------------------------------------------------
# SCORING
# ---------------------------------------------------------------------------
SCORE_MIN_TO_BUY = 9.0       # a partir de esta calificación (escala 1-10) se
                              # considera señal de COMPRA en largo
TOP_N_CANDIDATOS = 20         # ranking automático; watchlist manual muestra TODOS
# _RANK_LIMIT_OVERRIDE se setea en runtime (0 = todos)



# ---------------------------------------------------------------------------
# GESTIÓN DE RIESGO / EJECUCIÓN
# ---------------------------------------------------------------------------
RISK_PER_TRADE_PCT = 1.0        # % del capital total que se arriesga por operación
MAX_POSITIONS_OPEN = 3
DAILY_MAX_LOSS_PCT = 3.0        # kill-switch: detener el bot si se pierde esto en el día

# Rango de apertura (ORB)
ORB_WINDOW_MINUTES = 5
ENTRY_STRATEGY = "retest"  # "retest" (recomendado, ver README) o "orb" (clásico)

# ---------------------------------------------------------------------------
# MOTOR DE SCORING: clásico (probado en producción) vs. TitonSmartEngine
# (proporcional al tamaño real de la empresa - float en $, market cap band,
# float turnover ratio, RVOL estructural). Ver smart_engine.py.
# ---------------------------------------------------------------------------
SCORING_ENGINE = "classic"  # "classic" o "smart"

# Confirmaciones adicionales de entrada (indicadores extra para largo)
REQUIRE_VWAP_CONFIRMATION = True   # el precio debe estar sobre el VWAP del día
REQUIRE_MACD_CONFIRMATION = True   # el histograma del MACD debe ser positivo (momentum alcista)

# Stop-loss dinámico basado en ATR
ATR_PERIOD = 14

# --- Perfiles de salida (SWING vs SCALPING) ---
# EXIT_MODE decide qué perfil está activo. Se puede cambiar en caliente
# desde la UI (el radio "⚡ Modo de Salida" en streamlit_app.py escribe
# directo sobre esta variable, igual que ya hace SCORING_ENGINE).
#
#   "swing"    -> movimientos grandes, aguanta minutos/horas (el original)
#   "scalping" -> entradas/salidas rápidas, objetivos pequeños, stop ajustado
EXIT_MODE = "scalping"  # el objetivo actual del proyecto es Small Cap Scalping, no swing

EXIT_PROFILES = {
    "swing": {
        "label": "🐢 Swing (aguanta el movimiento grande)",
        "atr_stop_multiplier": 1.5,       # stop = entrada - (ATR * multiplicador)
        "stop_loss_min_pct": 3.0,         # nunca un stop más ajustado que esto
        "stop_loss_max_pct": 7.0,         # nunca arriesgar más que esto aunque el ATR sea mayor
        "take_profit_tiers": [
            {"gain_pct": 15.0, "sell_fraction": 0.50},   # vender 50% en +15%
            {"gain_pct": 25.0, "sell_fraction": 0.25},   # vender 25% en +25%
        ],
        "trailing_stop_pct": 8.0,         # % de retroceso desde el máximo, resto de la posición
        "time_stop_minutes": 45,          # si no toca TP1 en 45 min, se sale (movimiento grande = más paciencia)
    },
    "scalping": {
        "label": "⚡ Scalping (entra y sale rápido)",
        "atr_stop_multiplier": 0.8,
        "stop_loss_min_pct": 1.0,
        "stop_loss_max_pct": 2.5,
        "take_profit_tiers": [
            {"gain_pct": 3.0, "sell_fraction": 0.50},    # vender 50% en +3%
            {"gain_pct": 5.0, "sell_fraction": 0.30},    # vender 30% en +5%
        ],
        "trailing_stop_pct": 1.5,         # remanente sale rápido si el impulso se corta
        "time_stop_minutes": 8,           # scalping: si no toca TP1 en 8 min, el setup ya no es válido
    },
}


# --- Perfiles de RIESGO POR SESIÓN (premarket / regular / after-hours) ---
# A diferencia de EXIT_MODE (que el usuario elige a mano en la UI), la
# sesión activa la determina el reloj — get_current_session() en
# strategy.py decide cuál de estos tres perfiles aplica en cada momento.
#
# La idea central: en sesión extendida (premarket/after-hours) hay MENOS
# liquidez y MÁS ruido en el volumen (pocos participantes, spreads
# anchos), así que para poder "ser más agresivos" en cazar oportunidades
# ahí SIN arriesgar más capital, se compensa exigiendo más confirmación
# (catalizador obligatorio, score más alto) y arriesgando menos por
# operación (tamaño reducido, solo órdenes límite, spread máximo).
SESSION_RISK_PROFILES = {
    "premarket": {
        "label": "🌅 Premarket",
        "allow_auto_entry": True,
        "require_catalyst": True,           # en horario regular es solo bonus; aquí es obligatorio
        "min_score_override": 9.3,          # más exigente que el umbral normal (9.0) del Motor Smart
        "position_size_multiplier": 0.5,    # mitad del tamaño normal, por la liquidez reducida
        "max_spread_pct": 1.5,              # rechazar si el spread bid-ask supera 1.5% del precio
        "force_limit_orders": True,         # nunca orden a mercado en sesión extendida
    },
    "regular": {
        "label": "🔔 Regular",
        "allow_auto_entry": True,
        "require_catalyst": False,          # sigue siendo bonus, no obligatorio (comportamiento original)
        "min_score_override": None,         # usa el umbral normal del motor activo (Clásico/Smart)
        "position_size_multiplier": 1.0,
        "max_spread_pct": None,             # sin filtro de spread en horario regular (liquidez normal)
        "force_limit_orders": False,
    },
    "afterhours": {
        "label": "🌙 After-Hours",
        "allow_auto_entry": True,
        "require_catalyst": True,
        "min_score_override": 9.5,          # el más exigente de los tres: menor liquidez, más riesgo de ruido
        "position_size_multiplier": 0.35,   # el más chico de los tres tamaños
        "max_spread_pct": 2.0,              # after-hours suele tener spreads aún más anchos que premarket
        "force_limit_orders": True,
    },
}


def get_session_risk_profile(session: str = None) -> dict:
    """
    Perfil de riesgo activo según la sesión actual. Si no se pasa
    `session`, se detecta automáticamente con strategy.get_current_session().
    Fuera de sesión válida ("closed"), no se permite ninguna entrada.
    """
    if session is None:
        from strategy import get_current_session
        session = get_current_session()
    return SESSION_RISK_PROFILES.get(
        session, {"label": "⛔ Cerrado", "allow_auto_entry": False}
    )


def get_active_exit_profile() -> dict:
    """Perfil de salida activo según EXIT_MODE — único punto de verdad,
    tanto para execution.py (stops/TP reales) como para la UI (valores
    por defecto del formulario de personalización)."""
    return EXIT_PROFILES.get(EXIT_MODE, EXIT_PROFILES["swing"])


# Alias de compatibilidad hacia atrás (por si algo externo todavía los
# importa directamente) — siempre reflejan el perfil "swing" original,
# NO el perfil activo. Para el valor activo, usar get_active_exit_profile().
ATR_STOP_MULTIPLIER = EXIT_PROFILES["swing"]["atr_stop_multiplier"]
STOP_LOSS_MIN_PCT = EXIT_PROFILES["swing"]["stop_loss_min_pct"]
STOP_LOSS_MAX_PCT = EXIT_PROFILES["swing"]["stop_loss_max_pct"]
TAKE_PROFIT_TIERS = EXIT_PROFILES["swing"]["take_profit_tiers"]
TRAILING_STOP_PCT = EXIT_PROFILES["swing"]["trailing_stop_pct"]

# Ventana de barras usada para gap%/RVOL/RSI fuera de horario de mercado
# (fines de semana, feriados, antes de que abra el pre-market). Suficiente
# para alcanzar la última sesión de trading disponible (hasta ~4 días
# atrás, cubre un fin de semana largo con feriado el viernes o el lunes).
LOOKBACK_MINUTES_FALLBACK = 5760


# Diario de operaciones (para la pestaña de Historial y Estadísticas)
TRADE_JOURNAL_FILE = "trade_journal.csv"

# Horarios (hora de Nueva York, EST/EDT)
PREMARKET_START = "04:00"
MARKET_OPEN = "09:30"
MARKET_CLOSE = "16:00"

# ---------------------------------------------------------------------------
# V7.1 — DATA QUALITY & HALT ENGINE
# ---------------------------------------------------------------------------
# Si data_confidence < este umbral, el screener degrada COMPRA_LARGO → VIGILAR.
DATA_CONFIDENCE_MIN_FOR_BUY = 70

# Halt Engine (Nasdaq Trader RSS, gratis, sin API key)
HALT_ENGINE_ENABLED = True


# ---------------------------------------------------------------------------
# V7.4 — TRES PERFILES DE SCORING POR SESIÓN (Premarket / Regular / After-Hours)
# ---------------------------------------------------------------------------
# Cada sesión de la bolsa US tiene liquidez, volumen y comportamiento distintos.
# El scoring y la señal "LISTO" usan umbrales propios de la sesión activa.
#
# session_mode en UI / runtime:
#   "auto"       — detecta premarket | regular | afterhours | closed con el reloj NY
#   "premarket"  — fuerza reglas premarket (cazar gaps 4:00–9:30)
#   "regular"    — fuerza reglas regular (9:30–16:00), sub-ventana fuerte 9:30–11:00
#   "afterhours" — fuerza reglas AH (16:00–20:00)
#   "off"        — sin filtro horario (usa umbrales "regular" pero no bloquea por hora)
#
SESSION_SCORING_PROFILES = {
    "premarket": {
        "label": "🌅 Premarket (4:00–9:30 ET)",
        "gap_min_pct": 10.0,          # gaps más tempranos; 10% ya es interesante
        "rvol_min": 2.0,              # PM: RVOL clásico moderado; prioriza float turnover
        "score_min_listo": 9.0,       # Quality mínimo para LISTO
        "entry_min_listo": 6.5,       # Entry un poco más flexible (PMH/retest)
        "confidence_min": 65,         # datos extended a veces incompletos en free
        "prefer_float_turnover": True,
        "notes": "Cazar gaps/spikes con menos volumen; tamaño de posición ya reducido en SESSION_RISK.",
    },
    "regular": {
        "label": "🔔 Regular (9:30–16:00 ET)",
        "gap_min_pct": 15.0,
        "rvol_min": 3.0,
        "score_min_listo": 9.0,
        "entry_min_listo": 7.0,
        "confidence_min": 70,
        "prefer_float_turnover": False,
        "strong_window_start": "09:30",
        "strong_window_end": "11:00",
        "notes": "Gap & go clásico; primera hora es la ventana más fuerte.",
    },
    "afterhours": {
        "label": "🌙 After-Hours (16:00–20:00 ET)",
        "gap_min_pct": 8.0,           # movimientos post-close / noticias
        "rvol_min": 1.5,              # volumen AH es estructuralmente bajo
        "score_min_listo": 9.3,       # más exigente: spreads anchos, menos liquidez
        "entry_min_listo": 7.0,
        "confidence_min": 65,
        "prefer_float_turnover": True,
        "notes": "Solo setups muy limpios; size pequeño (SESSION_RISK). Cuidado con spreads.",
    },
}

# Compatibilidad V7.3
SESSION_FILTER_ENABLED_DEFAULT = False
SESSION_STRONG_START = "09:30"
SESSION_STRONG_END = "11:00"
SESSION_PREMARKET_START = "04:00"
ENTRY_SCORE_MIN_FOR_READY = 7.0


def get_session_scoring_profile(session: str = None) -> dict:
    """Umbrales de scoring según sesión. session: premarket|regular|afterhours."""
    if session is None or session == "closed":
        session = "regular"
    if session not in SESSION_SCORING_PROFILES:
        session = "regular"
    return SESSION_SCORING_PROFILES[session]

# ---------------------------------------------------------------------------
# TradeZero (ejecución opcional — NO market data)
# ---------------------------------------------------------------------------
TZ_API_KEY_ID = os.getenv("TZ_API_KEY_ID", "")
TZ_API_SECRET_KEY = os.getenv("TZ_API_SECRET_KEY", "") or os.getenv("TZ_API_SECRET", "")
TZ_ACCOUNT_ID = os.getenv("TZ_ACCOUNT_ID", "")


# ---------------------------------------------------------------------------
# Interactive Brokers TWS — fuente PRIMARIA de market data (solo si TWS está ON)
# Paper: puerto 7497 | Live: 7496 (no usar live por defecto)
# En Streamlit Cloud / sin TWS → failover automático a Alpaca (ver market_data_manager)
# ---------------------------------------------------------------------------
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))  # 7497 = paper TWS
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))
IBKR_ACCOUNT = os.getenv("IBKR_ACCOUNT", "DUR216049")  # paper
IBKR_CONNECT_TIMEOUT = float(os.getenv("IBKR_CONNECT_TIMEOUT", "3"))
IBKR_ENABLED = os.getenv("IBKR_ENABLED", "true").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Screening modes (V7.6.5)
# ---------------------------------------------------------------------------
# Completo: Twelve Data PM/AH, SEC dilution, sentiment (más lento, más datos)
# Rápido: sin Twelve Data ni SEC/news; prioritiza precio/gap/RVOL/float en caché
FAST_SCREENING = False  # se sobreescribe en runtime desde la UI
FAST_SCREENING_WORKERS = 4  # paralelismo en modo rápido


# ---------------------------------------------------------------------------
# VOLUME_FILTERS_V76 — guía operativa (Moomoo + bot)
# ---------------------------------------------------------------------------
# Moomoo Regular (recomendado):
#   Volume today >= 500K
#   Turnover   >= 1M USD
#   Volume Ratio (si existe) >= 3
# Moomoo Premarket:
#   Volume >= 200K–300K (no 500K o se vacía la lista)
#   % Chg >= 8–10%
# Moomoo After-Hours:
#   Volume >= 200K, % Chg >= 5–8%, no exigir RVOL alto
#
# Bot (interno):
#   PREMARKET_VOLUME_MIN = 300_000
#   SCANNER_MIN_VOLUME   = 500_000  (regular)
#   RVOL_MIN profiles: PM 2.0 | REG 3.0 | AH 1.5
#   LISTO exige RVOL de sesión + gap + quality (no solo volumen crudo)
VOLUME_FILTERS_V76 = True


# ---------------------------------------------------------------------------
# MOOMOO OPEND (solo LOCAL — Capa 1: datos; Capa 2: órdenes)
# ---------------------------------------------------------------------------
MOOMOO_OPEND_HOST = _get_secret("MOOMOO_OPEND_HOST", "127.0.0.1")
MOOMOO_OPEND_PORT = int(_get_secret("MOOMOO_OPEND_PORT", "11111") or "11111")
# SIMULATE = paper (por defecto). REAL solo cuando operes capital.
MOOMOO_TRD_ENV = _get_secret("MOOMOO_TRD_ENV", "SIMULATE").upper()  # SIMULATE | REAL
MOOMOO_ENABLED = _get_secret("MOOMOO_ENABLED", "True") == "True"
