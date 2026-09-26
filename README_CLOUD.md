# Small Cap Radar V1.0.2 — GitHub + Streamlit Cloud

Esta versión parte del proyecto `smallcaps_bot_v6_sincobertura` y añade un camino **cloud-first** para validar el radar antes de activar cualquier ejecución.

## Qué hace V1.0.2
- Universo dinámico de acciones US activas desde Alpaca.
- Descubrimiento primero con los screeners oficiales de Alpaca (movers + most-active), y snapshots IEX solo para candidatos.
- Control de ritmo de llamadas, reintentos ante 429/5xx y respeto de `Retry-After`.
- Fallback controlado de 500 símbolos (Rápido) o 1.000 (Amplio) si el screener no responde; nunca se consulta todo el universo de miles de símbolos en un solo barrido.
- Snapshots IEX para filtrar por precio, gap y volumen en dólares.
- Deep scan 1-min de los candidatos: VWAP, PMH/PML, HOD, EMA 9/20/50, RSI, ATR, ADX, Bollinger, aceleración de precio/volumen.
- SEC EDGAR: filings recientes asociados a posible financiación/dilución.
- Nasdaq Trader: intento de lectura de halts; falla de forma no destructiva.
- GlobeNewswire + PR Newswire RSS: catalizadores.
- FMP opcional para float/shares/market cap y volumen medio.
- `score` y `data_confidence` separados.
- Plan matemático de entrada/stop/TP solo como información.
- **No envía órdenes.**

## Deploy en GitHub
1. Crea un repositorio nuevo.
2. Sube el contenido de esta carpeta.
3. No subas `.streamlit/secrets.toml` ni `.env`.
4. En GitHub Actions → Settings → Secrets and variables → Actions, agrega:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - opcional `FMP_API_KEY`
   - opcional `SEC_USER_AGENT`

## Deploy en Streamlit Community Cloud
En Streamlit Community Cloud selecciona el repositorio, rama y `streamlit_app.py` como entrypoint. En Advanced settings / Secrets pega:

```toml
ALPACA_API_KEY = "TU_CLAVE"
ALPACA_SECRET_KEY = "TU_SECRET"
ALPACA_PAPER = "True"
FMP_API_KEY = ""
TWELVE_DATA_API_KEY = ""
SEC_USER_AGENT = "SmallCapRadar/1.0 contacto@tudominio.com"
```

Las claves deben permanecer fuera de GitHub. Streamlit documenta que los Secrets se configuran en la aplicación y no deben subirse al repositorio.

## Limitaciones que debemos medir en pruebas
1. Alpaca IEX no equivale a volumen consolidado de todas las bolsas.
2. RVOL intradía es un proxy si no tenemos histórico comparable por minuto.
3. Float es opcional en V1 y depende de FMP.
4. RSS no sustituye un terminal de noticias profesional.
5. GitHub Actions no es un motor de ejecución de scalping de baja latencia.
6. Un score alto no significa una probabilidad garantizada de éxito.

## Próxima fase
- histórico intradía comparable por minuto para RVOL real;
- ORB 1/3/5 minutos;
- fuerza relativa SPY/QQQ;
- mejor clasificación SEC por texto de filing;
- deduplicación de eventos;
- alertas Telegram;
- registro de señales para evaluar el score con datos reales;
- después de validar, integración de paper trading manual.
