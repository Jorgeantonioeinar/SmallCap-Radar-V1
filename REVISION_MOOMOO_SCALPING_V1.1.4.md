# Auditoría de Moomoo y scoring de scalping — V1.1.4

## Conclusión

El importador ya reconoce los tres formatos de exportación adjuntos (horario regular, premarket y after-hours/Post Mkt) por los **nombres de columna**, no por el orden. Se comprobó que no reduce el archivo a una lista de tickers: entrega Gap, precio, RVOL, volumen, capitalización y los demás valores utilizables al scoring, además de conservar la fila fuente para inspección.

La corrección más importante del último archivo fue reconocer `Post Mkt Stock Price`. En el export after-hours, el precio genérico era **$3.25** y el precio post-market **$3.47**; antes de la corrección el hint de precio podía tomar el valor genérico. Ahora ambos se conservan con su significado, y `3.47` se elige como precio de sesión after-hours.

## Causas y correcciones

1. **El orden de columnas no es una identidad estable.** El parser normaliza encabezados y asigna cada métrica por alias; una columna movida no cambia su significado.
2. **Cambio monetario y cambio porcentual son distintos.** `Chg` no se trata como `% Chg`. Para post-market se prefiere el porcentaje after-hours; si falta, se deriva desde el precio de esa sesión y `Prev Close` antes de usar el cambio regular.
3. **Alias del precio after-hours incompleto.** Se añadió `Post Mkt Stock Price` y sus variantes habituales; la sesión origen queda marcada en `price_session`.
4. **Snapshots de sesiones distintas.** Un precio importado solo sustituye al feed en la calificación si la sesión del archivo coincide con la sesión activa. En caso contrario se conserva la cotización viva y la tabla de notas explica la decisión; si no existe una cotización viva válida, se permite el respaldo importado.
5. **Símbolos que son palabras comunes.** En la columna explícita `Symbol` ya no se aplica el filtro de palabras usado para texto libre; así se conservan símbolos como `ALL`, `BIO`, `CSV` y tickers de una letra si cumplen el formato admitido.
6. **RVOL after-hours bajo pero positivo.** En Classic, un RVOL ausente, cero o menor que el umbral de la sesión permite usar `volumen de sesión / float` como alternativa cuando se dispone de ambos; nunca se suma dos veces. Si el float no está disponible, no se inventa ese componente.
7. **Smart y escalas de la aplicación.** Smart recibe el mínimo de Gap de la sesión activa y los límites globales `PRICE_MIN`/`PRICE_MAX` mediante una configuración por llamada, sin mutar un motor compartido. El volumen mostrado se alinea con la relación sesión/float realmente puntuada.

## Archivos reales contrastados

| Export | Símbolos importados | Columnas fuente | Gap | Precio | RVOL | Volumen | Market cap |
|---|---:|---:|---:|---:|---:|---:|---:|
| Regular `20261001215620.csv` | 16/16 | 32 | 16/16 | 16/16 | 16/16 | 16/16 | 16/16 |
| Premarket `20261001210008.csv` | 13/13 | 33 | 13/13 | 13/13 | 13/13 | 13/13 | 13/13 |
| Post-market `20261003062811.csv` | 51/51 | 33 | 51/51 | 51/51 | 51/51 | 51/51 | 51/51 |

Ejemplo contrastado del Post Mkt para AGRZ:

- `Price` regular: **$3.25**
- `Post Mkt Stock Price`: **$3.47**
- `% Chg` regular: **0.62 %**
- `Post Mkt % Chg`: **6.77 %** — Gap que alimenta el importador
- `Volume`: **21,258**; `Vol Ratio`: **0.03**; `Mkt Cap`: **$6,151,191**
- Sesión asociada al precio hint: `afterhours`

El archivo premarket no incluye volumen PM dedicado; se importa el `Volume` genérico. No se crea una columna PM ficticia. Los archivos de origen no se incorporan al ZIP del programa.

## Scoring y límites

El perfil predeterminado `EXIT_MODE = "scalping"` de Classic da hasta 3.5 puntos a Gap y 3.5 a volumen/RVOL (70 % del máximo), más los componentes de float y RSI. La escala total continúa acotada a 10. El volumen se interpreta con la base indicada en el resultado: RVOL o sesión/float. No tener float puede dejar puntos de volumen sin otorgar —es preferible a una estimación inventada.

El puntaje es un filtro de candidatos, no una probabilidad de ganancia ni una instrucción para comprar. Los valores de un archivo son una instantánea: para scalping hay que confirmar que el export corresponde a la sesión actual y que precio/volumen siguen vigentes.

## Validación ejecutada

- Compilación completa de Python con `compileall`.
- **14 pruebas unitarias correctas**: parser CSV y XLSX, porcentajes, orden variable, regular/premarket/after-hours, `Post Mkt Stock Price`, derivación de Gap, símbolos explícitos, pesos de score y routing Smart/Classic con fuentes simuladas sin red.
- Repositorio base: `Jorgeantonioeinar/SmallCap-Radar-V1`, commit `381c4dae7713441fa9f839ebf306764c8ccb4d7`. No se hicieron cambios remotos ni se publicó en GitHub.

## Uso

1. En Streamlit, carga el CSV/Excel de la sesión en la sección de importación y pulsa **Cargar lista a watchlist**.
2. Selecciona **Manual** y el screening **Rápido** si ese es tu flujo; después pulsa **Calificar**.
3. Verifica las columnas de origen en el panel de datos Moomoo y la procedencia del precio/RVOL en las notas de cada candidato. Si la sesión del archivo y la sesión activa no coinciden, no interpretes el score como una lectura en vivo de esa sesión.
4. Prueba primero en paper; el código no puede garantizar que cualquier export futuro de Moomoo conserve estos mismos encabezados o valores.
