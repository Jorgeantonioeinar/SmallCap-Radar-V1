# Bot Small Caps Long — Paper Trading (Alpaca + Yahoo Finance)

Implementa la estrategia consolidada que definimos: screener con score 1-10,
entrada manual de tickers, ORB (Opening Range Breakout), stop-loss dinámico
por ATR, trailing stop y take-profit escalonado. **Empieza siempre en Paper
Trading** (`ALPACA_PAPER=True`).

## 1. Instalación

```bash
python3 -m venv venv
source venv/bin/activate        # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Configurar tus credenciales (¡nunca en el código!)

1. Copia `.env.example` a `.env`.
2. Completa `ALPACA_API_KEY` y `ALPACA_SECRET_KEY` con las claves de tu
   cuenta **Paper Trading** de Alpaca (dashboard de Alpaca → "Paper
   Trading" → API Keys).
3. (Opcional) Completa `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` para
   recibir alertas en tu celular (instrucciones dentro de `notifier.py`).
4. Verifica que `.env` esté en tu `.gitignore` antes de subir nada a GitHub.

## 3. Ejecutar el bot (una sola pasada)

```bash
python main.py
```

Esto:
- Te pedirá por consola si quieres agregar tickers manuales (los que saques
  de Momo Screener, Webull, etc.). También puedes editarlos directamente en
  `manual_tickers.txt` (un ticker por línea).
- **Float y RVOL manuales (recomendado si Yahoo Finance falla):** en vez de
  poner solo el ticker, puedes agregar el float y/o el RVOL que ya viste en
  tu escáner externo, separados por coma:
  ```
  IMRN
  CDTG,2500000
  ISPC,1800000,5.2
  ```
  Estos valores puestos a mano **tienen prioridad** sobre lo que devuelva
  Yahoo Finance — el bot sigue funcionando aunque Yahoo esté bloqueando
  peticiones (algo cada vez más común, incluso fuera de este proyecto).
  **Recomendación:** dado que Yahoo Finance bloquea con frecuencia las
  peticiones automatizadas desde servidores compartidos (Streamlit Cloud,
  GitHub Actions, etc.), trata el float/RVOL manual como tu método
  **principal**, no como respaldo — mira Momo Screener/Webull y pégalo
  directamente. Es más confiable que depender de que Yahoo responda.
- Mostrará la tabla con el Top 20 de candidatos: precio, gap %, RVOL,
  float, RSI y la calificación 1-10.
- Para los que tengan score ≥ 9, evaluará si ya hay un breakout ORB válido
  y, si lo hay, enviará la orden de compra en tu cuenta paper de Alpaca.

## 4. Ejecutar en modo continuo (durante toda la sesión)

Edita la última línea de `main.py`:

```python
main(loop=True, interval_seconds=60)
```

Esto repetirá el ciclo cada 60 segundos (ajustable), re-evaluando el
screener, ejecutando nuevas entradas y actualizando el trailing stop /
take-profit de las posiciones abiertas.

## 5. Ajustar la estrategia

Todos los parámetros (float máximo, % de gap mínimo, RVOL mínimo, % de
riesgo por operación, niveles de take-profit, % de trailing stop, etc.)
están centralizados en `config.py` con comentarios explicando cada uno.

## 6. Subirlo a GitHub y ponerlo a correr

### 6.1 Crear el repositorio y subir el código

```bash
cd smallcaps_bot
git init
git add .
git commit -m "Bot small caps long - versión inicial"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

El `.gitignore` ya incluido evita que subas `.env` (tus claves reales) o
la carpeta `__pycache__/` por error. **Nunca** hagas commit de tus API
keys — siempre van como "Secrets" de GitHub (ver 6.2).

### 6.2 Configurar los Secrets de GitHub (tus API keys)

En tu repositorio: **Settings → Secrets and variables → Actions → New
repository secret**. Crea estos cuatro (los dos de Telegram son opcionales):

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Estos secrets nunca quedan visibles en el código ni en los logs.

### 6.3 GitHub Actions (screening automático programado)

Ya incluí el archivo `.github/workflows/screening.yml`. Corre
automáticamente cada 15 minutos durante el horario de mercado de EE.UU.
(ajusta el cron si cambia el horario de verano/invierno, hay una nota
dentro del archivo) e imprime el ranking + ejecuta las entradas en tu
cuenta **paper** de Alpaca. También puedes lanzarlo a mano desde la
pestaña **Actions → Small Caps Screening → Run workflow**.

**Limitación importante de GitHub Actions:** cada ejecución es un proceso
nuevo y efímero (no queda "vivo" entre corridas), así que:
- No hay consola interactiva: en CI el bot **no** te pedirá tickers por
  input(), simplemente lee lo que ya esté guardado en
  `manual_tickers.txt` dentro del repo. Para agregar un ticker manual,
  edítalo y haz commit/push (o edítalo directo en GitHub desde el
  navegador), y en la próxima corrida programada ya lo tomará en cuenta.
- El trailing stop / take-profit se recalculan solo en cada corrida (cada
  15 min en el ejemplo), no segundo a segundo. Para una gestión de
  posición más fina y en tiempo real, la opción 6.4 es mejor.

### 6.4 Alternativa para gestión continua de posiciones (recomendado una vez que ya operes en real)

Para que el trailing stop se actualice cada 30-60 segundos (no cada 15
min) necesitas un proceso que quede corriendo todo el día, algo que
GitHub Actions no está pensado para hacer. Lo más simple es conectar el
mismo repositorio de GitHub a un servidor gratuito/económico:

- **Render / Railway (planes free):** conecta tu repo de GitHub, define
  las mismas variables de entorno como "Environment Variables" del
  servicio, y como comando de arranque usa `python main.py` con
  `main(loop=True, interval_seconds=60)` en la última línea de
  `main.py`. El servicio hace auto-deploy cada vez que haces push a
  GitHub.
- **AWS EC2 Free Tier / VPS propio:** clonas el repo, defines las
  variables de entorno, y lo corres dentro de un `tmux`/`screen` o como
  servicio `systemd` para que sobreviva reinicios.

En ambos casos el código es exactamente el mismo que subiste a GitHub;
solo cambia quién lo ejecuta y con qué frecuencia.

### 6.5 Desplegar el dashboard en Streamlit Community Cloud (gratis)

Ya incluí `streamlit_app.py`, un dashboard visual con: entrada manual de
tickers, tabla de ranking con colores, botón de compra en paper trading
(con o sin validar el ORB), y un panel de posiciones abiertas que se
auto-actualiza cada 30 segundos.

**Pasos:**

1. Sube el código a GitHub (paso 6.1) — Streamlit Cloud se conecta
   directo a tu repositorio, no necesitas subir nada aparte.
2. Entra a **https://share.streamlit.io** e inicia sesión con tu cuenta
   de GitHub.
3. Click en **"New app"** → selecciona tu repositorio y la rama
   (`main`) → en **"Main file path"** escribe `streamlit_app.py`.
4. Antes de darle a "Deploy", abre **"Advanced settings" → "Secrets"** y
   pega esto (con tus valores reales, formato TOML — así es como
   Streamlit Cloud maneja las claves, en vez de variables de entorno
   sueltas):

   ```toml
   ALPACA_API_KEY = "tu_api_key_de_paper_trading"
   ALPACA_SECRET_KEY = "tu_secret_key_de_paper_trading"
   ALPACA_PAPER = "True"
   TELEGRAM_BOT_TOKEN = "tu_token"
   TELEGRAM_CHAT_ID = "tu_chat_id"
   ```

5. Dale a **"Deploy"**. En 1-2 minutos tendrás tu dashboard en una URL
   pública tipo `https://tu-app.streamlit.app`.

**Importante — límite del plan gratuito de Streamlit Cloud:** la app
"se duerme" si nadie la usa por un rato, y el panel de posiciones solo se
auto-actualiza mientras la pestaña esté abierta y la app despierta. Es
decir: **Streamlit es tu panel de control interactivo** (ideal para ver
el ranking, agregar tickers manuales, y decidir/ejecutar compras tú
mismo en tiempo real mientras operas), pero **no reemplaza** al workflow
de GitHub Actions (6.3) para la parte 100% desatendida del screening.
Lo ideal en la práctica: dejas GitHub Actions corriendo el screening
automático de fondo, y abres el dashboard de Streamlit durante tu
horario de trading para monitorear y ejecutar entradas manuales cuando
tú quieras.

## 8. Sistema de redundancia de datos (evitar depender solo de Yahoo)

Para **precios y volumen** (tiempo real):
1. **Alpaca WebSockets** (`realtime_feed.py`) — streaming continuo de trades y barras, con **reconexión automática** (backoff progresivo: 2s, 5s, 10s, 30s, 60s) si la conexión se cae. Se activa solo en modo continuo local (`main.py` con `loop=True`) — una conexión WebSocket persistente no encaja con el modelo de Streamlit, que re-ejecuta el script en cada interacción, así que el dashboard sigue usando REST (ya probado y funcionando).
2. **REST de Alpaca** (respaldo 1) — si el WebSocket aún no tiene un dato reciente (o no está activo), se usa la consulta REST de siempre.
3. **Último dato del WebSocket, aunque esté "viejo"** (respaldo 2) — si hasta el REST falla, se usa el último precio recibido por WebSocket como último recurso, en vez de devolver nada.

Para el **float**, el bot prueba, en este orden, y usa el primero que responda:

1. **Caché local** (`float_cache.csv`) — instantáneo, válido por 20 horas (el float casi no cambia intradía).
2. **Financial Modeling Prep (FMP)** — 250 peticiones/día gratis, da el **float real**. Regístrate gratis en https://financialmodelingprep.com y pon tu clave en `FMP_API_KEY`.
3. **Alpha Vantage** — solo 25 peticiones/día gratis, y da `SharesOutstanding` (acciones totales, **no** el float real) como aproximación. Regístrate en https://www.alphavantage.co/support/#api-key y pon tu clave en `ALPHAVANTAGE_API_KEY`. Úsalo con cuidado: si escaneas más de ~20 tickers en el día, se agota rápido.
4. **Yahoo Finance** — último recurso, gratis pero bloquea seguido.
5. Si las 4 fallan, siempre puedes usar el **override manual** (sección 7): escribir `TICKER,FLOAT` directamente en la entrada manual, que sigue teniendo prioridad sobre todo esto.

Para el **sentimiento de noticias** (nuevo, informativo — no decide una entrada por sí solo):
1. **Finnhub** (gratis, generoso) — trae titulares recientes y aplica una heurística simple de palabras clave (positivas: contrato, aprobación, récord... / negativas: dilución, oferta, demanda...).
2. **Alpha Vantage News Sentiment** — respaldo, mismo límite de 25/día.
3. Si ambas fallan: score neutral (0.0), el bot sigue funcionando igual.

Ninguna de estas 3 claves (`FMP_API_KEY`, `ALPHAVANTAGE_API_KEY`, `FINNHUB_API_KEY`) es obligatoria — si no las configuras, el bot simplemente salta esa fuente y sigue con la siguiente de la cadena (incluyendo el override manual).

## 9. Nuevos indicadores para la entrada en largo

Además del breakout ORB (ruptura del rango de apertura con volumen), agregué dos confirmaciones adicionales que recomiendo especialmente para small caps:

- **VWAP (Volume Weighted Average Price):** es el indicador #1 que usan los traders profesionales de momentum. Mientras el precio esté **sobre** una VWAP que va subiendo, la presión compradora sigue dominando; perder el VWAP con volumen suele ser la señal de salida más confiable, incluso antes de que se dispare tu stop-loss.
- **MACD (12,26,9), histograma:** confirma que el momentum de corto plazo sigue siendo alcista, como filtro independiente del ORB — evita entrar justo cuando el impulso ya se está agotando.

Ambos son configurables en `config.py`:
```python
REQUIRE_VWAP_CONFIRMATION = True   # exigir precio > VWAP para entrar
REQUIRE_MACD_CONFIRMATION = True   # exigir histograma MACD > 0 para entrar
```
Si quieres probar la estrategia sin estos filtros extra (por ejemplo, para comparar cuántas señales pierdes con vs. sin ellos), simplemente ponlos en `False`.

**Otros indicadores que vale la pena considerar más adelante** (no implementados todavía, requieren datos de pago o mayor complejidad):
- **Order flow / Nivel 2 (bid-ask imbalance):** ver quién está "parado" comprando en el ask agresivamente — la señal más confiable de todas, pero requiere un feed de Nivel 2 (de pago).
- **Fuerza relativa vs. el mercado (SPY/QQQ):** un small cap que sube fuerte mientras el mercado general está plano o cae es una señal más limpia que uno que solo sigue al mercado.
- **Contracción de rango (ATR decreciente) antes del breakout:** consolidaciones cada vez más estrechas antes de un movimiento suelen preceder rupturas más explosivas y confiables.

## 10. Ejecutarlo localmente en VS Code (en vez de, o además de, Streamlit Cloud)

Todo el código funciona igual en tu computadora que en la nube — no necesitas cambiar nada del código, solo dónde lo corres:

1. Abre la carpeta `smallcaps_bot/` en VS Code.
2. Abre una terminal integrada (Ctrl+ñ o Terminal → New Terminal) y sigue los pasos de la sección 1-2 de este README (crear entorno virtual, `pip install -r requirements.txt`, copiar `.env.example` a `.env` y poner tus claves reales).
3. Para el bot en consola: `python main.py` (o `python main.py` con `loop=True` en la última línea para que corra en bucle todo el día).
4. Para el dashboard visual: `streamlit run streamlit_app.py` — se abre solo en tu navegador en `http://localhost:8501`, funciona exactamente igual que en Streamlit Cloud, pero corriendo 100% en tu máquina (sin los límites de CPU/sleep del plan gratuito de la nube).

**Ventaja de correrlo local:** control total, sin límites de Streamlit Cloud, y puedes dejarlo corriendo en segundo plano todo el día de trading sin que se "duerma". **Desventaja:** solo funciona mientras tu computadora esté prendida y conectada a internet — no es "24/7 en la nube" como GitHub Actions.

## 11. Scanner automático Top 30 (sin escribir tickers a mano)

Ahora el dashboard tiene un selector en la barra lateral con 2 modos:

- **🔍 Screener Automático (Top 30 Gappers/Spikes):** consulta en vivo el **TradingView Scanner** (endpoint público, gratis, sin necesidad de clave) filtrando por precio ($0.50-$15), capitalización (<$2B), volumen (>500K) y gap (>+5%), y trae automáticamente el Top 30 ordenado por mayor cambio %. Si TradingView falla, cae solo a **Finviz** (vía `finvizfinance`) como respaldo. Si ambos fallan, no rompe nada — simplemente te avisa y puedes usar el modo Manual mientras tanto.
- **📝 Manual / Personalizado:** el modo de siempre, con tu entrada manual de tickers.
- Puedes marcar **"Combinar con mi watchlist manual también"** para juntar ambas listas en un solo screening.

Los parámetros de los filtros del scanner (precio, market cap, volumen, gap mínimo) están en `config.py`:
```python
SCANNER_PRICE_MIN = 0.50
SCANNER_PRICE_MAX = 15.0
SCANNER_MAX_MARKET_CAP = 2_000_000_000
SCANNER_MIN_VOLUME = 500_000
SCANNER_MIN_CHANGE_PCT = 5.0
```

## 12. Actualización del RVOL: ya NO depende de Yahoo Finance

El RVOL (volumen relativo) ahora se calcula así:
1. **Alpaca** (barras DIARIAS de los últimos 20 días naturales, promediando los últimos 10 días hábiles) — fuente principal, ya no usa Yahoo para esto.
2. **Twelve Data** (respaldo, si Alpaca no devuelve barras) — necesita `TWELVE_DATA_API_KEY` (gratis en twelvedata.com).

Los errores de FMP (402/429, típicos cuando el plan gratis no cubre un ticker) ahora se capturan en **silencio** (nivel `debug`, no aparecen en los logs normales de Streamlit Cloud) y la fuente se desactiva automáticamente el resto de la sesión para no seguir gastando llamadas inútiles.

## 14. Rango de precio ampliado y alertas de Telegram conectadas

- **Rango de precio:** ahora acepta acciones entre **$1 y $40** (antes $1-$20), tanto en el screener manual (`config.PRICE_MIN`/`PRICE_MAX`) como en el scanner automático (`config.SCANNER_PRICE_MIN`/`SCANNER_PRICE_MAX`).
- **Alertas de Telegram**, ya conectadas de verdad a estos 3 eventos (con `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` configurados en tus Secrets):
  1. 🎯 **Score alto detectado** (≥9.0) — se envía una sola vez por ticker por sesión, justo al terminar el screening.
  2. 🟢/💰/🔴 **Orden ejecutada o posición cerrada** en Paper Trading (compra, take-profit parcial, stop-loss, cierre de emergencia).
  3. 🛑 **Kill-switch activado** (pérdida diaria ≥ 3%) — se envía una sola vez por día, no en cada actualización de la pantalla.

Todas las alertas usan formato Markdown y viven en `notifier.py` (`alert_high_score`, `alert_order_executed`, `alert_position_closed`, `alert_kill_switch`), todas construidas sobre una única función base `send_telegram_alert()`.

## 15. Dashboard profesional (KPIs, gráfico Plotly, diario de operaciones)

El dashboard ahora tiene 2 pestañas:

**📈 Operativa en Vivo:**
- 4 KPIs de cabecera: Buying Power, P&L del día, estado del Kill-Switch, número de oportunidades con Score≥9.
- Tabla de screening con formato profesional (moneda, %, float en millones) vía `st.column_config`.
- **Gráfico de confirmación interactivo (Plotly):** al elegir un ticker, muestra velas de 1 minuto + línea dorada del ORB High + curva azul del VWAP + volumen + histograma MACD, todo en un solo gráfico con 3 paneles.
- **Vista previa de orden:** antes de comprar, muestra cuántas acciones se comprarían, el stop-loss inicial, y los niveles TP1/TP2, calculados con las mismas fórmulas que usa la ejecución real.
- **Posiciones abiertas mejoradas:** P&L flotante en $ y %, y un botón individual de **"🔴 Cierre de emergencia"** por posición (cancela el stop y vende a mercado de inmediato).
- **Consola de logs en vivo:** un panel desplegable con los últimos eventos del bot en tiempo real.

**📊 Historial y Estadísticas:**
- Cada operación cerrada (take-profit, stop-loss, o cierre de emergencia) se guarda automáticamente en `trade_journal.csv`.
- Se calculan **Win Rate %**, **Profit Factor** (ganancia bruta / pérdida bruta) y **P&L total**.
- Se grafica la **curva de equity acumulada** día a día.
- Tabla completa del diario, descargable/revisable directamente.

## 17. Análisis de las 4 estrategias de Gap & Go — qué se incorporó y por qué

Revisé a fondo las 4 transcripciones (Alex Flamas, Pau - Trading Studio, Ulfur Trading, y la clase de reciclaje) y extraje lo que se repite consistentemente en todas — eso es lo que vale la pena tomar como regla, no una opinión aislada de un solo trader:

| Lección repetida en las 4 fuentes | Cómo se implementó |
|---|---|
| **"No persigas la vela verde, compra el retroceso"** | Nueva función `check_gap_and_go_retest()`: exige ruptura confirmada del PMH + retroceso con volumen decreciente + reclamo sobre VWAP, en vez de comprar directo en la ruptura (que era lo que hacía el ORB clásico). Ahora es la estrategia por defecto. |
| **Float entre 1M y 10M da ~85% de acierto** | `FLOAT_MIN_SHARES=1,000,000` / `FLOAT_MAX_SHARES=10,000,000` — fuera de ese rango, se descarta directo (antes el tope era 20M y no había piso). |
| **La dilución/oferta puede destruir el trade en segundos (caso APVO)** | Nuevo módulo `sec_shield.py`: consulta la SEC EDGAR real y bloquea (424B4/424B5) o penaliza fuertemente (S-1/S-3) según haya evidencia real de dilución. |
| **Durante un halt, el stop-loss NO se ejecuta** | Esto es una limitación real de cualquier bróker (no solo nuestro bot) — lo importante es lo anterior: el SEC Shield busca evitar entrar en tickers con riesgo de halt por dilución en primer lugar. |
| **Evitar perseguir el precio; exigir volumen decreciente en el pullback** | Implementado literalmente en `check_gap_and_go_retest()`. |
| **Empresas chinas se comportan más erráticas/manipuladas** | Penalización de -1 punto si el país de la sede es China/Hong Kong/Taiwán (vía perfil de FMP). |
| **Mejor horario: apertura hasta la 1:00pm ET** | `is_within_trading_window()` — el dashboard avisa (no bloquea, para que puedas seguir practicando) si estás fuera de esa ventana. |
| **Objetivo de scalping rápido, no aguantar indefinidamente** | Ya cubierto por el take-profit escalonado (+15%/+25%) y el trailing stop existentes. |

**Bug real que encontré de paso:** la librería `ta` calcula el VWAP con una ventana móvil de 14 velas, NO el VWAP real (acumulado desde la apertura) que describen las 4 transcripciones. Lo reemplacé por el cálculo correcto — esto afectaba tanto al ORB como al gráfico.

## 18. Nueva configuración de ejecución: Single-Bullet

Por defecto ahora el bot compra con un **monto fijo en dólares** en vez de calcular por % de riesgo — más simple y predecible mientras aprendes:

```python
EXECUTION_MODE = "single_bullet"   # o "risk_based" para volver al modo anterior
TOTAL_BULLETS = 1                  # arquitectura lista para escalar a 5 disparos más adelante
TRADE_AMOUNT_USD = 1000.0          # monto fijo por operación
```

## 19. Sobre Moomoo y TradeZero (honestidad técnica)

- **Moomoo (Futu OpenD):** requiere un programa corriendo en tu propia computadora (el "gateway" OpenD) — **no puede funcionar en Streamlit Cloud**, solo sería viable si más adelante corres el bot 100% local en VS Code con OpenD abierto al mismo tiempo.
- **TradeZero:** no ofrece una API pública para cuentas retail — no existe un endpoint al que conectar código de forma legítima. Por ahora seguimos 100% con Alpaca.

## 20. SEC EDGAR: cómo configurarlo

La API de la SEC es gratis y no necesita clave, pero **exige un User-Agent con tu nombre/correo real** en cada petición (si no, te bloquea con 403). Configúralo en tus Secrets:

```toml
SEC_EDGAR_USER_AGENT = "Tu Nombre tu_correo@ejemplo.com"
```

## 21. "Mis Tickers Manuales" — siempre visibles, sin el corte de Top 20

**El problema que resolvió esto:** el scanner automático trae hasta 30 tickers, se combinan con tu watchlist manual, se califican todos, y solo se muestran los mejores `TOP_N_CANDIDATOS` (20) por score. Eso podía dejar tus tickers manuales fuera de la tabla principal aunque sí se hubieran calificado correctamente — simplemente no alcanzaban a competir contra los 30 del scanner ese día.

**La solución:** un panel nuevo, **"📌 Mis Tickers Manuales"**, que aparece siempre debajo del screening principal (en cualquier modo) y muestra el Score real de TODOS tus tickers manuales, sin el corte de Top 20.

Desde ahí también puedes hacer **"🎯 Compra manual bajo tu propio criterio"**: eliges cualquiera de tus tickers manuales (sin importar si llega a 9.0 o no), confirmas con una casilla que es tu decisión y no una señal automática del bot, y compras — el bot sigue calculando el tamaño de posición (single-bullet o risk-based), el stop-loss dinámico y el take-profit escalonado exactamente igual que en una compra normal. Solo se salta el requisito de Score ≥ 9.

## 22. TitonSmartEngine — scoring proporcional al tamaño real de la empresa

**El problema que resuelve:** un filtro fijo como "float < 10M acciones" no distingue entre una empresa de $15M de capitalización y una de $2,000M con un float artificialmente pequeño — ambas pasarían el mismo filtro, aunque su comportamiento de mercado es completamente distinto.

**La solución (`smart_engine.py`):** un motor de scoring nuevo, opcional, que evalúa:
- **Float Market Cap** (float × precio, en dólares) — normaliza el float automáticamente por precio, en vez de solo contar acciones.
- **Market Cap Band** — puntaje completo entre $15M-$150M (banda óptima de small cap real), decayendo fuera de ese rango.
- **Float Turnover Ratio** (float / acciones totales) — mide qué tan "controlada" está la empresa por insiders.
- **RVOL Estructural** (volumen premarket / float) — la versión cuantificada del concepto de "reciclaje de acciones".
- **Catalizador + Gap**, y **Estructura de precio**.

Actívalo con:
```python
SCORING_ENGINE = "smart"   # o "classic" (por defecto, el que ya está probado en producción)
```

**Ya no hace falta editar `config.py` a mano** — arriba de la tabla de screening en el dashboard hay un selector **"🧠 Motor de Scoring"** con 3 opciones: **🅰️ Clásico**, **🆚 Comparar ambos** (por defecto), y **🅱️ Smart (proporcional)**. En modo "Comparar ambos", el dashboard corre los dos motores sobre el mismo universo de tickers y muestra las dos tablas lado a lado, para que decidas tú mismo cuál te da mejores señales antes de comprometerte a usar uno solo.

Probado con 5 escenarios sintéticos, incluyendo el caso clave: una mega-cap de $2,000M con float de 8M acciones (que el sistema clásico habría aprobado por el conteo bruto) queda correctamente degradada a 7.0/10 por el nuevo motor, al detectar que ese float es irrelevante en el contexto de una empresa tan grande.

## 23. Backtest histórico (`backtest.py`) — con su limitación documentada

Te dejé un script de backtesting completo y ejecutable, pero **no pude correrlo yo mismo** (mi entorno no tiene salida de red hacia Alpaca) — está listo para que lo corras tú en tu VS Code local con tus propias claves:

```bash
python backtest.py --tickers IMRN,CDTG,ONCO --years 2
```

**Limitación metodológica que el propio script documenta:** usa el float y las acciones en circulación ACTUALES aplicados a precios históricos — un sesgo de "look-ahead" conocido, ya que el float de una empresa hace 18 meses pudo ser distinto al de hoy. Ninguna fuente gratuita guarda float histórico point-in-time. Los componentes de Gap%, RVOL y estructura de precio sí son genuinamente históricos. Trata los resultados como orientación direccional, no como promesa de rentabilidad.



1. Correr el bot en paper trading al menos 2-3 semanas, comparando el
   ranking del screener contra tu propio criterio manual.
2. Ajustar los pesos del scoring en `screener.py` (`score_candidate`)
   según lo que veas que realmente funciona.
3. Migrar `data_fetcher.py` a una fuente de datos de pago (Polygon.io,
   Benzinga News API) cuando el bot sea consistente, tal como se detalló
   en el documento de estrategia.
