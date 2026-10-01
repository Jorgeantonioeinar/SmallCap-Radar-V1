# V7.6 — Potencia y claridad

## Cambios
1. **RSI / Entry / Estado siempre que sea posible** aunque el ticker se descarte
   (float bajo, dilución, sin precio, halt). Ya no quedan filas en "None" vacías.
2. **Halt**: sigue forzando DESCARTAR / Scalp NO, pero muestra métricas si hay barras.
3. **UI**: valores vacíos se muestran como **N/D**.
4. **IBKR**: solo si `IBKR_FORCE=true` en PC local (nube = Alpaca/web, sin bucle).
5. **TradeZero client** incluido en paquete local (ejecución paper, no market data).

## Cómo desplegar
- Nube: subir ZIP cloud → Redeploy
- PC: descomprimir sobre `smallcaps_bot`, `pip install -r requirements.txt`,
  opcional `IBKR_FORCE=true` solo con TWS paper en 7497
