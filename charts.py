"""
charts.py
----------
Gráfico interactivo de confirmación para un ticker seleccionado en el
dashboard: velas de 1 minuto + línea de ruptura ORB + VWAP + histograma
MACD, usando Plotly (se renderiza con st.plotly_chart en streamlit_app.py).
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import config
from strategy import compute_vwap, get_opening_range


def build_confirmation_chart(symbol: str, bars: pd.DataFrame):
    """
    Devuelve una figura de Plotly con 3 paneles:
      1. Velas + línea ORB High (dorada punteada) + curva VWAP (azul)
      2. Volumen negociado
      3. Histograma MACD
    Si no hay suficientes barras, devuelve None (el caller debe manejarlo).
    """
    if bars is None or bars.empty or len(bars) < 5:
        return None

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        row_heights=[0.6, 0.2, 0.2],
        vertical_spacing=0.03,
        subplot_titles=(f"{symbol} — Precio, ORB y VWAP", "Volumen", "MACD (histograma)"),
    )

    # --- Panel 1: Velas ---
    fig.add_trace(
        go.Candlestick(
            x=bars.index, open=bars["open"], high=bars["high"],
            low=bars["low"], close=bars["close"], name="Precio",
        ),
        row=1, col=1,
    )

    # --- ORB High (línea dorada punteada) ---
    range_high, _ = get_opening_range(bars, window_minutes=config.ORB_WINDOW_MINUTES)
    if range_high is not None:
        fig.add_hline(
            y=range_high, line_dash="dot", line_color="goldenrod", line_width=2,
            annotation_text="ORB High", annotation_position="top left",
            row=1, col=1,
        )

    # --- VWAP (línea azul), anclado a la sesión del día ---
    vwap_value = compute_vwap(bars)
    if vwap_value is not None:
        # Recalculamos la serie completa (no solo el último valor) para graficarla
        idx_ny = bars.index.tz_convert(bars.index.tz) if bars.index.tz is not None else bars.index
        typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
        vwap_series = (typical_price * bars["volume"]).cumsum() / bars["volume"].cumsum()
        fig.add_trace(
            go.Scatter(x=bars.index, y=vwap_series, mode="lines", name="VWAP",
                       line=dict(color="royalblue", width=2)),
            row=1, col=1,
        )

    # --- Panel 2: Volumen ---
    colors = ["seagreen" if c >= o else "indianred" for o, c in zip(bars["open"], bars["close"])]
    fig.add_trace(
        go.Bar(x=bars.index, y=bars["volume"], name="Volumen", marker_color=colors),
        row=2, col=1,
    )

    # --- Panel 3: MACD histograma ---
    try:
        import ta
        macd_ind = ta.trend.MACD(close=bars["close"])
        histogram = macd_ind.macd_diff()
        hist_colors = ["seagreen" if v >= 0 else "indianred" for v in histogram]
        fig.add_trace(
            go.Bar(x=bars.index, y=histogram, name="MACD hist.", marker_color=hist_colors),
            row=3, col=1,
        )
    except Exception:
        pass

    fig.update_layout(
        height=650,
        showlegend=False,
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return fig
