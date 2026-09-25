"""
backtest.py
-----------
Backtest histórico del TitonSmartEngine usando barras DIARIAS reales de
Alpaca. Diseñado para correr LOCALMENTE en tu VS Code (no en mi entorno
de pruebas, que no tiene salida de red hacia Alpaca).

====================================================================
LIMITACIÓN METODOLÓGICA IMPORTANTE — LÉELA ANTES DE CONFIAR EN LOS
RESULTADOS (esto no es opcional, es la diferencia entre un backtest
útil y uno que te hace perder dinero real por falsa confianza):
====================================================================

Este backtest usa el FLOAT y las ACCIONES EN CIRCULACIÓN ACTUALES
(de hoy) aplicados a los precios HISTÓRICOS de hace 1-2 años. Eso es
un sesgo de "look-ahead": en la vida real, el float de una empresa
hace 18 meses pudo ser muy distinto al de hoy (ofertas, splits,
recompras). Casi ninguna fuente gratuita guarda el float histórico
"tal como era en cada fecha" — por eso ningún backtest casero de
estrategias de small caps con filtros de float es 100% confiable,
ni el nuestro.

Los componentes de "Market Cap" y "Float Turnover" del score, por lo
tanto, tienen esta contaminación. Los componentes de "Gap %", "RVOL
estructural" (usando el volumen real de cada día histórico) y
"Estructura de precio" SÍ son genuinamente históricos y no tienen
este sesgo.

Tratá los resultados como una ORIENTACIÓN DIRECCIONAL de qué tan
seguido el patrón de gap+volumen se sostiene o se revierte — no como
una promesa de rentabilidad futura.

====================================================================
CÓMO CORRERLO:
====================================================================
    python backtest.py --tickers IMRN,CDTG,ONCO --years 2

Requiere las mismas variables de entorno que el resto del bot
(ALPACA_API_KEY, ALPACA_SECRET_KEY) en tu .env local.
"""

import argparse
import logging
from datetime import datetime, timedelta

import pandas as pd

import config
from data_fetcher import DataFetcher
from smart_engine import TitonSmartEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("backtest")


def run_backtest(tickers, years=2, hold_days=1):
    """
    Para cada ticker y cada día histórico disponible:
      1. Calcula el gap % del día (open de hoy vs. close de ayer).
      2. Usa el volumen del PRIMER TRAMO del día como proxy de "volumen
         premarket" (con datos diarios no tenemos el premarket real
         separado — otra limitación a tener en cuenta, ver nota arriba).
      3. Corre el TitonSmartEngine con el float/market cap ACTUALES
         (sesgo de look-ahead documentado arriba).
      4. Si el score aprueba (Single-Bullet), simula una compra al
         cierre de ese día y mide el retorno a `hold_days` después.

    Devuelve un DataFrame con una fila por señal generada, con su
    retorno realizado — para que calcules tú mismo win rate, retorno
    promedio, etc. con los números reales de tu corrida.
    """
    fetcher = DataFetcher()
    engine = TitonSmartEngine()

    results = []

    for symbol in tickers:
        symbol = symbol.strip().upper()
        logger.info(f"[{symbol}] Descargando barras diarias históricas ({years} años)...")

        fundamentals = fetcher.get_fundamentals(symbol)
        market_cap = fundamentals.get("market_cap")
        float_shares = fundamentals.get("float_shares")
        shares_outstanding = fundamentals.get("shares_outstanding")

        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed

        end = datetime.utcnow()
        start = end - timedelta(days=365 * years)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                start=start, end=end, feed=DataFeed.IEX,
            )
            bars = fetcher.data_client.get_stock_bars(req).df
            if bars.empty:
                logger.warning(f"[{symbol}] Sin barras históricas, se omite.")
                continue
            if isinstance(bars.index, pd.MultiIndex):
                bars = bars.xs(symbol, level=0)
        except Exception as e:
            logger.warning(f"[{symbol}] Error descargando histórico: {e}")
            continue

        for i in range(1, len(bars) - hold_days):
            today = bars.iloc[i]
            yesterday = bars.iloc[i - 1]
            future = bars.iloc[i + hold_days]

            data = {
                "ticker": symbol,
                "current_price": float(today["open"]),
                "prev_close": float(yesterday["close"]),
                "market_cap": market_cap,
                "float_shares": float_shares,
                "shares_outstanding": shares_outstanding,
                # Proxy imperfecto: usamos el volumen del día completo como
                # aproximación de "actividad premarket" (ver limitación arriba)
                "premarket_volume": float(today["volume"]) * 0.15,  # heurística: ~15% del volumen del día suele darse en premarket
                "catalyst_verified": None,  # no reconstruible históricamente sin un archivo de noticias fechado
                "sec_dilution_blocked": False,  # no reconstruible sin snapshots históricos de SEC EDGAR
            }

            verdict = engine.evaluate(data)
            if verdict["signal"] == "Single-Bullet Aprobado":
                entry_price = float(today["close"])
                exit_price = float(future["close"])
                forward_return_pct = ((exit_price - entry_price) / entry_price) * 100

                results.append({
                    "ticker": symbol,
                    "date": bars.index[i],
                    "score": verdict["score"],
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "forward_return_pct": round(forward_return_pct, 2),
                })

    return pd.DataFrame(results)


def summarize(df: pd.DataFrame):
    if df.empty:
        print("No se generaron señales 'Single-Bullet Aprobado' en el período analizado.")
        return

    wins = df[df["forward_return_pct"] > 0]
    print(f"\nTotal de señales generadas: {len(df)}")
    print(f"Win rate: {len(wins) / len(df) * 100:.1f}%")
    print(f"Retorno promedio por señal: {df['forward_return_pct'].mean():.2f}%")
    print(f"Retorno mediano: {df['forward_return_pct'].median():.2f}%")
    print(f"Mejor señal: {df['forward_return_pct'].max():.2f}%")
    print(f"Peor señal: {df['forward_return_pct'].min():.2f}%")
    print("\n⚠️ Recuerda: estos números tienen sesgo de look-ahead en float/market "
          "cap (ver la nota al inicio de este archivo). Trátalos como orientación, no como promesa.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest histórico del TitonSmartEngine")
    parser.add_argument("--tickers", required=True, help="Lista separada por comas, ej: IMRN,CDTG,ONCO")
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--hold-days", type=int, default=1, help="Días de tenencia simulados tras la señal")
    parser.add_argument("--output", default="backtest_results.csv")
    args = parser.parse_args()

    tickers = args.tickers.split(",")
    df = run_backtest(tickers, years=args.years, hold_days=args.hold_days)
    df.to_csv(args.output, index=False)
    print(f"\nResultados guardados en {args.output}")
    summarize(df)
