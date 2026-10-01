# IBKR TWS como fuente primaria + failover Alpaca

## Cómo lo usas (diseño real)

| Situación | Qué usa el bot |
|-----------|----------------|
| **PC encendida + TWS Paper abierto** (puerto 7497) | **IBKR** = datos primarios (precio, barras, scanner) |
| **PC sin TWS**, o **Streamlit Cloud / celular** solo para **ver** datos | **Alpaca + Finviz/Yahoo** automáticamente (failover) |
| **Operar** | Siempre con la PC (TWS y/o TradeZero); el móvil es solo consulta |

No hace falta operar desde el teléfono. El failover existe para que **puedas mirar el panel en el móvil** cuando TWS no está en esa máquina (la nube nunca puede hablar con `127.0.0.1` de tu casa).

## Configuración TWS Paper

1. Abre **TWS** o **IB Gateway** en modo **Paper**.
2. Activa API:
   - File → Global Configuration → API → Settings
   - Enable ActiveX and Socket Clients
   - Socket port = **7497** (paper)
   - Trusted IPs: 127.0.0.1
3. Cuenta paper: **DUR216049** (en `.env` como `IBKR_ACCOUNT`)
4. En el bot (`.env`):

```env
IBKR_ENABLED=true
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
IBKR_ACCOUNT=DUR216049
IBKR_CONNECT_TIMEOUT=3
```

5. Dependencia: `pip install ib_insync`

## Comprobar

```bat
cd D:\C\Desktop\BOTS\smallcaps_bot
.venv\Scripts\activate
python -c "from market_data_manager import get_market_data_manager; m=get_market_data_manager(); print(m.get_status())"
```

- Con TWS abierto: `"active": "ibkr"`
- Con TWS cerrado: `"active": "alpaca"` (o fuentes web)

## GitHub / Streamlit Cloud

El mismo código se sube a GitHub. En la nube **siempre** hará failover (no hay TWS). El screening, Halt, Short, Scalp LISTO siguen funcionando con Alpaca/Finviz.
