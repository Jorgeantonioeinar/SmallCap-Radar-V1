# V7.6.8 — Importación fiel de reportes Moomoo por sesión

El parser identifica cada métrica por el encabezado, no por la posición de la columna. Se probaron dos exportaciones reales: regular (16 tickers/32 columnas) y premarket (13 tickers/33 columnas). La variante premarket con `Pre Mkt Stock Price` y `Pre Mkt % Chg` ahora elige el Gap porcentual correcto sobre `% Chg`; `Chg` se conserva como cambio en dólares. También se reconocen `Mkt Cap`, `Vol Ratio` y las variantes regulares de precio/volumen. Se corrigió además la exclusión equivocada del ticker válido `CSV`.

Se conservan las columnas disponibles y el panel desplegable muestra los valores importados. Hay alias para encabezados habituales de premarket y after-hours (`Pre Mkt`, `After Hours`, `Post Mkt`, `AH`); para after-hours se probó un layout sintético porque aún no se ha recibido un export real de esa sesión. Las fórmulas vigentes de scoring no se alteran: se usan Gap, RVOL, precio, float y los fallbacks compatibles con cada motor; el resto queda como contexto visible.

La compilación y 6 pruebas automatizadas pasan. Dependencias añadidas para Excel: `openpyxl` y `xlrd`.
