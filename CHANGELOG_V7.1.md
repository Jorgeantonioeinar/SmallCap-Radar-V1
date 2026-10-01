# Small Cap Bot V7.1 — Changelog

## Mejoras incluidas (basadas en auditoría técnica)

### 1. Halt Engine (nuevo módulo `halt_engine.py`)
- Fuente: Nasdaq Trader Trade Halt RSS (100% gratis, sin API key).
- Actualización ~1 vez/minuto con caché.
- Si un ticker está en halt → señal forzada a **DESCARTAR**.
- Visible en el dashboard como columna **Halt**.

### 2. Data Confidence Score (0-100)
- Calculado por ticker según disponibilidad de:
  - precio, previous close / gap, float, RVOL, PMH, estado del feed de halt.
- Niveles: HIGH (≥90) / MEDIUM (75-89) / LOW (<75).
- Si confianza < 70 y la señal era COMPRA_LARGO → se degrada a **VIGILAR**.
- Visible en el dashboard como columna **Confianza**.

### 3. UI (Streamlit)
- Nuevas columnas: **Confianza** y **Halt**.
- Coloreado:
  - Verde: Quality + Entry buenos + datos confiables.
  - Amarillo: Quality bueno pero extendido o datos incompletos.
  - Rojo: HALT activo.

### 4. Seguridad de datos
- El bot ya no propone compra automática cuando faltan datos críticos
  (PMH, float, prev close, etc.).

## Qué NO cambió (a propósito)
- Arquitectura general (Streamlit + Alpaca paper).
- Motores Clásico / Smart.
- Time-stop, Entry Score, Chase Status, perfiles scalping/swing.
- Cadena de float (FMP → Finviz → Massive → Tiingo → Yahoo).

## Cómo desplegar
1. Sube esta carpeta a tu repo de GitHub (o reemplaza el contenido).
2. En Streamlit Cloud: Main file = `streamlit_app.py`.
3. Secrets: los mismos de siempre (ALPACA_*, opcional FMP, TIINGO, etc.).
4. No hace falta API key nueva para el Halt Engine.

## Limitaciones que siguen existiendo
- Premarket/after-hours en tiempo real sigue limitado en planes free
  (Twelve Data free no entrega extended hours).
- Volumen sigue siendo IEX (Alpaca free), no SIP consolidado.
- Estas limitaciones se reflejan ahora en el **Data Confidence Score**.

## Próximos pasos sugeridos (V7.2)
- FINRA Short Sale Volume como contexto.
- Separar RVOL Daily / Intraday / Vol-Float en columnas.
- Catalyst Classifier (GlobeNewswire / FDA).
- Journal con MFE/MAE.

# V7.2 — Short Volume FINRA + RVOL separado

## Nuevas funciones
1. **short_volume.py** — FINRA Daily Short Sale Volume (CDN gratis, sin API key).
   - short_pct, short_pressure (BAJO / NORMAL / ALTO / MUY_ALTO)
   - Columna **Short** en el dashboard
2. **RVOL separado** en `get_volume_metrics()`:
   - rvol_daily (volumen hoy / promedio 10d)
   - rvol_session (normalizado a fracción de sesión — preferido para scalping)
   - float_turnover (volumen / float)
3. El RVOL mostrado en UI usa preferentemente **rvol_session**.

## Notas de uso
- Short volume FINRA es **flujo del día** (off-exchange), NO short interest.
- Valores típicos 30-55% son normales; >70% es presión alta relativa.
- No bloquea entradas por sí solo; es contexto de confirmación.
