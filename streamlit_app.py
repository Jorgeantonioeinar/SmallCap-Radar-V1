"""
streamlit_app.py
-----------------
Dashboard "Titon" — terminal de trading de small caps de nivel
profesional, desplegable gratis en Streamlit Community Cloud.

Estructura:
  Pestaña 1 "📈 Operativa en Vivo":
    - KPIs de cabecera (capital, P&L del día, kill-switch, oportunidades)
    - Selector de modo (Screener automático Top 30 / Manual / Combinado)
    - Tabla de screening con formato profesional (moneda, %, etc.)
    - Gráfico de confirmación (velas + ORB + VWAP + MACD) por ticker
    - Tarjeta de vista previa de orden (calculadora de riesgo) + botón de compra
    - Panel de posiciones abiertas con P&L flotante y cierre de emergencia
    - Consola de eventos en vivo (logs del bot)
  Pestaña 2 "📊 Historial y Estadísticas":
    - Win Rate, Profit Factor, P&L total
    - Curva de equity acumulada
    - Tabla completa del diario de operaciones

LIMITACIÓN IMPORTANTE: Streamlit Community Cloud "duerme" la app tras un
rato de inactividad; el trailing stop y el panel de posiciones solo se
actualizan mientras la pestaña esté abierta. Para automatización 24/7 sin
depender del navegador, usa el workflow de GitHub Actions en paralelo.
"""

import pandas as pd
import numpy as np
import streamlit as st
# --- Entorno: en la nube nunca forzar IBKR ---
import os as _os
_os.environ.setdefault("IBKR_FORCE", "false")
if _os.path.exists("/mount/src") or _os.path.exists("/home/appuser") or _os.environ.get("STREAMLIT_SHARING_MODE"):
    _os.environ["IS_STREAMLIT_CLOUD"] = "1"
    _os.environ["IBKR_FORCE"] = "false"
    _os.environ["IBKR_ENABLED"] = "false"


# set_page_config debe ser el PRIMER comando de Streamlit del script.
st.set_page_config(
    page_title="Titon — Small Caps Trading Bot",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.markdown(
    """
    **Checklist lunes (scalp)**  
    1. Sesión **Auto** o **Premarket** 4:00–9:30  
    2. Busca **Scalp = LISTO** (arriba de la tabla)  
    3. Evita Halt / Dilución Alta / NO-CHASE  
    4. Confirma en el chart antes de comprar  
    """
)


# Marca entorno nube para que MarketDataManager no intente TWS local
import os as _os_cloud
if _os_cloud.getenv("STREAMLIT_SHARING_MODE") or _os_cloud.path.exists("/mount/src") or _os_cloud.path.exists("/home/appuser"):
    _os_cloud.environ["IS_STREAMLIT_CLOUD"] = "1"
    _os_cloud.environ["IBKR_ENABLED"] = "false"


# --- Fuente de datos activa (IBKR vs Alpaca failover) ---
try:
    from market_data_manager import get_market_data_manager
    _mdm = get_market_data_manager()
    _src = _mdm.active_name
    if _src == "ibkr":
        st.sidebar.success("📡 Datos: IBKR TWS (primario)")
    elif _src == "alpaca":
        st.sidebar.info("📡 Datos: Alpaca (failover — TWS off o nube)")
    else:
        st.sidebar.warning("📡 Datos: fuentes web (Finviz/Yahoo) — sin IBKR/Alpaca")
except Exception:
    pass


import config
from data_fetcher import DataFetcher
from screener import get_universe, rank_candidates, load_manual_tickers, add_manual_ticker, replace_manual_watchlist
from market_scanner import get_top30_gappers_spikes
from strategy import check_orb_breakout, check_gap_and_go_retest, get_premarket_high, is_within_trading_window
from execution import PositionManager
from notifier import TelegramNotifier
from charts import build_confirmation_chart
from notifier import alert_high_score, alert_kill_switch
import journal_analytics
import live_log

live_log.attach_once()

st.markdown(
    """
    <style>
    .block-container {padding-left: 2rem; padding-right: 2rem; max-width: 100%;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# RECURSOS COMPARTIDOS (se crean una sola vez por sesión/servidor)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_fetcher():
    return DataFetcher()


@st.cache_resource
def get_notifier():
    return TelegramNotifier()


if "position_manager" not in st.session_state:
    st.session_state.position_manager = PositionManager(
        get_fetcher().trading_client, notifier=get_notifier()
    )
if "ranked" not in st.session_state:
    st.session_state.ranked = []

fetcher = get_fetcher()
pm = st.session_state.position_manager


# ---------------------------------------------------------------------------
# VALIDACIÓN DE CREDENCIALES
# ---------------------------------------------------------------------------
if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
    st.error(
        "No se encontraron las claves de Alpaca. Configúralas en "
        "Streamlit Cloud: **Settings → Secrets** (o en tu .env local)."
    )
    st.stop()


# ---------------------------------------------------------------------------
# BARRA LATERAL: MODO DE OPERACIÓN + ENTRADA MANUAL
# ---------------------------------------------------------------------------
st.sidebar.title("🚀 Titon")

# --- Moomoo OpenD (Capa 1, solo local) ---
if getattr(config, "MOOMOO_ENABLED", True):
    try:
        from moomoo_client import get_moomoo_client
        _mm = get_moomoo_client()
        _mm_st = _mm.status()
        if _mm_st.get("quote_connected") or (_mm_st.get("port_open") and _mm_st.get("package_ok")):
            st.sidebar.success(f"🟠 Moomoo OpenD: {_mm_st['host']}:{_mm_st['port']} OK")
        elif not _mm_st.get("package_ok"):
            st.sidebar.warning("🟠 Moomoo: instala `pip install moomoo-api`")
        elif not _mm_st.get("port_open"):
            st.sidebar.info("🟠 Moomoo OpenD: apagado (abre OpenD en esta PC)")
        else:
            st.sidebar.info(f"🟠 Moomoo: {_mm_st.get('message', '—')}")
    except Exception as _e_mm:
        st.sidebar.caption(f"🟠 Moomoo: {_e_mm}")



st.sidebar.header("⚡ Modo de Salida")
_exit_labels = [p["label"] for p in config.EXIT_PROFILES.values()]
_exit_keys = list(config.EXIT_PROFILES.keys())
_default_idx = _exit_keys.index(config.EXIT_MODE) if config.EXIT_MODE in _exit_keys else 0
_chosen_exit_label = st.sidebar.radio(
    "¿Cómo quieres operar hoy?", _exit_labels, index=_default_idx, key="exit_mode_radio",
)
config.EXIT_MODE = _exit_keys[_exit_labels.index(_chosen_exit_label)]
_active_profile = config.get_active_exit_profile()
st.sidebar.caption(
    f"TP1 +{_active_profile['take_profit_tiers'][0]['gain_pct']}% · "
    f"TP2 +{_active_profile['take_profit_tiers'][1]['gain_pct']}% · "
    f"Trailing {_active_profile['trailing_stop_pct']}% · "
    f"Stop {_active_profile['stop_loss_min_pct']}-{_active_profile['stop_loss_max_pct']}%"
)
st.sidebar.caption(
    "⚠️ Este modo aplica a las **próximas** entradas y al trailing de posiciones "
    "abiertas desde ahora. No modifica retroactivamente stops/TP ya calculados "
    "de posiciones que abriste con el otro modo."
)

from strategy import get_current_session
_current_session = get_current_session()
_session_profile = config.get_session_risk_profile(_current_session)
st.sidebar.header(f"🕐 Sesión: {_session_profile.get('label', _current_session)}")
if _session_profile.get("allow_auto_entry"):
    _parts = [f"Score mín. {_session_profile['min_score_override']}" if _session_profile.get("min_score_override") else "Score: umbral normal"]
    _parts.append(f"Tamaño x{_session_profile.get('position_size_multiplier', 1.0):g}")
    if _session_profile.get("require_catalyst"):
        _parts.append("catalizador obligatorio")
    if _session_profile.get("max_spread_pct"):
        _parts.append(f"spread máx {_session_profile['max_spread_pct']}%")
    st.sidebar.caption(" · ".join(_parts))
else:
    st.sidebar.caption("Mercado cerrado — entradas automáticas deshabilitadas.")

st.sidebar.header("⚙️ Modo de operación")
modo_screening = st.sidebar.radio(
    "¿Cómo quieres armar la lista de candidatos?",
    ["🔍 Screener Automático (Top 30 Gappers/Spikes)", "📝 Manual / Personalizado"],
    index=0,
)
combinar_ambos = False
if modo_screening.startswith("🔍"):
    combinar_ambos = st.sidebar.checkbox("➕ Combinar con mi watchlist manual también", value=False)
    st.sidebar.caption(
        "Consulta TradingView en vivo (cae a Finviz si falla) para traer "
        "las small caps con más gap%, volumen y momentum del momento."
    )

st.sidebar.header("➕ Entrada manual de tickers")
manual_input = st.sidebar.text_area(
    "Un ticker por línea. Float/RVOL opcionales separados por coma:\nIMRN\nCDTG,2500000\nISPC,1800000,5.2",
    height=100,
)
if st.sidebar.button("Agregar a la watchlist"):
    added = []
    for line in manual_input.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        symbol = parts[0].upper()
        float_override, rvol_override = None, None
        try:
            if len(parts) >= 2 and parts[1]:
                float_override = float(parts[1])
            if len(parts) >= 3 and parts[2]:
                rvol_override = float(parts[2])
        except ValueError:
            st.sidebar.warning(f"Formato inválido en '{line}', se agregó solo el ticker.")
        add_manual_ticker(symbol, float_override, rvol_override)
        added.append(symbol)
    if added:
        st.sidebar.success(f"Agregado(s): {', '.join(added)}")

st.sidebar.subheader("Watchlist manual actual")
current_manual = load_manual_tickers()
if st.session_state.get("manual_watchlist"):
    # prioriza la última importación en esta sesión (nube)
    _ss = st.session_state["manual_watchlist"]
    current_manual = [{"symbol": s, "float_override": None, "rvol_override": None} for s in _ss]

if current_manual:
    for e in current_manual:
        extra = ""
        if e["float_override"] is not None:
            extra += f" · float: {e['float_override']:,.0f}"
        if e["rvol_override"] is not None:
            extra += f" · RVOL: {e['rvol_override']}"
        st.sidebar.write(f"**{e['symbol']}**{extra}")
else:
    st.sidebar.write("_(vacía por ahora)_")


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def get_account_info():
    try:
        account = fetcher.trading_client.get_account()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        buying_power = float(account.buying_power)
        daily_pnl = equity - last_equity
        daily_pnl_pct = (daily_pnl / last_equity * 100) if last_equity else 0.0
        return {
            "equity": equity, "buying_power": buying_power,
            "daily_pnl": daily_pnl, "daily_pnl_pct": daily_pnl_pct,
        }
    except Exception as e:
        st.warning(f"No se pudo leer la cuenta de Alpaca: {e}")
        return None


def render_order_customization_form(key_prefix, default_price, default_qty, default_stop_price):
    """
    Formulario reutilizable para personalizar una orden antes de enviarla:
    cantidad de acciones, stop-loss, TP1/TP2, trailing stop, y tipo de
    orden (mercado o límite). Todo editable — si el usuario no toca nada,
    quedan los valores del perfil de salida ACTIVO (config.EXIT_MODE:
    swing o scalping), no valores fijos.
    """
    profile = config.get_active_exit_profile()
    tp_tiers_default = profile["take_profit_tiers"]
    tp1_default = tp_tiers_default[0]["gain_pct"]
    tp2_default = tp_tiers_default[1]["gain_pct"] if len(tp_tiers_default) > 1 else tp1_default * 1.5
    trailing_default = profile["trailing_stop_pct"]

    with st.expander(f"⚙️ Personalizar orden (perfil activo: {profile['label']})", expanded=False):
        c1, c2 = st.columns(2)
        qty = c1.number_input(
            "Cantidad de acciones", min_value=1,
            value=max(int(default_qty), 1) if default_qty else 1,
            step=1, key=f"{key_prefix}_qty",
        )
        stop_price_input = c2.number_input(
            "Stop-Loss ($)", min_value=0.01, value=round(float(default_stop_price), 2),
            step=0.01, format="%.2f", key=f"{key_prefix}_stop",
        )

        c3, c4, c5 = st.columns(3)
        tp1_pct = c3.number_input("TP1 (%)", min_value=0.5, value=float(tp1_default), step=0.5, key=f"{key_prefix}_tp1")
        tp2_pct = c4.number_input("TP2 (%)", min_value=0.5, value=float(tp2_default), step=0.5, key=f"{key_prefix}_tp2")
        trailing_pct = c5.number_input("Trailing stop (%)", min_value=0.2, value=float(trailing_default), step=0.2, key=f"{key_prefix}_trail")

        order_type_label = st.radio(
            "Tipo de orden", ["Mercado", "Límite"], horizontal=True, key=f"{key_prefix}_ordertype"
        )
        limit_price = None
        if order_type_label == "Límite":
            limit_price = st.number_input(
                "Precio límite ($)", min_value=0.01, value=round(float(default_price), 2),
                step=0.01, format="%.2f", key=f"{key_prefix}_limitprice",
            )

    return {
        "qty": int(qty),
        "stop_price": float(stop_price_input),
        "tp_tiers": [
            {"gain_pct": tp1_pct, "sell_fraction": 0.5},
            {"gain_pct": tp2_pct, "sell_fraction": 0.25},
        ],
        "trailing_pct": float(trailing_pct),
        "order_type": "limit" if order_type_label == "Límite" else "market",
        "limit_price": limit_price,
    }


def compute_ranked_with_engine_mode(fetcher, universe, mode):
    """
    Corre el ranking con el motor Clásico, el Smart (proporcional), o
    ambos — según lo que el usuario elija en pantalla. Devuelve un dict:
        {"classic": [...]}  o  {"smart": [...]}  o  {"classic": [...], "smart": [...]}
    Restaura config.SCORING_ENGINE a su valor original al terminar, para
    no dejar el estado global "pegado" a un motor entre ejecuciones.
    """
    original_engine = config.SCORING_ENGINE
    results = {}
    try:
        # En Rápido, "Comparar ambos" solo corre Clásico (evita 2x tiempo)
        effective = mode
        if getattr(config, "FAST_SCREENING", False) and mode == "🆚 Comparar ambos":
            effective = "🅰️ Clásico"
        if effective in ("🅰️ Clásico", "🆚 Comparar ambos"):
            config.SCORING_ENGINE = "classic"
            results["classic"] = rank_candidates(fetcher, tickers=universe)
        if effective in ("🅱️ Smart (proporcional)", "🆚 Comparar ambos"):
            config.SCORING_ENGINE = "smart"
            results["smart"] = rank_candidates(fetcher, tickers=universe)
    finally:
        config.SCORING_ENGINE = original_engine
    return results


def render_ranking_table(ranked_list, title=None, key_suffix=""):
    """Renderiza la tabla de screening formateada (reutilizable para 1 o 2 motores a la vez)."""
    if title:
        st.markdown(f"**{title}**")
    if not ranked_list:
        st.info("Sin candidatos para mostrar.")
        return
    df = pd.DataFrame(ranked_list)
    # Prioridad visual: LISTO > VIGILAR > NO, luego Quality
    if not df.empty and "scalp_ready" in df.columns:
        _ord = {"LISTO": 0, "VIGILAR": 1, "NO": 2}
        df = df.assign(
            _sk=df["scalp_ready"].map(lambda x: _ord.get(x, 3))
        ).sort_values(
            by=["_sk", "score"] if "score" in df.columns else ["_sk"],
            ascending=[True, False] if "score" in df.columns else [True],
        ).drop(columns=["_sk"], errors="ignore")


    # "Sin cobertura" vs "Descartado": si NINGUNA fuente gratuita tiene ni
    # precio ni float para el ticker, no es lo mismo que evaluarlo y que
    # saliera mal — es que no hay con qué evaluarlo. Antes ambos casos se
    # veían igual (todo en None + "DESCARTAR"), lo cual generó confusión
    # varias veces pensando que era un bug del programa.
    if "price" in df.columns and "float_shares" in df.columns:
        sin_cobertura = df["price"].isna() & df["float_shares"].isna()
        df.loc[sin_cobertura, "signal"] = "SIN COBERTURA"
    # entry_score/chase_status son nuevos — con get() por si algún registro viejo
    # (caché, backtest) todavía no los trae, para no romper la tabla.
    if "entry_score" not in df.columns:
        df["entry_score"] = None
    if "chase_status" not in df.columns:
        df["chase_status"] = None
    if "dilution_risk" not in df.columns:
        df["dilution_risk"] = None
    if "data_confidence" not in df.columns:
        df["data_confidence"] = None
    if "data_quality" not in df.columns:
        df["data_quality"] = None
    if "halted" not in df.columns:
        df["halted"] = False
    if "short_pressure" not in df.columns:
        df["short_pressure"] = None
    if "rvol_session" not in df.columns:
        df["rvol_session"] = None
    if "scalp_ready" not in df.columns:
        df["scalp_ready"] = None

    # Preferir RVOL session-aware (scalping); fallback al rvol clásico
    if "rvol_session" in df.columns:
        df["rvol_display"] = df["rvol_session"].where(df["rvol_session"].notna(), df.get("rvol"))
    else:
        df["rvol_display"] = df.get("rvol")

    cols = [
        "symbol", "price", "gap_pct", "rvol_display", "float_shares", "rsi",
        "score", "entry_score", "chase_status", "dilution_risk",
        "short_pressure", "data_confidence", "halted", "scoring_session", "scalp_ready", "signal",
    ]
    cols = [c for c in cols if c in df.columns]
    df_display = df[cols].copy()
    if "float_shares" in df_display.columns:
        df_display["float_shares"] = df_display["float_shares"].apply(
            lambda v: (v / 1_000_000) if pd.notnull(v) and v > 0 else None
        )

    rename_map = {
        "symbol": "Ticker",
        "price": "Precio",
        "gap_pct": "Gap %",
        "rvol_display": "RVOL",
        "float_shares": "Float (M)",
        "rsi": "RSI",
        "score": "Quality",
        "entry_score": "Entry",
        "chase_status": "Estado",
        "dilution_risk": "Dilución",
        "short_pressure": "Short",
        "data_confidence": "Confianza",
        "halted": "Halt",
        "scoring_session": "Sesión",
        "scalp_ready": "Scalp",
        "signal": "Señal",
    }
    df_display = df_display.rename(columns={k: v for k, v in rename_map.items() if k in df_display.columns})
    # NO convertir columnas numéricas a "N/D" (rompe st.column_config.NumberColumn).
    # Solo textos: None/NaN → cadena vacía o N/D en columnas de texto.
    _numeric_cols = {"Precio", "Gap %", "RVOL", "Float (M)", "RSI", "Quality", "Entry", "Confianza"}
    _text_cols = {"Ticker", "Estado", "Dilución", "Short", "Halt", "Sesión", "Scalp", "Señal"}
    for _col in df_display.columns:
        if _col in _numeric_cols:
            df_display[_col] = pd.to_numeric(df_display[_col], errors="coerce")
        elif _col in _text_cols:
            df_display[_col] = df_display[_col].apply(
                lambda v: "N/D" if v is None or (isinstance(v, float) and pd.isna(v)) or v == "" else v
            )


    chase_emoji = {"NORMAL": "🟢 NORMAL", "EXTENDIDO": "🟡 EXTENDIDO",
                   "MUY_EXTENDIDO": "🟠 MUY EXTENDIDO", "NO_CHASE": "🔴 NO-CHASE",
                   "SIN_DATOS": "⚪ Sin datos"}
    if "Estado" in df_display.columns:
        df_display["Estado"] = df_display["Estado"].map(lambda v: chase_emoji.get(v, v))

    dilucion_emoji = {"BAJO": "🟢 Bajo", "MEDIO": "🟡 Medio", "ALTO": "🔴 Alto", "DESCONOCIDO": "⚪ N/D",
                     "Bajo": "🟢 Bajo", "Medio": "🟡 Medio", "Alto": "🔴 Alto"}
    if "Dilución" in df_display.columns:
        df_display["Dilución"] = df_display["Dilución"].map(lambda v: dilucion_emoji.get(v, v) if v else "⚪ N/D")

    if "Halt" in df_display.columns:
        df_display["Halt"] = df_display["Halt"].map(lambda v: "🔴 SÍ" if v else "🟢 No")

    scalp_emoji = {"LISTO": "🟢 LISTO", "VIGILAR": "🟡 VIGILAR", "NO": "🔴 NO"}
    session_emoji = {"premarket": "🌅 PM", "regular": "🔔 REG", "afterhours": "🌙 AH"}
    if "Sesión" in df_display.columns:
        df_display["Sesión"] = df_display["Sesión"].map(lambda v: session_emoji.get(v, v) if v else "—")
    if "Scalp" in df_display.columns:
        df_display["Scalp"] = df_display["Scalp"].map(lambda v: scalp_emoji.get(v, v) if v else "⚪ N/D")

    short_emoji = {
        "BAJO": "🟢 Bajo", "NORMAL": "🟡 Normal", "ALTO": "🟠 Alto",
        "MUY_ALTO": "🔴 Muy alto", "N/D": "⚪ N/D",
    }
    if "Short" in df_display.columns:
        df_display["Short"] = df_display["Short"].map(lambda v: short_emoji.get(v, v) if v else "⚪ N/D")

    def resaltar_compra(row):
        # Verde solo si Quality Y Entry son buenos Y no está en halt Y confianza razonable
        try:
            buena_calidad = float(row.get("Quality") or 0) >= config.SCORE_MIN_TO_BUY
        except Exception:
            buena_calidad = False
        estado = str(row.get("Estado", ""))
        buen_momento = ("MUY EXTENDIDO" not in estado) and ("NO-CHASE" not in estado)
        en_halt = "SÍ" in str(row.get("Halt", ""))
        scalp = str(row.get("Scalp", ""))
        conf = row.get("Confianza")
        try:
            conf_ok = conf is None or float(conf) >= 70
        except Exception:
            conf_ok = True
        if en_halt or "NO" in scalp and "LISTO" not in scalp and "VIGILAR" not in scalp:
            # halt o Scalp NO
            if en_halt:
                color = "background-color: #f8d7da"
            elif "🔴 NO" in scalp or scalp.strip() == "NO":
                color = "background-color: #f8d7da"
            else:
                color = ""
        elif "LISTO" in scalp:
            color = "background-color: #d4f7d4"
        elif buena_calidad and buen_momento and conf_ok:
            color = "background-color: #d4f7d4"
        elif "VIGILAR" in scalp or buena_calidad:
            color = "background-color: #fff3cd"
        else:
            color = ""
        return [color] * len(row)

    st.dataframe(
        df_display.style.apply(resaltar_compra, axis=1),
        use_container_width=True, hide_index=True,
        key=f"ranking_table_{key_suffix}",
        column_config={
            "Precio": st.column_config.NumberColumn(format="$%.2f"),
            "Gap %": st.column_config.NumberColumn(format="%.2f%%"),
            "RVOL": st.column_config.NumberColumn(format="%.2fx"),
            "Float (M)": st.column_config.NumberColumn(format="%.2fM"),
            "RSI": st.column_config.NumberColumn(format="%.1f"),
            "Quality": st.column_config.NumberColumn(format="%.2f", help="Qué tan bueno es el candidato"),
            "Entry": st.column_config.NumberColumn(format="%.2f", help="Si AHORA es buen momento para entrar (0-10)"),
            "Confianza": st.column_config.NumberColumn(format="%.0f", help="Data Confidence 0-100 (calidad de los datos)"),
        },
    )
    st.caption(
        "🟢 Verde = Quality + Entry buenos + datos confiables · "
        "🟡 Amarillo = Quality bueno pero extendido o datos incompletos · "
        "🔴 Rojo = HALT activo (no operar) · "
        "Confianza < 70 → solo VIGILAR · "
        "Short = % short volume FINRA (contexto) · "
        "Scalp = señal unificada LISTO / VIGILAR / NO (Quality+Entry+Halt+Confianza+horario)."
    )


# ---------------------------------------------------------------------------
# PESTAÑAS PRINCIPALES
# ---------------------------------------------------------------------------
tab_live, tab_history = st.tabs(["📈 Operativa en Vivo", "📊 Historial y Estadísticas"])

with tab_live:
    st.title("Titon — Small Caps Trading Bot")
    modo_txt = "🟢 PAPER TRADING (simulado)" if config.PAPER_TRADING else "🔴 CUENTA REAL — ¡cuidado!"
    st.caption(f"Modo actual: **{modo_txt}**  |  Score mínimo de compra: **{config.SCORE_MIN_TO_BUY}/10**")

    # -----------------------------------------------------------------
    # KPIs DE CABECERA
    # -----------------------------------------------------------------
    account_info = get_account_info()
    kill_switch_triggered = (
        account_info is not None and account_info["daily_pnl_pct"] <= -config.DAILY_MAX_LOSS_PCT
    )

    k1, k2, k3, k4 = st.columns(4)
    if account_info:
        k1.metric("💰 Buying Power", f"${account_info['buying_power']:,.2f}")
        k2.metric(
            "📊 P&L del Día",
            f"${account_info['daily_pnl']:,.2f}",
            f"{account_info['daily_pnl_pct']:+.2f}%",
        )
    else:
        k1.metric("💰 Buying Power", "N/A")
        k2.metric("📊 P&L del Día", "N/A")
    k3.metric("🛑 Kill-Switch", "🔴 Detenido" if kill_switch_triggered else "🟢 Operativo")
    n_oportunidades = sum(1 for r in st.session_state.ranked if r["score"] >= config.SCORE_MIN_TO_BUY)
    k4.metric("🎯 Oportunidades (Score≥9)", n_oportunidades)

    if kill_switch_triggered:
        st.error(
            f"🔴 Kill-switch activado: la cuenta ya perdió más del "
            f"{config.DAILY_MAX_LOSS_PCT}% hoy. Las compras nuevas están bloqueadas "
            f"hasta el día siguiente."
        )
        if not st.session_state.get("kill_switch_alerted", False):
            alert_kill_switch(account_info["daily_pnl_pct"], config.DAILY_MAX_LOSS_PCT)
            st.session_state.kill_switch_alerted = True
    else:
        st.session_state.kill_switch_alerted = False

    with st.expander("🔧 Diagnóstico de credenciales (no expone tu clave completa)", expanded=True):
        def _mask(value):
            return f"{value[:4]}...{value[-4:]}  (longitud: {len(value)})" if value and len(value) >= 8 else "(vacío)"
        st.write(f"**ALPACA_API_KEY:** `{_mask(config.ALPACA_API_KEY)}`")
        st.write(f"**ALPACA_SECRET_KEY:** `{_mask(config.ALPACA_SECRET_KEY)}`")
        st.write(f"**ALPACA_PAPER:** `{config.PAPER_TRADING}`")

        st.divider()
        if st.button("🔌 Probar conexión real con Alpaca (cuenta + cotización + barras)"):
            with st.spinner("Probando..."):
                diag = fetcher.test_connection()
            for label, key in [("Cuenta", "account"), ("Cotización (AAPL)", "quote"), ("Barras (AAPL)", "bars")]:
                r = diag[key]
                if r["ok"]:
                    st.success(f"✅ {label}: {r['detail']}")
                else:
                    st.error(f"❌ {label}: {r['detail']}")
            if not diag["account"]["ok"]:
                st.warning(
                    "La prueba de **Cuenta** falló primero — esto casi siempre significa "
                    "que las llaves ALPACA_API_KEY/ALPACA_SECRET_KEY están mal copiadas, "
                    "vencidas, o son de un tipo (paper/live) distinto al configurado en "
                    "ALPACA_PAPER. Revisa Streamlit Cloud → Settings → Secrets."
                )
            elif diag["account"]["ok"] and not diag["quote"]["ok"]:
                st.warning(
                    "La cuenta es válida pero la **cotización** falló — revisa en tu cuenta de "
                    "Alpaca (dashboard web) que hayas aceptado el 'Market Data Agreement', "
                    "obligatorio incluso para el feed gratuito IEX. Sin aceptarlo, Alpaca "
                    "responde 403 en cada petición de datos aunque las llaves sean correctas."
                )

    # -----------------------------------------------------------------
    # SCREENING
    # -----------------------------------------------------------------
    st.header("🔍 Screening")

    motor_scoring = st.radio(
        "🧠 Motor de Scoring",
        ["🅰️ Clásico", "🆚 Comparar ambos", "🅱️ Smart (proporcional)"],
        index=1, horizontal=True,
        help=(
            "Clásico: filtros probados en producción (float 1M-10M fijo). "
            "Smart: proporcional al tamaño real de la empresa (Float Market Cap, "
            "Market Cap Band, Float Turnover, RVOL Estructural). "
            "Comparar ambos: corre los dos y los muestra apilados (Clásico arriba, Smart abajo) para ver todas las columnas."
        ),
    )

    session_mode_label = st.radio(
        "⏰ Sesión de scoring (hora NY)",
        [
            "🔄 Auto (detecta premarket / regular / after-hours)",
            "🌅 Premarket 4:00–9:30 (cazar gaps)",
            "🔔 Regular 9:30–16:00 (gap & go clásico)",
            "🌙 After-Hours 16:00–20:00 (spikes post-close)",
            "Sin filtro horario",
        ],
        index=0,
        horizontal=True,
        help=(
            "Cada sesión usa umbrales distintos (gap, RVOL, Quality/Entry para LISTO). "
            "Premarket y After-Hours permiten cazar gaps con menos volumen pero más exigencia de score. "
            "Auto = usa el reloj de Nueva York. Forzar sesión sirve para estudiar setups aunque el mercado esté cerrado."
        ),
        key="session_mode_ui",
    )
    _sm_map = {
        "🔄 Auto (detecta premarket / regular / after-hours)": "auto",
        "🌅 Premarket 4:00–9:30 (cazar gaps)": "premarket",
        "🔔 Regular 9:30–16:00 (gap & go clásico)": "regular",
        "🌙 After-Hours 16:00–20:00 (spikes post-close)": "afterhours",
        "Sin filtro horario": "off",
    }
    config._SESSION_MODE_RUNTIME = _sm_map.get(session_mode_label, "auto")
    config._SESSION_FILTER_RUNTIME = config._SESSION_MODE_RUNTIME  # compat

    # Badge de sesión actual (reloj NY)
    try:
        from strategy import get_current_session
        _clock = get_current_session()
        _clock_lbl = {"premarket": "🌅 Premarket", "regular": "🔔 Regular",
                      "afterhours": "🌙 After-Hours", "closed": "⛔ Cerrado"}.get(_clock, _clock)
        _prof = config.get_session_scoring_profile(
            config._SESSION_MODE_RUNTIME if config._SESSION_MODE_RUNTIME in ("premarket", "regular", "afterhours")
            else (_clock if _clock != "closed" else "regular")
        )
        st.caption(
            f"Reloj NY ahora: **{_clock_lbl}** · Perfil de scoring activo: **{_prof.get('label')}** "
            f"(Gap≥{_prof.get('gap_min_pct')}% · LISTO si Quality≥{_prof.get('score_min_listo')} y Entry≥{_prof.get('entry_min_listo')})"
        )
    except Exception:
        pass

    velocidad_screening = st.radio(
        "⚡ Velocidad de screening",
        [
            "🐢 Completo (más datos: TD + SEC + Top 30)",
            "⚡ Rápido (sin Twelve Data/SEC, paralelo — ideal scalp)",
        ],
        horizontal=True,
        help="Rápido: SIN Twelve Data (evita pausas de 60s). Usa esto en scalp. Completo: más datos, más lento.",
    )
    config.FAST_SCREENING = velocidad_screening.startswith("⚡")

    
    
    st.markdown("### 📥 Importar lista (Webull / Moomoo)")
    st.caption(
        "**Para scalp:** sube el CSV o Excel exportado de Moomoo → **Cargar lista**. "
        "Se priorizan Gap/RVOL/precio/float del archivo cuando están disponibles."
    )
    # Limpiar el text_area sin borrar a mano
    if st.session_state.pop("_clear_import_paste", False):
        st.session_state["import_paste_tickers"] = ""
    c1, c2 = st.columns(2)
    with c1:
        pasted = st.text_area(
            "Pegar texto / tabla Webull",
            height=100,
            placeholder="Solo si no tienes CSV. Pulsa «Limpiar pegado» antes de pegar de nuevo.",
            key="import_paste_tickers",
        )
        if st.button("🧹 Limpiar pegado", key="btn_clear_paste"):
            st.session_state["_clear_import_paste"] = True
            st.rerun()
    with c2:
        up = st.file_uploader(
            "Archivo CSV / Excel / TXT de Moomoo",
            type=["csv", "txt", "xlsx", "xls"],
            key="import_file_tickers",
        )
        if up is not None:
            st.caption(f"Archivo: **{up.name}** ({len(up.getvalue())} bytes)")

    if st.button("➕ Cargar lista a watchlist", type="primary", key="btn_import_tickers"):
        from ticker_from_text import (
            extract_tickers,
            extract_tickers_from_csv,
            extract_rows_from_csv,
            extract_rows_from_excel,
        )
        # Entradas ricas: symbol + gap/rvol del CSV Moomoo; el texto/Webull aporta solo símbolos
        entries: list = []
        by_sym = {}
        sources = []
        if up is not None:
            raw = up.getvalue()
            name = (getattr(up, "name", "") or "").lower()
            try:
                if name.endswith((".xlsx", ".xls")):
                    rows = extract_rows_from_excel(raw, filename=name)
                    source_name = "Excel"
                else:
                    rows = extract_rows_from_csv(raw)
                    source_name = "CSV/TXT"
                for e in rows:
                    by_sym[e["symbol"]] = dict(e)
                sources.append(f"{source_name}:{len(rows)}")
                imported_fields = {
                    "gap_override": "Gap",
                    "rvol_override": "RVOL",
                    "price_hint": "precio",
                    "float_override": "float",
                    "market_cap_override": "market cap",
                    "premarket_volume_override": "volumen PM",
                    "afterhours_volume_override": "volumen AH",
                    "volume_override": "volumen",
                    "prev_close_hint": "cierre previo",
                }
                found = [
                    f"{label}:{sum(e.get(field) is not None for e in rows)}"
                    for field, label in imported_fields.items()
                    if any(e.get(field) is not None for e in rows)
                ]
                if found:
                    sources.extend(found)
                elif rows:
                    st.warning(
                        "Leí los tickers, pero no encontré columnas reconocidas de Gap/RVOL/precio/float. "
                        "Revisa los encabezados del export de Moomoo."
                    )
            except Exception as exc:
                st.error(f"No pude leer el archivo {up.name}: {exc}")
        blob = (pasted or "").strip()
        if blob:
            from_paste = extract_tickers(blob)
            sources.append(f"texto:{len(from_paste)}")
            for s in from_paste:
                if s not in by_sym:
                    by_sym[s] = {"symbol": s}
        ban = {"AAPL", "TSLA", "MSFT", "AMZN", "NVDA", "META", "GOOG", "GOOGL"}
        entries = [v for k, v in by_sym.items() if k not in ban]
        if not entries:
            st.error(
                "No se detectó ningún ticker. "
                "Usa un CSV/Excel de Moomoo o pega texto y pulsa Cargar lista."
            )
        else:
            syms = [e["symbol"] for e in entries]
            try:
                ok = replace_manual_watchlist(syms)
            except Exception as e:
                ok = list(syms)
                st.warning(f"Archivo no escrito ({e}); sesión OK.")
            st.session_state["manual_watchlist"] = list(ok)
            # Guardar overrides (gap/rvol) del CSV para el scoring
            st.session_state["manual_entries_rich"] = entries
            st.session_state.pop("manual_ranked", None)
            st.session_state["_clear_import_paste"] = True
            gap_n = sum(1 for e in entries if e.get("gap_override") is not None)
            st.success(
                f"✅ Watchlist: **{len(ok)}** tickers ({', '.join(sources)})\n\n"
                + ", ".join(ok[:40])
                + ("…" if len(ok) > 40 else "")
                + (f"\n\n📊 **{gap_n}** con Gap% tomado del archivo importado (no solo API)." if gap_n else "")
            )
            st.info("Siguiente: **Manual** + **Rápido** → **Calificar** (1 clic). CSV + texto se **unen** en una sola lista.")
            st.rerun()

    _wl = st.session_state.get("manual_watchlist") or [e["symbol"] for e in load_manual_tickers()]
    if _wl:
        st.caption(f"Watchlist activa (**{len(_wl)}**): " + ", ".join(_wl[:25]) + ("…" if len(_wl) > 25 else ""))
    else:
        st.caption("Watchlist vacía — CSV/Excel o pegado + Cargar lista.")


    with st.expander("🟠 Moomoo OpenD — cotización (Capa 1)", expanded=False):
        st.caption(
            "Solo PC local con OpenD en 127.0.0.1:11111. "
            "Paper/órdenes = Capa 2 (próxima)."
        )
        try:
            from moomoo_client import get_moomoo_client
            mm = get_moomoo_client()
            st_json = mm.status()
            c1, c2, c3 = st.columns(3)
            c1.metric("Puerto OpenD", "OK" if st_json.get("port_open") else "Cerrado")
            c2.metric("moomoo-api", "OK" if st_json.get("package_ok") else "Falta")
            c3.metric("Quote ctx", "On" if st_json.get("quote_connected") else "Off")
            if st_json.get("message"):
                st.caption(st_json["message"])
            syms_mm = st.text_input("Tickers a cotizar", value="CLRO MIMI", key="moomoo_snap_symbols")
            b1, b2 = st.columns(2)
            if b1.button("Cotizar vía Moomoo", key="btn_moomoo_snap"):
                parts = [x.strip().upper() for x in syms_mm.replace(",", " ").split() if x.strip()]
                with st.spinner("OpenD…"):
                    rows = mm.get_snapshot(parts)
                if rows:
                    st.dataframe(rows, use_container_width=True)
                else:
                    st.warning(mm.last_error or "Sin datos")
            if b2.button("Desconectar quote", key="btn_moomoo_close"):
                mm.close()
                st.success("Desconectado")
        except Exception as e:
            st.error(str(e))



    
    if st.button("🚀 Ejecutar Screening Ahora", type="primary"):
        _spin = "⚡ Screening RÁPIDO..." if config.FAST_SCREENING else "🐢 Screening COMPLETO (más fuentes)..."
        with st.spinner(_spin):
            universe = []
            if modo_screening.startswith("🔍"):
                universe = get_top30_gappers_spikes() or []
                # FAST: limitar solo el escáner automático (no la watchlist manual)
                if universe and config.FAST_SCREENING:
                    universe = universe[:15]
                if not universe:
                    st.warning(
                        "El escáner automático no devolvió resultados (mercado "
                        "cerrado o ambas fuentes fallaron). Prueba el modo Manual."
                    )
                if combinar_ambos:
                    manual_universe = get_universe()
                    existentes = {e["symbol"] for e in universe}
                    for e in manual_universe:
                        if e["symbol"] not in existentes:
                            universe.append(e)
            else:
                # Manual: SOLO watchlist completa (sin recorte a 15)
                rich = st.session_state.get("manual_entries_rich")
                mw = st.session_state.get("manual_watchlist")
                if rich:
                    universe = list(rich)
                elif mw:
                    universe = [{"symbol": s, "float_override": None, "rvol_override": None} for s in mw]
                else:
                    universe = load_manual_tickers()
                if not universe:
                    st.warning("Watchlist manual vacía. Pega Webull/Moomoo arriba y pulsa «Cargar tickers».")
                else:
                    st.caption(f"Modo Manual: calificando **{len(universe)}** símbolos (sin límite de 15).")

            # En Manual no recortar: el usuario importó la lista a propósito (Webull/Moomoo)
            if (
                universe
                and config.FAST_SCREENING
                and len(universe) > 15
                and modo_screening.startswith("🔍")
            ):
                universe = universe[:15]
            ranked_by_engine = {}
            if universe:
                _is_manual = not modo_screening.startswith("🔍")
                _prev_lim = getattr(config, "_RANK_LIMIT_OVERRIDE", None)
                if _is_manual:
                    config._RANK_LIMIT_OVERRIDE = 0  # mostrar todos
                try:
                    ranked_by_engine = compute_ranked_with_engine_mode(fetcher, universe, motor_scoring) or {}
                except Exception as _rank_ex:
                    st.error(f"Error en ranking: {_rank_ex}")
                    ranked_by_engine = {}
                finally:
                    config._RANK_LIMIT_OVERRIDE = _prev_lim
            st.session_state.ranked_by_engine = ranked_by_engine
            st.session_state.ranked = (
                ranked_by_engine.get("classic")
                or ranked_by_engine.get("smart")
                or []
            )
            if st.session_state.ranked:
                st.success(f"Listo: {len(st.session_state.ranked)} tickers calificados.")
            else:
                st.warning(
                    "Screening terminó sin filas. Usa **Rápido**, espera 1 min si hubo rate limit, "
                    "y no pulses Completo ni varios botones a la vez."
                )

            # --- Alerta de Telegram: candidatos con score alto (una sola vez por símbolo/sesión) ---
            if "alerted_symbols" not in st.session_state:
                st.session_state.alerted_symbols = set()
            for r in st.session_state.ranked:
                if r["score"] >= config.SCORE_MIN_TO_BUY and r["symbol"] not in st.session_state.alerted_symbols:
                    alert_high_score(r["symbol"], r["score"], r["price"], r["gap_pct"] or 0.0)
                    st.session_state.alerted_symbols.add(r["symbol"])

    # -----------------------------------------------------------------
    # MIS TICKERS MANUALES
    # -----------------------------------------------------------------
    if st.session_state.get("manual_entries_rich"):
        manual_entries_raw = list(st.session_state["manual_entries_rich"])
    elif st.session_state.get("manual_watchlist"):
        manual_entries_raw = [
            {"symbol": s, "float_override": None, "rvol_override": None}
            for s in st.session_state["manual_watchlist"]
        ]
    else:
        manual_entries_raw = load_manual_tickers()

    st.subheader("📌 Mis Tickers Manuales")
    if not manual_entries_raw:
        st.warning("Watchlist vacía. Usa **Cargar lista a watchlist** arriba (CSV o pegado).")
    else:
        st.caption("En lista: " + ", ".join(e["symbol"] for e in manual_entries_raw[:30]))
        _momo_rows = [
            {"symbol": entry.get("symbol"), **entry["momo_data"]}
            for entry in manual_entries_raw
            if isinstance(entry, dict) and isinstance(entry.get("momo_data"), dict)
        ]
        if _momo_rows:
            _momo_labels = {
                "symbol": "Ticker", "name": "Nombre", "price": "Precio Momo",
                "premarket_price": "Precio PM", "afterhours_price": "Precio AH",
                "change": "Cambio $", "change_pct": "Cambio %", "market_cap": "Market Cap",
                "volume": "Volumen", "turnover": "Turnover", "bid": "Bid", "ask": "Ask",
                "free_float": "Free Float", "bid_size": "Bid Size", "ask_size": "Ask Size",
                "open": "Open", "prev_close": "Prev Close", "high": "High", "low": "Low",
                "vol_ratio": "Vol Ratio", "bid_ask_ratio": "Bid/Ask Ratio", "range_pct": "Range %",
                "pe_lfy": "P/E LFY", "turnover_pct": "Turnover %", "change_rate": "Change Rate",
                "premarket_change_pct": "Cambio % PM", "afterhours_change_pct": "Cambio % AH",
                "premarket_volume": "Volumen PM", "afterhours_volume": "Volumen AH",
                "five_min_change": "5min Chg", "five_day_change": "5D Chg", "ten_day_change": "10D Chg",
                "twenty_day_change": "20D Chg", "sixty_day_change": "60D Chg",
                "one_hundred_twenty_day_change": "120D Chg", "two_hundred_fifty_day_change": "250D Chg",
                "ytd_change": "YTD Chg", "industry": "Industry",
            }
            with st.expander("📋 Ver todas las columnas del archivo Moomoo", expanded=False):
                _momo_df = pd.DataFrame(_momo_rows).rename(columns=_momo_labels)
                _momo_df = _momo_df.rename(columns={c: str(c).replace("_", " ").title() for c in _momo_df.columns if c not in _momo_labels})
                _momo_order = [label for key, label in _momo_labels.items() if label in _momo_df.columns]
                _momo_order.extend(c for c in _momo_df.columns if c not in _momo_order)
                st.dataframe(_momo_df[_momo_order], use_container_width=True, hide_index=True)
                st.caption("El Gap usa la columna porcentual de la sesión (`% Chg`, `Pre Mkt % Chg` o `After Hours % Chg`); `Chg` es el cambio absoluto en dólares.")
        if st.button("🔄 Calificar mis tickers manuales", key="btn_score_manual"):
            with st.spinner(f"Calificando {len(manual_entries_raw)} símbolos (lista completa)…"):
                # No aplicar TOP_N: mostrar todos los de la watchlist (SUGP, etc.)
                _prev_lim = getattr(config, "_RANK_LIMIT_OVERRIDE", None)
                config._RANK_LIMIT_OVERRIDE = 0  # all
                try:
                    st.session_state.manual_ranked = rank_candidates(
                        fetcher, tickers=manual_entries_raw
                    )
                    st.success(
                        f"Calificados {len(st.session_state.manual_ranked or [])} / {len(manual_entries_raw)} símbolos"
                    )
                except Exception as _ex:
                    st.error(f"Error al calificar: {_ex}")
                finally:
                    config._RANK_LIMIT_OVERRIDE = _prev_lim
        

        if st.session_state.get("manual_ranked"):
            render_ranking_table(st.session_state.manual_ranked, key_suffix="manual")

            # --- Compra manual BAJO TU PROPIO CRITERIO (sin exigir score >= 9) ---
            st.markdown("**🎯 Compra manual bajo tu propio criterio**")
            st.caption(
                "⚠️ SIN mínimo de score: puedes elegir cualquier ticker (score 3, 5, 7, "
                "el que sea) bajo tu propio criterio y responsabilidad. "
                "El bot sigue calculando el tamaño de posición, stop-loss y take-profit "
                "igual que siempre — solo se salta el requisito automático de Score ≥ 9."
            )
            manual_symbols = [r["symbol"] for r in st.session_state.manual_ranked]
            colm1, colm2 = st.columns([2, 1])
            symbol_manual_buy = colm1.selectbox("Ticker (cualquier score)", manual_symbols, key="manual_buy_select")
            confirmo_manual = colm2.checkbox("Confirmo que es mi decisión, no una señal del bot", key="manual_buy_confirm")

            manual_candidate = next(r for r in st.session_state.manual_ranked if r["symbol"] == symbol_manual_buy)
            manual_price = fetcher.get_latest_price(symbol_manual_buy)

            if manual_price and confirmo_manual and not kill_switch_triggered:
                manual_stop = pm.calculate_dynamic_stop(manual_price, manual_candidate.get("atr"))
                manual_qty = pm.calculate_qty(manual_price, manual_stop)
                st.metric("Score actual (informativo)", f"{manual_candidate['score']:.2f}")

                manual_order_params = render_order_customization_form(
                    "manual_buy", manual_price, manual_qty, manual_stop
                )

                if st.button(f"🚀 Comprar {symbol_manual_buy} (criterio manual)"):
                    pm.enter_long(
                        symbol_manual_buy, manual_price, manual_candidate.get("atr"),
                        qty=manual_order_params["qty"], stop_price=manual_order_params["stop_price"],
                        tp_tiers=manual_order_params["tp_tiers"], trailing_pct=manual_order_params["trailing_pct"],
                        order_type=manual_order_params["order_type"], limit_price=manual_order_params["limit_price"],
                    )
                    st.success(f"✅ Orden enviada (criterio manual): {symbol_manual_buy} @ ${manual_price:.2f}")
            elif kill_switch_triggered:
                st.info("Compras bloqueadas por el kill-switch.")
            elif not confirmo_manual:
                st.info("Marca la casilla de confirmación para habilitar la compra manual.")
        else:
            st.info("Dale a '🔄 Calificar mis tickers manuales' para ver su score, sin importar el Top 20 del scanner automático.")

    if st.session_state.ranked:
        ranked_by_engine = st.session_state.get("ranked_by_engine", {"classic": st.session_state.ranked})

        if len(ranked_by_engine) == 2:
            # Apilados verticalmente: Clásico arriba, Smart abajo
            # así se ven todas las columnas sin recortar en pantallas normales.
            render_ranking_table(ranked_by_engine["classic"], title="🅰️ Motor Clásico", key_suffix="classic")
            st.markdown("---")
            render_ranking_table(ranked_by_engine["smart"], title="🅱️ Motor Smart (proporcional)", key_suffix="smart")
        else:
            engine_name = "🅰️ Motor Clásico" if "classic" in ranked_by_engine else "🅱️ Motor Smart (proporcional)"
            render_ranking_table(st.session_state.ranked, title=engine_name, key_suffix="single")

        df = pd.DataFrame(st.session_state.ranked)

        # -------------------------------------------------------------
        # GRÁFICO DE CONFIRMACIÓN (velas + ORB + VWAP + MACD)
        # -------------------------------------------------------------
        st.subheader("📈 Gráfico de confirmación")
        symbol_for_chart = st.selectbox("Selecciona un ticker para ver su gráfico", df["symbol"].tolist())
        if symbol_for_chart:
            chart_bars = fetcher.get_bars(symbol_for_chart, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)
            if not chart_bars.empty:
                chart_bars = chart_bars.tail(150)  # mostrar como máximo las últimas 150 velas
            fig = build_confirmation_chart(symbol_for_chart, chart_bars)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No hay suficientes barras todavía para graficar este ticker.")

        # -------------------------------------------------------------
        # VISTA PREVIA DE ORDEN + COMPRA
        # -------------------------------------------------------------
        st.subheader("🟢 Vista previa de orden y ejecución")

        if not is_within_trading_window():
            st.warning(
                f"⏰ Estás fuera de la ventana horaria recomendada "
                f"({config.TRADING_WINDOW_START_ET}-{config.TRADING_WINDOW_END_ET} hora de Nueva York). "
                f"Las 4 estrategias analizadas coinciden en que después de esa ventana el volumen "
                f"suele apagarse. Puedes comprar igual (útil para pruebas), pero con más cautela."
            )

        buy_candidates = df[df["score"] >= config.SCORE_MIN_TO_BUY]["symbol"].tolist()

        if buy_candidates and not kill_switch_triggered:
            col1, col2 = st.columns([2, 1])
            symbol_to_buy = col1.selectbox("Ticker con score >= mínimo", buy_candidates)
            estrategia_entrada = col2.radio(
                "Estrategia de confirmación",
                ["🔄 Retest del PMH (recomendado)", "📐 ORB clásico", "⚡ Sin validar (directo)"],
                index=0,
            )

            candidate = next(r for r in st.session_state.ranked if r["symbol"] == symbol_to_buy)
            preview_price = fetcher.get_latest_price(symbol_to_buy)

            if preview_price:
                stop_preview = pm.calculate_dynamic_stop(preview_price, candidate.get("atr"))
                qty_preview = pm.calculate_qty(preview_price, stop_preview)
                _profile_preview = config.get_active_exit_profile()
                tp1_pct_preview = _profile_preview["take_profit_tiers"][0]["gain_pct"]
                tp2_pct_preview = _profile_preview["take_profit_tiers"][1]["gain_pct"]
                trailing_pct_preview = _profile_preview["trailing_stop_pct"]
                tp1 = preview_price * (1 + tp1_pct_preview / 100.0)
                tp2 = preview_price * (1 + tp2_pct_preview / 100.0)
                monto_str = (
                    f"${config.TRADE_AMOUNT_USD:,.0f} fijo (single-bullet)"
                    if config.EXECUTION_MODE == "single_bullet" else "1% de riesgo de la cuenta"
                )

                st.markdown(
                    f"**📋 Vista previa (calculadora de riesgo — modo: {monto_str} · "
                    f"perfil: {_profile_preview['label']}):**"
                )
                riesgo_dolares_preview = (preview_price - stop_preview) * qty_preview if qty_preview else 0

                p1, p2, p3, p4, p5 = st.columns(5)
                p1.metric("Acciones a comprar", f"{qty_preview}")
                p2.metric("Stop-Loss inicial", f"${stop_preview:.2f}",
                          f"-{((preview_price - stop_preview) / preview_price) * 100:.1f}%")
                p3.metric("Riesgo en $ (si toca stop)", f"${riesgo_dolares_preview:,.2f}")
                p4.metric(f"TP1 (+{tp1_pct_preview:g}%)", f"${tp1:.2f}")
                p5.metric(f"TP2 (+{tp2_pct_preview:g}%) + Trailing {trailing_pct_preview:g}%", f"${tp2:.2f}")

                if candidate.get("dilution_reason"):
                    st.caption(f"🛡️ SEC EDGAR: {candidate['dilution_reason']}")

                order_params = render_order_customization_form(
                    "main_buy", preview_price, qty_preview, stop_preview
                )

                if st.button(f"🚀 Ejecutar Compra en Paper Trading: {symbol_to_buy}"):
                    bars = fetcher.get_bars(symbol_to_buy, minutes_back=config.LOOKBACK_MINUTES_FALLBACK)

                    def _do_enter(entry_price_used):
                        pm.enter_long(
                            symbol_to_buy, entry_price_used, candidate.get("atr"),
                            qty=order_params["qty"], stop_price=order_params["stop_price"],
                            tp_tiers=order_params["tp_tiers"], trailing_pct=order_params["trailing_pct"],
                            order_type=order_params["order_type"], limit_price=order_params["limit_price"],
                        )

                    if estrategia_entrada.startswith("🔄"):
                        pmh = get_premarket_high(bars)
                        if pmh is None:
                            st.warning("No se pudo calcular el PMH (falta data de pre-market para este ticker).")
                        else:
                            decision = check_gap_and_go_retest(bars, pmh, candidate)
                            if decision["entry_signal"]:
                                _do_enter(decision["entry_price"])
                                st.success(f"✅ Orden enviada (retest PMH ${pmh:.2f}): {symbol_to_buy} @ ${decision['entry_price']:.2f}")
                            else:
                                st.warning(f"Sin retest confirmado todavía: {decision['reason']}")
                    elif estrategia_entrada.startswith("📐"):
                        decision = check_orb_breakout(bars, candidate)
                        if decision["entry_signal"]:
                            _do_enter(decision["entry_price"])
                            st.success(f"✅ Orden enviada: {symbol_to_buy} @ ${decision['entry_price']:.2f}")
                        else:
                            st.warning(f"Sin breakout ORB confirmado todavía: {decision['reason']}")
                    else:
                        _do_enter(preview_price)
                        st.success(f"✅ Orden enviada (sin validar): {symbol_to_buy} @ ${preview_price:.2f}")
            else:
                st.warning("No se pudo obtener el precio actual para armar la vista previa.")
        elif kill_switch_triggered:
            st.info("Compras bloqueadas por el kill-switch (ver arriba).")
        else:
            st.info(f"Ningún candidato alcanza el score mínimo ({config.SCORE_MIN_TO_BUY}/10) todavía.")
    else:
        st.info("Aún no has corrido el screening en esta sesión. Presiona el botón de arriba.")

    # -----------------------------------------------------------------
    # POSICIONES ABIERTAS (con cierre de emergencia)
    # -----------------------------------------------------------------
    st.header("📊 Posiciones abiertas")

    @st.fragment(run_every=30)
    def positions_panel():
        for symbol in list(pm.positions.keys()):
            price = fetcher.get_latest_price(symbol)
            if price:
                pm.update_position(symbol, price)

        if pm.positions:
            for symbol, pos in list(pm.positions.items()):
                current_price = fetcher.get_latest_price(symbol) or pos.entry_price
                pnl_dollars = (current_price - pos.entry_price) * pos.qty_remaining
                pnl_pct = pos.gain_pct(current_price)

                c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1, 1, 1.3, 1.3, 1.2])
                c1.write(f"**{symbol}**")
                c2.write(f"Entrada: ${pos.entry_price:.2f}")
                c3.write(f"Actual: ${current_price:.2f}")
                c4.write(f"P&L: ${pnl_dollars:+.2f} ({pnl_pct:+.1f}%)")
                c5.write(f"Stop: ${pos.stop_price:.2f}")
                if c6.button("🔴 Cierre de emergencia", key=f"close_{symbol}"):
                    if pm.close_position_market(symbol, current_price):
                        st.success(f"{symbol} cerrado a mercado.")
                        st.rerun()
        else:
            st.info("No hay posiciones abiertas en este momento.")

    positions_panel()
    st.caption(
        "🔄 Se actualiza cada 30s mientras la pestaña esté abierta. Si se "
        "'duerme' Streamlit Cloud, usa GitHub Actions para automatización 24/7."
    )

    # -----------------------------------------------------------------
    # CONSOLA DE EVENTOS EN VIVO
    # -----------------------------------------------------------------
    with st.expander("📟 Terminal de Logs en Vivo"):
        lines = live_log.get_recent_lines(80)
        st.code("\n".join(lines) if lines else "(sin eventos todavía)", language="log")


# ---------------------------------------------------------------------------
# PESTAÑA 2: HISTORIAL Y ESTADÍSTICAS
# ---------------------------------------------------------------------------
with tab_history:
    st.title("📊 Historial y Estadísticas")
    journal_df = journal_analytics.load_journal()

    if journal_df.empty:
        st.info(
            "Todavía no hay operaciones cerradas. Cuando el bot cierre alguna "
            "posición (take-profit, stop-loss o cierre de emergencia), "
            "aparecerá aquí."
        )
    else:
        stats = journal_analytics.compute_stats(journal_df)

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Operaciones totales", stats["total_trades"])
        s2.metric("Win Rate", f"{stats['win_rate_pct']:.1f}%" if stats["win_rate_pct"] is not None else "N/A")
        pf = stats["profit_factor"]
        if pf == float("inf"):
            pf_display = "∞"
        elif isinstance(pf, (int, float)):
            pf_display = f"{pf:.2f}"
        else:
            pf_display = "N/A"
        s3.metric("Profit Factor", pf_display)
        s4.metric("P&L Total", f"${stats['total_pnl_dollars']:,.2f}")

        st.subheader("📈 Curva de Equity Acumulada")
        equity_curve = journal_analytics.compute_equity_curve(journal_df)
        st.line_chart(equity_curve.set_index("timestamp")["cumulative_pnl"])

        st.subheader("📋 Diario completo de operaciones")
        st.dataframe(
            journal_df.sort_values("timestamp", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "entry_price": st.column_config.NumberColumn(format="$%.2f"),
                "exit_price": st.column_config.NumberColumn(format="$%.2f"),
                "pnl_dollars": st.column_config.NumberColumn(format="$%.2f"),
                "pnl_pct": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
