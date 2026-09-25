"""
journal_analytics.py
---------------------
Lee el diario de operaciones (trade_journal.csv, generado por
execution.py) y calcula las métricas de rendimiento que se muestran en
la pestaña "Historial y Estadísticas" del dashboard:

  - Win Rate % (operaciones ganadoras / total)
  - Profit Factor (ganancia bruta total / pérdida bruta total)
  - Curva de equity acumulada (P&L corrido en el tiempo)
"""

import os

import pandas as pd

import config


def load_journal():
    """Devuelve un DataFrame con el diario, o vacío si todavía no hay operaciones."""
    path = config.TRADE_JOURNAL_FILE
    if not os.path.exists(path):
        return pd.DataFrame(columns=[
            "timestamp", "symbol", "qty", "entry_price", "exit_price",
            "pnl_dollars", "pnl_pct", "reason",
        ])
    try:
        df = pd.read_csv(path, parse_dates=["timestamp"])
        return df
    except Exception:
        return pd.DataFrame()


def compute_stats(df: pd.DataFrame):
    """Calcula Win Rate, Profit Factor y totales a partir del diario."""
    if df.empty:
        return {
            "total_trades": 0,
            "win_rate_pct": None,
            "profit_factor": None,
            "total_pnl_dollars": 0.0,
            "avg_win_pct": None,
            "avg_loss_pct": None,
        }

    wins = df[df["pnl_dollars"] > 0]
    losses = df[df["pnl_dollars"] < 0]

    total_trades = len(df)
    win_rate_pct = round((len(wins) / total_trades) * 100, 1) if total_trades else None

    gross_profit = wins["pnl_dollars"].sum()
    gross_loss = abs(losses["pnl_dollars"].sum())
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else None
    )

    return {
        "total_trades": total_trades,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "total_pnl_dollars": round(df["pnl_dollars"].sum(), 2),
        "avg_win_pct": round(wins["pnl_pct"].mean(), 2) if not wins.empty else None,
        "avg_loss_pct": round(losses["pnl_pct"].mean(), 2) if not losses.empty else None,
    }


def compute_equity_curve(df: pd.DataFrame):
    """Devuelve un DataFrame con el P&L acumulado ordenado por fecha, para graficar."""
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "cumulative_pnl"])
    sorted_df = df.sort_values("timestamp").copy()
    sorted_df["cumulative_pnl"] = sorted_df["pnl_dollars"].cumsum()
    return sorted_df[["timestamp", "cumulative_pnl"]]
