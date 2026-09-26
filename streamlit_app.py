"""Small Cap Radar V1.1 — Multi-source Streamlit Cloud app.
Scanner only: no order submission.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Small Cap Radar V1.1", page_icon="⚡", layout="wide")
from multisource_radar import scan
NY=ZoneInfo("America/New_York")

def secret(name, default=""):
    try:
        if name in st.secrets:return str(st.secrets[name])
    except Exception:pass
    return os.getenv(name,default)

cfg={
 "ALPACA_API_KEY":secret("ALPACA_API_KEY"),"ALPACA_SECRET_KEY":secret("ALPACA_SECRET_KEY"),"ALPACA_PAPER":secret("ALPACA_PAPER","True"),
 "MASSIVE_API_KEY":secret("MASSIVE_API_KEY"),"FINNHUB_API_KEY":secret("FINNHUB_API_KEY"),"TWELVE_DATA_API_KEY":secret("TWELVE_DATA_API_KEY"),"FMP_API_KEY":secret("FMP_API_KEY"),
 "OPENFDA_API_KEY":secret("OPENFDA_API_KEY"),"SEC_USER_AGENT":secret("SEC_USER_AGENT","SmallCapRadar/1.1 contact@example.com"),
}

st.title("⚡ Small Cap Radar V1.1")
st.caption("Multi-source: price + momentum + catalyst + SEC/dilution + halts + data confidence · Scanner only")
paper=str(cfg['ALPACA_PAPER']).lower() in {'true','1','yes','on'}
st.info(f"Alpaca Trading API: {'PAPER' if paper else 'LIVE'} · Market Data: IEX · Multi-source enrichment enabled")

with st.sidebar:
    st.header("⚙️ Filtros")
    min_price=st.number_input("Precio mínimo",0.01,500.0,0.50,0.10)
    max_price=st.number_input("Precio máximo",0.02,500.0,30.0,1.0)
    min_gap=st.number_input("Gap mínimo (%)",0.0,500.0,5.0,1.0)
    min_dv=st.number_input("Volumen $ mínimo",0.0,100_000_000.0,500_000.0,100_000.0,format="%.0f")
    max_float=st.number_input("Float máximo",100_000.0,5_000_000_000.0,100_000_000.0,1_000_000.0,format="%.0f")
    max_candidates=st.slider("Candidatos para deep scan",5,30,15)
    manual_raw=st.text_area("Tickers manuales (uno por línea)","")
    manual=[x.strip().upper() for x in manual_raw.splitlines() if x.strip()]
    st.divider(); st.subheader("Fuentes externas")
    st.caption("Las claves se leen de Streamlit Secrets; nunca las pongas en GitHub.")
    for n in ['MASSIVE_API_KEY','FMP_API_KEY','FINNHUB_API_KEY','TWELVE_DATA_API_KEY','OPENFDA_API_KEY']:
        st.write(('🟢 ' if cfg[n] else '⚪ ')+n.replace('_API_KEY',''))
    st.divider()
    st.subheader("Finviz / TradingView")
    st.caption("No hacemos scraping. Puedes exportar el screener desde cada servicio y subir el CSV aquí.")
    tv_file=st.file_uploader("CSV de TradingView o Finviz",type=['csv'],key='external_csv')
    st.info("V1.1 no envía órdenes. Validamos primero datos y señales en Paper Trading.")
    refresh=st.button("🔄 ESCANEAR AHORA",type='primary',use_container_width=True)

if not cfg['ALPACA_API_KEY'] or not cfg['ALPACA_SECRET_KEY']:
    st.error("Faltan ALPACA_API_KEY y ALPACA_SECRET_KEY en Streamlit → Manage app → Settings → Secrets.")
    st.stop()

if 'radar_df' not in st.session_state:st.session_state.radar_df=pd.DataFrame()
if 'radar_meta' not in st.session_state:st.session_state.radar_meta={}

if refresh or (st.session_state.radar_df.empty and not st.session_state.get('attempted',False)):
    st.session_state.attempted=True
    with st.spinner("Descubriendo candidatos con múltiples fuentes y enriqueciendo los mejores..."):
        try:
            df,meta=scan(cfg,min_price,max_price,min_gap,min_dv,max_float,max_candidates,manual)
            st.session_state.radar_df=df;st.session_state.radar_meta=meta
        except Exception as e:
            st.error(f"Error de escaneo: {type(e).__name__}: {e}")
            st.warning("El escaneo no reintenta en bucle. Revisa la fuente indicada y pulsa ESCANEAR AHORA.")
            st.stop()

df=st.session_state.radar_df;meta=st.session_state.radar_meta

# Optional external screener upload: enrich/watchlist only; it never overrides market data.
if tv_file is not None:
    try:
        ext=pd.read_csv(tv_file); ext.columns=[str(c).strip() for c in ext.columns]
        possible=[c for c in ext.columns if str(c).lower() in {'ticker','symbol'}]
        if possible:
            syms=set(ext[possible[0]].astype(str).str.upper().str.strip())
            if not df.empty:
                df['External Screener']=df['symbol'].map(lambda s:'TradingView/Finviz' if s in syms else '')
            st.success(f"CSV externo cargado: {len(syms)} símbolos. Se usa como contexto/watchlist, no como fuente de precio primaria.")
        else: st.warning("No encontré una columna Ticker/Symbol en el CSV.")
    except Exception as e: st.warning(f"No pude leer el CSV externo: {e}")

m1,m2,m3,m4=st.columns(4)
m1.metric('Candidatos',len(df));m2.metric('Universe elegible',meta.get('universe_assets',0));m3.metric('Descubiertos',meta.get('discovery_candidates',0));m4.metric('Tiempo',f"{meta.get('elapsed_sec',0)}s")

with st.expander('🔌 Estado de fuentes',expanded=True):
    ss=meta.get('source_status',{});cols=st.columns(8)
    names=['massive','fmp','alpaca','alpaca_snapshots','alpaca_bars','finnhub','twelvedata','sec']
    labels=['Massive','FMP','Alpaca','Snapshots','Bars','Finnhub','TwelveData','SEC']
    for col,n,l in zip(cols,names,labels):col.metric(l,'OK' if ss.get(n) else 'OFF')
    st.caption(' · '.join([f"{k}: {'OK' if v else 'OFF'}" for k,v in ss.items()]))
    st.caption(meta.get('rate_limit_strategy',''))

if df.empty:
    st.warning('No hubo candidatos con estos filtros. Prueba Gap 3–5%, volumen $300k–$500k o precio más amplio.')
    st.stop()

st.subheader('🎯 Radar multi-fuente')
cols=['symbol','name','exchange','price','gap_pct','volume','rvol','intraday_rvol','volume_accel','spread_pct','float_shares','market_cap','vwap','pmh','rsi','adx','catalyst_score','sec_risk_score','short_volume_ratio','clinical_hits','fda_hits','data_confidence','score','setup']
view=df[[c for c in cols if c in df.columns]].copy()
for c in ['price','vwap','pmh','stop','tp1','tp2']:
    if c in view:view[c]=view[c].round(4)
for c in ['gap_pct','spread_pct','catalyst_score','sec_risk_score','short_volume_ratio','data_confidence','score']:
    if c in view:view[c]=view[c].round(1)
st.dataframe(view,use_container_width=True,hide_index=True)

st.subheader('🔎 Ficha del candidato')
sym=st.selectbox('Ticker',df.symbol.tolist());r=df[df.symbol==sym].iloc[0]
a,b,c,d,e=st.columns(5)
a.metric('Score',f"{r.score:.1f}/100");b.metric('Data confidence',f"{r.data_confidence:.0f}%");c.metric('Gap',f"{r.gap_pct:+.1f}%");d.metric('Setup',r.setup);e.metric('Fuentes',int(r.source_count))
x1,x2,x3,x4=st.columns(4)
x1.metric('Precio',f"${r.price:.4f}");x2.metric('VWAP',f"${r.vwap:.4f}" if pd.notna(r.vwap) else '—');x3.metric('PMH',f"${r.pmh:.4f}" if pd.notna(r.pmh) else '—');x4.metric('Spread',f"{r.spread_pct:.2f}%" if pd.notna(r.spread_pct) else '—')
st.write(f"**Empresa:** {r.get('name','')}")
st.write(f"**Catalizador:** {r.catalyst_text or 'No identificado'}")
st.write(f"**SEC flags:** {r.sec_flags or 'Sin bandera reciente'}")
st.write(f"**Halt:** {'SÍ' if r.halt else 'No'}")
st.write(f"**Short-sale volume:** {r.short_volume_ratio:.1f}%" if pd.notna(r.short_volume_ratio) else '**Short-sale volume:** sin dato')
st.write(f"**ClinicalTrials:** {int(r.clinical_hits)} coincidencias · **FDA:** {int(r.fda_hits)} coincidencias")
st.write(f"**Fuentes usadas:** {r.source_notes}")
if r.catalyst_url:st.markdown(f"[Abrir catalizador/noticia]({r.catalyst_url})")

st.subheader('📌 Plan matemático del setup — NO es una orden')
p1,p2,p3=st.columns(3);p1.metric('Zona entrada',r.entry_zone or '—');p2.metric('Stop técnico',f"${r.stop:.4f}" if pd.notna(r.stop) else '—');p3.metric('TP1 / TP2',f"${r.tp1:.4f} / ${r.tp2:.4f}" if pd.notna(r.tp1) else '—')

with st.expander('🧠 Arquitectura de datos'):
    st.write('Descubrimiento: Massive/FMP/Alpaca → filtro precio-gap-volumen → snapshots Alpaca solo para candidatos → barras 1m → enriquecimiento SEC/Finnhub/Twelve Data/FMP → halts Nasdaq → contexto FINRA → ClinicalTrials/openFDA para perfiles biotech cuando hay nombre de empresa → score y data confidence.')
with st.expander('⚠️ Finviz y TradingView'):
    st.write('V1.1 no raspa sus páginas ni depende de endpoints internos. Finviz Elite permite exportar datos de screener y TradingView permite exportar resultados del Screener; el CSV puede cargarse arriba como contexto/watchlist. La fuente primaria de precio sigue siendo una API de mercado.')
with st.expander('🛡️ Limitaciones'):
    st.write('Ninguna fuente gratuita debe interpretarse como cinta consolidada por defecto. Massive puede aportar cobertura mucho más amplia según el plan; Alpaca Basic/IEX no equivale a SIP. Finnhub/Twelve Data/FMP tienen sus propios límites. El score es un filtro técnico, no una probabilidad de ganar.')

st.caption(f"Último escaneo: {datetime.now(NY).strftime('%Y-%m-%d %H:%M:%S ET')}")
