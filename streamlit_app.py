"""Small Cap Radar — Streamlit Cloud entrypoint.

Cloud V1 is scanner-only: no automatic order submission. The legacy execution
engine remains in the repository for later controlled paper-trading work.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Small Cap Radar V1.0", page_icon="⚡", layout="wide")

from cloud_radar import scan

NY=ZoneInfo("America/New_York")

def secret(name, default=""):
    try:
        if name in st.secrets: return str(st.secrets[name])
    except Exception: pass
    return os.getenv(name, default)

cfg={
    "ALPACA_API_KEY": secret("ALPACA_API_KEY"),
    "ALPACA_SECRET_KEY": secret("ALPACA_SECRET_KEY"),
    "FMP_API_KEY": secret("FMP_API_KEY"),
    "TWELVE_DATA_API_KEY": secret("TWELVE_DATA_API_KEY"),
    "SEC_USER_AGENT": secret("SEC_USER_AGENT", "SmallCapRadar/1.0 contact@example.com"),
}

st.markdown("""<style>.block-container{max-width:100%;padding-top:1rem}.metric-card{padding:.8rem;border:1px solid #333;border-radius:.6rem}</style>""",unsafe_allow_html=True)
st.title("⚡ Small Cap Radar V1.0")
st.caption("Gap + momentum + catalyst + dilution/SEC risk + halts + data confidence · Scanner only")

if not cfg["ALPACA_API_KEY"] or not cfg["ALPACA_SECRET_KEY"]:
    st.warning("Configura ALPACA_API_KEY y ALPACA_SECRET_KEY en Streamlit Cloud → Settings → Secrets. No pongas las claves en GitHub.")
    st.code('ALPACA_API_KEY = "..."\nALPACA_SECRET_KEY = "..."\nALPACA_PAPER = "True"\nSEC_USER_AGENT = "SmallCapRadar/1.0 contacto@tudominio.com"')
    st.stop()

with st.sidebar:
    st.header("⚙️ Filtros")
    min_price=st.number_input("Precio mínimo",0.01,500.0,0.50,0.10)
    max_price=st.number_input("Precio máximo",0.02,500.0,30.0,1.0)
    min_gap=st.number_input("Gap mínimo (%)",0.0,500.0,5.0,1.0)
    min_dv=st.number_input("Volumen $ mínimo",0.0,100_000_000.0,500_000.0,100_000.0,format="%.0f")
    max_float=st.number_input("Float máximo",100_000.0,5_000_000_000.0,100_000_000.0,1_000_000.0,format="%.0f")
    max_candidates=st.slider("Candidatos para deep scan",5,40,25)
    manual_raw=st.text_area("Tickers manuales (uno por línea)","")
    manual=[x.strip().upper() for x in manual_raw.splitlines() if x.strip()]
    st.divider()
    st.info("V1 no envía órdenes. Primero validamos el radar y sus datos en Paper Trading.")
    refresh=st.button("🔄 ESCANEAR AHORA",type="primary",use_container_width=True)

if "radar_df" not in st.session_state: st.session_state.radar_df=pd.DataFrame()
if "radar_meta" not in st.session_state: st.session_state.radar_meta={}

if refresh or st.session_state.radar_df.empty:
    with st.spinner("Escaneando universo NYSE/Nasdaq/AMEX/ARCA y enriqueciendo candidatos..."):
        try:
            df,meta=scan(cfg,min_price,max_price,min_gap,min_dv,max_float,max_candidates,manual)
            st.session_state.radar_df=df; st.session_state.radar_meta=meta
        except Exception as e:
            st.error(f"Error de escaneo: {e}")
            st.stop()

df=st.session_state.radar_df; meta=st.session_state.radar_meta

m1,m2,m3,m4=st.columns(4)
m1.metric("Candidatos",len(df)); m2.metric("Universe",meta.get("universe_assets",0)); m3.metric("Deep scan",meta.get("snapshot_candidates",0)); m4.metric("Tiempo",f"{meta.get('elapsed_sec',0)}s")

if df.empty:
    st.info("No hubo candidatos con estos filtros. Baja Gap/volumen o amplía el rango de precio.")
    st.stop()

st.subheader("🎯 Radar")
cols=["symbol","price","gap_pct","rvol","intraday_rvol","volume_accel","spread_pct","float_shares","vwap","pmh","rsi","adx","catalyst_score","sec_risk_score","data_confidence","score","setup"]
show=[c for c in cols if c in df.columns]
view=df[show].copy()
for c in ["price","vwap","pmh"]:
    if c in view: view[c]=view[c].round(4)
for c in ["gap_pct","spread_pct","catalyst_score","sec_risk_score","data_confidence","score"]:
    if c in view: view[c]=view[c].round(1)
st.dataframe(view,use_container_width=True,hide_index=True)

st.subheader("🔎 Ficha del candidato")
sym=st.selectbox("Ticker",df.symbol.tolist())
r=df[df.symbol==sym].iloc[0]
a,b,c,d=st.columns(4)
a.metric("Score",f"{r.score:.1f}/100"); b.metric("Data confidence",f"{r.data_confidence:.0f}%"); c.metric("Gap",f"{r.gap_pct:+.1f}%"); d.metric("Setup",r.setup)

x1,x2,x3,x4=st.columns(4)
x1.metric("Precio",f"${r.price:.4f}"); x2.metric("VWAP",f"${r.vwap:.4f}" if pd.notna(r.vwap) else "—"); x3.metric("PMH",f"${r.pmh:.4f}" if pd.notna(r.pmh) else "—"); x4.metric("Spread",f"{r.spread_pct:.2f}%" if pd.notna(r.spread_pct) else "—")

st.write(f"**Catalizador:** {r.catalyst_text or 'No identificado'}")
st.write(f"**SEC flags:** {r.sec_flags or 'Sin bandera reciente'}")
st.write(f"**Halt:** {'SÍ' if r.halt else 'No'}")
st.write(f"**Fuentes:** {r.source_notes}")

if r.catalyst_url:
    st.markdown(f"[Abrir noticia/catalizador]({r.catalyst_url})")

st.subheader("📌 Plan matemático del setup — NO es una orden")
p1,p2,p3=st.columns(3)
p1.metric("Zona entrada",r.entry_zone or "—"); p2.metric("Stop técnico",f"${r.stop:.4f}" if pd.notna(r.stop) else "—"); p3.metric("TP1 / TP2",f"${r.tp1:.4f} / ${r.tp2:.4f}" if pd.notna(r.tp1) else "—")

with st.expander("🧠 Cómo se calcula el score"):
    st.write("Gap 15 · RVOL 15 · aceleración volumen 10 · aceleración precio 10 · VWAP 10 · PMH 10 · float 8 · spread 7 · catalyst 10 · riesgo SEC 5, con pequeñas bonificaciones técnicas. El score no es una probabilidad de ganar.")

with st.expander("🛡️ Calidad y limitaciones"):
    st.write("Alpaca se consulta con feed IEX. No se presenta como volumen consolidado de todas las bolsas. RVOL es proxy si no hay histórico comparable. Float depende de FMP opcional. SEC/noticias/halts fallan de forma no destructiva. GitHub/Streamlit sirven para screening y validación, no para scalping de latencia ultra baja.")

st.caption(f"Último escaneo: {datetime.now(NY).strftime('%Y-%m-%d %H:%M:%S ET')}")
