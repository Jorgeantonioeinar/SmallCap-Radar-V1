# Small Cap Radar V1.1.4 — Scoring de scalping e importación Moomoo

## Cambios principales

- El motor **Classic**, predeterminado y con `EXIT_MODE = "scalping"`, reserva hasta **3.5/10 puntos al Gap** y **3.5/10 al volumen/RVOL**. Float y RSI aportan los puntos restantes; el score máximo continúa en 10. El perfil `swing` conserva sus pesos anteriores.
- Gap y RVOL usan umbrales de la sesión activa. El motor **Smart** también adopta los límites de precio configurados por la app (`PRICE_MIN`/`PRICE_MAX`) y el Gap mínimo de regular, premarket o after-hours, sin modificar una configuración global compartida.
- El precio de la fila de Moomoo se etiqueta como `regular`, `premarket` o `afterhours`. Para puntuar, se usa como snapshot cuando coincide con la sesión activa; si no coincide, se prefiere el precio vivo y se agrega una nota. Si no hay precio vivo válido, el precio importado queda como respaldo.
- No se suman dos veces métricas de volumen. Si el RVOL falta, es cero o queda bajo el mínimo de la sesión, el volumen de sesión/float puede sustituirlo cuando ambos datos existen. Sin float válido no se fabrica puntuación de turnover. Smart muestra el mismo volumen/float que usa para puntuar.

## Importación Moomoo

- Identificación por encabezados —no por posición— para CSV/TXT, XLSX y XLS; tolera columnas reordenadas y conserva las columnas fuente reconocidas y las adicionales disponibles en `momo_data`.
- Distingue el cambio porcentual de `Chg` monetario. El Gap de sesión viene de `Post Mkt % Chg`/`After Hours % Chg`, `Pre Mkt % Chg` o `% Chg`; si falta el porcentaje de sesión pero existe precio de sesión y `Prev Close`, calcula ese Gap antes de caer al cambio regular.
- Reconoce el encabezado real `Post Mkt Stock Price` y lo utiliza como precio after-hours; no confunde ese snapshot con `Price` regular.
- En una columna explícita `Symbol`, acepta símbolos válidos aunque coincidan con palabras comunes o tengan una sola letra; la lista de palabras de descarte continúa aplicándose al texto libre, no a datos de ticker estructurados.

## Verificación con los archivos reales adjuntos

| Reporte | Filas importadas | Columnas de origen | Cobertura Gap, precio, RVOL, volumen y market cap |
|---|---:|---:|---:|
| Regular | 16/16 | 32 | 16/16 en cada campo |
| Premarket | 13/13 | 33 | 13/13 en cada campo |
| After-hours / Post Mkt | 51/51 | 33 | 51/51 en cada campo |

En el post-market, la primera fila AGRZ se importó con `Price` regular **$3.25**, `Post Mkt Stock Price` **$3.47**, Gap post-market **6.77%**, volumen **21,258**, y capitalización **$6,151,191**. El export premarket no traía una columna dedicada de volumen PM; por eso el parser usa su `Volume` genérico y no inventa un dato separado.

## Pruebas y origen

- `python3 -m compileall -q .`: correcto.
- `python3 -m unittest discover -s tests -v`: **14 pruebas correctas**, incluida lectura XLSX, las tres sesiones, encabezados reordenados, símbolos explícitos y pruebas de scoring sin llamadas de red.
- Basado en el repositorio público `https://github.com/Jorgeantonioeinar/SmallCap-Radar-V1`, commit `381c4dae7713441fa9f839ebef306764c8ccb4d7`. Los cambios de esta entrega son locales; no se publicó nada en GitHub.

## Límite importante

No existe una promesa honesta de que un sistema de mercado sea «100 % a prueba de fallos»: Moomoo puede cambiar encabezados o formato, los feeds pueden estar retrasados y los reportes son snapshots. El score clasifica candidatos, **no es una orden ni una garantía de resultado**. Antes de operar con dinero real, validar con paper trading y revisar spreads, liquidez, slippage, halts, calidad de datos y diferencias entre la hora del export y la sesión activa.
