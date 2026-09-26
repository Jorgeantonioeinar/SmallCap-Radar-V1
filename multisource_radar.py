"""Multi-source Small Cap Radar V1.1.

Scanner-only architecture for Streamlit Cloud.  It combines independent sources
without treating any single free feed as consolidated market truth.
"""
from __future__ import annotations

import re, time, math
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


class HTTP:
    def __init__(self, timeout=15, min_interval=0.20):
        self.timeout=timeout; self.min_interval=min_interval; self._last=0.0
        self.s=requests.Session(); self.s.headers.update({"User-Agent":"SmallCapRadar/1.1"})
    def _pace(self):
        w=self.min_interval-(time.monotonic()-self._last)
        if w>0: time.sleep(w)
    def request(self, method, url, headers=None, params=None, json=None, retries=3):
        last=None
        for attempt in range(retries+1):
            self._pace()
            try:
                r=self.s.request(method,url,headers=headers,params=params,json=json,timeout=self.timeout)
                self._last=time.monotonic()
                if r.status_code==429:
                    if attempt>=retries: return r
                    ra=r.headers.get("Retry-After")
                    try: d=float(ra) if ra else min(8,2**attempt)
                    except Exception: d=min(8,2**attempt)
                    time.sleep(max(1,min(d,20))); continue
                if r.status_code in (500,502,503,504) and attempt<retries:
                    time.sleep(min(8,2**attempt)); continue
                return r
            except requests.RequestException as e:
                last=e
                if attempt>=retries: raise
                time.sleep(min(8,2**attempt))
        raise last or RuntimeError("HTTP request failed")
    def json(self,url,headers=None,params=None,**kw):
        r=self.request("GET",url,headers=headers,params=params,**kw); r.raise_for_status(); return r.json()
    def text(self,url,headers=None,params=None,**kw):
        r=self.request("GET",url,headers=headers,params=params,**kw); r.raise_for_status(); return r.text
    def post_json(self,url,headers=None,payload=None,**kw):
        r=self.request("POST",url,headers=headers,json=payload,**kw); r.raise_for_status(); return r.json()


@dataclass
class Candidate:
    symbol:str
    name:str=""
    exchange:str=""
    price:float=0; prev_close:float=0; gap_pct:float=0; volume:float=0; dollar_volume:float=0
    bid:Optional[float]=None; ask:Optional[float]=None; spread_pct:Optional[float]=None
    float_shares:Optional[float]=None; shares_outstanding:Optional[float]=None; market_cap:Optional[float]=None
    rvol:Optional[float]=None; intraday_rvol:Optional[float]=None; volume_accel:Optional[float]=None; price_accel_1m:Optional[float]=None
    vwap:Optional[float]=None; vwap_slope:Optional[float]=None; ema9:Optional[float]=None; ema20:Optional[float]=None; ema50:Optional[float]=None
    rsi:Optional[float]=None; atr:Optional[float]=None; adx:Optional[float]=None; bollinger_upper:Optional[float]=None; bollinger_lower:Optional[float]=None
    pmh:Optional[float]=None; pml:Optional[float]=None; hod:Optional[float]=None
    catalyst_score:float=0; catalyst_count:int=0; catalyst_text:str=""; catalyst_url:str=""
    sec_risk_score:float=0; sec_flags:str=""; short_volume_ratio:Optional[float]=None; halt:bool=False; halt_reason:str=""
    finnhub_sentiment:Optional[float]=None; clinical_hits:int=0; fda_hits:int=0
    source_count:int=0; data_confidence:float=0; score:float=0; setup:str="NO_SETUP"
    entry_zone:str=""; stop:Optional[float]=None; tp1:Optional[float]=None; tp2:Optional[float]=None; source_notes:str=""
    def to_dict(self): return asdict(self)


def ema(s,n): return float(s.ewm(span=n,adjust=False).mean().iloc[-1]) if len(s)>=n else None

def rsi(s,n=14):
    if len(s)<n+1:return None
    d=s.diff(); g=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); l=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); rs=g/l.replace(0,np.nan)
    return float((100-100/(1+rs)).iloc[-1]) if pd.notna(rs.iloc[-1]) else 100.0

def atr(df,n=14):
    if len(df)<n+1:return None
    p=df.close.shift(1); tr=pd.concat([df.high-df.low,(df.high-p).abs(),(df.low-p).abs()],axis=1).max(axis=1)
    return float(tr.ewm(alpha=1/n,adjust=False).mean().iloc[-1])

def adx(df,n=14):
    if len(df)<2*n+2:return None
    up=df.high.diff(); dn=-df.low.diff(); plus=up.where((up>dn)&(up>0),0.0); minus=dn.where((dn>up)&(dn>0),0.0); p=df.close.shift(1)
    tr=pd.concat([df.high-df.low,(df.high-p).abs(),(df.low-p).abs()],axis=1).max(axis=1); a=tr.ewm(alpha=1/n,adjust=False).mean()
    pdi=100*plus.ewm(alpha=1/n,adjust=False).mean()/a; mdi=100*minus.ewm(alpha=1/n,adjust=False).mean()/a
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    v=dx.ewm(alpha=1/n,adjust=False).mean().iloc[-1]; return float(v) if pd.notna(v) else None

def bar_features(df):
    if df is None or df.empty:return {}
    d=df.copy()
    for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.dropna(subset=["close"]).sort_values("timestamp")
    if d.empty:return {}
    d["ny"]=pd.to_datetime(d.timestamp,utc=True).dt.tz_convert(NY)
    reg=d[(d.ny.dt.time>=pd.Timestamp("09:30").time())&(d.ny.dt.time<=pd.Timestamp("16:00").time())]
    pre=d[(d.ny.dt.time>=pd.Timestamp("04:00").time())&(d.ny.dt.time<pd.Timestamp("09:30").time())]
    x=reg if len(reg)>=10 else d; c=x.close; v=x.volume; typ=(x.high+x.low+x.close)/3
    vw=float((typ*v).sum()/v.sum()) if v.sum() else None
    pv=x.iloc[:-5] if len(x)>=6 else x.iloc[:0]; pvw=float((((pv.high+pv.low+pv.close)/3)*pv.volume).sum()/pv.volume.sum()) if pv.volume.sum() else None
    base=float(v.iloc[-30:-5].mean()) if len(v)>=30 else None
    ma=c.rolling(20).mean(); sd=c.rolling(20).std()
    return {"vwap":vw,"vwap_slope":((vw-pvw)/pvw*100 if vw and pvw else 0),"ema9":ema(c,9),"ema20":ema(c,20),"ema50":ema(c,50),"rsi":rsi(c),"atr":atr(x),"adx":adx(x),"hod":float(x.high.max()),"pmh":float(pre.high.max()) if not pre.empty else None,"pml":float(pre.low.min()) if not pre.empty else None,"price_accel_1m":float((c.iloc[-1]/c.iloc[-2]-1)*100) if len(c)>1 else None,"volume_accel":float(v.iloc[-1]/base) if base and base>0 else None,"intraday_rvol":float(v.iloc[-5:].mean()/v.iloc[-30:-5].mean()) if len(v)>=30 and v.iloc[-30:-5].mean()>0 else None,"bollinger_upper":float((ma+2*sd).iloc[-1]) if len(c)>=20 else None,"bollinger_lower":float((ma-2*sd).iloc[-1]) if len(c)>=20 else None}


def clip(x,a=0,b=1): return max(a,min(b,float(x)))

def score(c):
    s=15*clip(c.gap_pct/30)+15*clip((c.rvol or 0)/8)+10*clip((c.volume_accel or 0)/3)+10*clip(max(c.price_accel_1m or 0,0)/3)
    if c.vwap and c.price>c.vwap:s+=10*(1 if (c.vwap_slope or 0)>=0 else .6)
    if c.pmh: s+=10 if c.price>=c.pmh*.995 else (5 if c.price>=c.pmh*.97 else 0)
    if c.float_shares:s+=8*clip(20_000_000/c.float_shares)
    if c.spread_pct is not None:s+=7*clip(1-c.spread_pct/2)
    s+=8*clip((c.catalyst_score+2)/4)
    s+=4*clip((c.source_count)/5)
    if c.ema9 and c.ema20 and c.price>c.ema9>c.ema20:s+=2
    if c.ema20 and c.ema50 and c.ema20>c.ema50:s+=1
    if c.rsi is not None and 50<=c.rsi<=78:s+=2
    if c.adx is not None and c.adx>=20:s+=2
    s+=5*(1-clip(c.sec_risk_score/100))
    if c.halt:s*=.25
    if c.data_confidence<70:s*=.85
    return round(clip(s/100)*100,1)

def setup(c):
    if c.halt:return "HALT"
    if c.sec_risk_score>=60:return "DILUTION_RISK"
    if c.data_confidence<70:return "LOW_CONFIDENCE"
    if c.score>=75:
        if c.pmh and c.price>=c.pmh*.995:return "PMH_BREAKOUT"
        if c.vwap and c.price>c.vwap:return "VWAP_MOMENTUM"
        return "MOMENTUM"
    return "WATCH" if c.score>=60 else "NO_SETUP"

def confidence(c):
    checks=[c.price>0,c.prev_close>0,c.volume>0,c.bid is not None and c.ask is not None,c.vwap is not None,c.ema9 is not None,c.rsi is not None,c.atr is not None,c.pmh is not None,c.source_count>=2,c.sec_flags!="SEC unavailable"]
    w=[12,8,10,8,10,8,7,8,7,12,10]
    return round(sum(a for ok,a in zip(checks,w) if ok)/sum(w)*100,1)


class Alpaca:
    DATA="https://data.alpaca.markets"; TRADING_LIVE="https://api.alpaca.markets/v2"; TRADING_PAPER="https://paper-api.alpaca.markets/v2"
    def __init__(self,key,secret,paper,http):self.h={"APCA-API-KEY-ID":key,"APCA-API-SECRET-KEY":secret};self.http=http;self.trading=self.TRADING_PAPER if paper else self.TRADING_LIVE
    def assets(self):return self.http.json(f"{self.trading}/assets",headers=self.h,params={"status":"active","asset_class":"us_equity"})
    def movers(self,top=50):return self.http.json(f"{self.DATA}/v1beta1/screener/stocks/movers",headers=self.h,params={"top":top})
    def active(self,top=100):return self.http.json(f"{self.DATA}/v1beta1/screener/stocks/most-actives",headers=self.h,params={"top":top,"by":"volume"})
    def snapshots(self,syms):
        if not syms:return {}
        out={}
        for i in range(0,len(syms),100):out.update(self.http.json(f"{self.DATA}/v2/stocks/snapshots",headers=self.h,params={"symbols":",".join(syms[i:i+100]),"feed":"iex"}) or {})
        return out
    def bars(self,syms,start,end):
        if not syms:return {}
        j=self.http.json(f"{self.DATA}/v2/stocks/bars",headers=self.h,params={"symbols":",".join(syms),"timeframe":"1Min","start":start.astimezone(UTC).isoformat().replace('+00:00','Z'),"end":end.astimezone(UTC).isoformat().replace('+00:00','Z'),"limit":10000,"feed":"iex","adjustment":"raw"})
        return {k:pd.DataFrame(v) for k,v in (j.get("bars") or {}).items()}
    @staticmethod
    def symbols(payload):
        out=[]
        def walk(x):
            if isinstance(x,dict):
                for k,v in x.items():
                    if k.lower() in {"symbol","ticker"} and isinstance(v,str):out.append(v.upper())
                    else:walk(v)
            elif isinstance(x,list):
                for v in x:walk(v)
        walk(payload);return list(dict.fromkeys(x for x in out if re.fullmatch(r"[A-Z]{1,5}",x)))

class Massive:
    BASE="https://api.massive.com"
    def __init__(self,key,http):self.key=key;self.http=http
    def full_snapshot(self):return self.http.json(f"{self.BASE}/v2/snapshot/locale/us/markets/stocks/tickers",params={"apiKey":self.key,"include_otc":"false"})
    def bars(self,symbol,start,end):
        return self.http.json(f"{self.BASE}/v2/aggs/ticker/{symbol}/range/1/minute/{start.date()}/{end.date()}",params={"adjusted":"true","sort":"asc","limit":50000,"apiKey":self.key})
    def profile(self,symbol):return self.http.json(f"{self.BASE}/v3/reference/tickers/{symbol}",params={"apiKey":self.key})

class FMP:
    BASE="https://financialmodelingprep.com/stable"
    def __init__(self,key,http):self.key=key;self.http=http
    def screener(self,minp,maxp,minvol):
        p={"apikey":self.key,"priceMoreThan":minp,"priceLowerThan":maxp,"volumeMoreThan":minvol,"exchange":"NASDAQ,NYSE,AMEX","country":"US","isEtf":"false","isFund":"false","isActivelyTrading":"true","limit":200}
        return self.http.json(f"{self.BASE}/company-screener",params=p)
    def profile(self,symbol):
        return self.http.json(f"{self.BASE}/profile",params={"symbol":symbol,"apikey":self.key})

class Finnhub:
    BASE="https://finnhub.io/api/v1"
    def __init__(self,key,http):self.key=key;self.http=http
    def quote(self,symbol):return self.http.json(f"{self.BASE}/quote",params={"symbol":symbol,"token":self.key})
    def profile(self,symbol):return self.http.json(f"{self.BASE}/stock/profile2",params={"symbol":symbol,"token":self.key})
    def news(self,symbol,days=2):
        to=datetime.now(NY).date(); fr=to-timedelta(days=days)
        return self.http.json(f"{self.BASE}/company-news",params={"symbol":symbol,"from":fr.isoformat(),"to":to.isoformat(),"token":self.key})
    def sentiment(self,symbol):return self.http.json(f"{self.BASE}/news-sentiment",params={"symbol":symbol,"token":self.key})

class TwelveData:
    BASE="https://api.twelvedata.com"
    def __init__(self,key,http):self.key=key;self.http=http
    def quote(self,symbol):return self.http.json(f"{self.BASE}/quote",params={"symbol":symbol,"apikey":self.key})

class SEC:
    def __init__(self,http,ua):self.http=http;self.h={"User-Agent":ua,"Accept-Encoding":"gzip, deflate"};self.map=None
    def ticker_map(self):
        if self.map is None:
            j=self.http.json("https://www.sec.gov/files/company_tickers.json",headers=self.h);self.map={str(v['ticker']).upper():str(v['cik_str']).zfill(10) for v in j.values()}
        return self.map
    def events(self,symbols):
        out={}; mp=self.ticker_map(); cutoff=datetime.now(UTC)-timedelta(days=90)
        risky={"S-1","S-1/A","S-3","S-3/A","424B4","424B5","EFFECT","RW","POS AM"}
        for s in symbols[:12]:
            try:
                cik=mp.get(s); 
                if not cik:continue
                j=self.http.json(f"https://data.sec.gov/submissions/CIK{cik}.json",headers=self.h);r=j.get('filings',{}).get('recent',{}); rows=[]
                for form,ds,acc in zip(r.get('form',[]),r.get('filingDate',[]),r.get('accessionNumber',[])):
                    try:dt=datetime.fromisoformat(ds).replace(tzinfo=UTC)
                    except:continue
                    if dt<cutoff:continue
                    if form in risky or form in {"8-K","10-Q","10-K","6-K","20-F"}:rows.append((dt,form,acc))
                rows.sort(reverse=True); risk=sum(20 if x[1].startswith('424') else 15 for x in rows if x[1] in risky)
                out[s]={"risk":min(risk,100),"flags":', '.join(dict.fromkeys(x[1] for x in rows[:8])),"latest":rows[0][0].isoformat() if rows else ''}
            except Exception:out[s]={"risk":0,"flags":"SEC unavailable"}
        return out

class Public:
    def __init__(self,http,ua):self.http=http;self.sec=SEC(http,ua)
    def rss(self,url,limit=80):
        try:root=ET.fromstring(self.http.text(url));out=[]
        except:return []
        for item in root.iter():
            tag=item.tag.lower().split('}')[-1]
            if tag not in ('item','entry'):continue
            d={}
            for ch in list(item):
                k=ch.tag.lower().split('}')[-1];d[k]=(ch.text or '').strip() or ch.attrib.get('href','')
            out.append(d)
            if len(out)>=limit:break
        return out
    def news(self):
        out=[]
        for u in ['https://www.globenewswire.com/RssFeed/subjectcode/20-Business%20Services/feedTitle/GlobeNewswire%20-%20Business%20Services','https://www.prnewswire.com/rss/news-releases-list.rss']:
            out+=self.rss(u,60)
        return out
    def halts(self):
        out={}
        for item in self.rss('https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts',120):
            text=' '.join(str(v) for v in item.values()); m=re.search(r'\b([A-Z]{1,5})\b',text)
            if m:out[m.group(1)]=text[:240]
        return out

class Clinical:
    BASE='https://clinicaltrials.gov/api/v2'
    def __init__(self,http):self.http=http
    def sponsor_hits(self,company):
        if not company:return []
        try:
            j=self.http.json(f'{self.BASE}/studies',params={'query.spons':company,'pageSize':5,'fields':'NCTId,BriefTitle,OverallStatus,Phase','countTotal':'true'})
            return j.get('studies',[])
        except:return []

class FDA:
    def __init__(self,key,http):self.key=key;self.http=http
    def manufacturer_hits(self,company):
        if not self.key or not company:return []
        try:
            q={'api_key':self.key,'search':f'openfda.manufacturer_name:"{company}"','limit':5}
            j=self.http.json('https://api.fda.gov/drug/drugsfda.json',params=q);return j.get('results',[])
        except:return []

class FINRA:
    def __init__(self,http):self.http=http
    def short_volume(self, symbols):
        # Best-effort public dataset. It is context only, not short interest.
        try:
            payload={'limit':5000,'fields':['tradeReportDate','securitiesInformationProcessorSymbolIdentifier','shortParQuantity','shortExemptParQuantity','totalParQuantity'],'compareFilters':[{'compareType':'greater','fieldName':'tradeReportDate','fieldValue':(datetime.now(NY).date()-timedelta(days=3)).isoformat()}]}
            rows=self.http.post_json('https://api.finra.org/data/group/otcMarket/name/regShoDaily',payload=payload)
            latest={}
            for r in rows if isinstance(rows,list) else []:
                s=str(r.get('securitiesInformationProcessorSymbolIdentifier','')).upper(); total=float(r.get('totalParQuantity') or 0); short=float(r.get('shortParQuantity') or 0)
                if s in symbols and total:latest[s]=short/total*100
            return latest
        except:return {}


def _parse_massive(payload):
    out={}
    for x in (payload.get('tickers') or []):
        s=str(x.get('ticker','')).upper(); day=x.get('day') or {}; prev=x.get('prevDay') or {}; q=x.get('lastQuote') or {}; m=x.get('min') or {}
        if not re.fullmatch(r'[A-Z]{1,5}',s):continue
        p=float((x.get('lastTrade') or {}).get('p') or m.get('c') or day.get('c') or 0); pc=float(prev.get('c') or 0); v=float(day.get('v') or 0)
        out[s]={'price':p,'prev_close':pc,'volume':v,'gap_pct':float(x.get('todaysChangePerc') or ((p/pc-1)*100 if pc else 0)),'bid':q.get('p'),'ask':q.get('P'),'name':x.get('name',''),'exchange':x.get('exchange','')}
    return out

def _parse_fmp(rows):
    out={}
    for x in rows or []:
        s=str(x.get('symbol','')).upper();
        if s:out[s]=x
    return out

def _news_score(items,sym):
    pos=('approval','approved','fda','clinical','trial','contract','agreement','partnership','acquisition','merger','award','record','launch','results','clearance','phase 2','phase 3','revenue','orders')
    neg=('offering','registered direct','dilution','warrant','convertible','reverse split','going concern','bankruptcy','delisting','lawsuit','investigation','terminated','failure','default')
    hits=[]; sc=0
    for x in items or []:
        title=str(x.get('headline') or x.get('title') or x.get('name') or ''); desc=str(x.get('summary') or x.get('description') or '')
        t=(title+' '+desc).lower()
        if re.search(rf'\b{re.escape(sym.lower())}\b',t):
            d=1+(1.5 if any(w in t for w in pos) else 0)-(2 if any(w in t for w in neg) else 0);sc+=d;hits.append((title,x.get('url') or x.get('link') or ''))
    return max(-5,min(5,sc)),len(hits),hits[0] if hits else ('','')


def scan(config,min_price,max_price,min_gap,min_dv,max_float,max_candidates,manual,coverage='Multi-source'):
    started=time.time(); key=config.get('ALPACA_API_KEY'); secret=config.get('ALPACA_SECRET_KEY')
    if not key or not secret: raise RuntimeError('Faltan ALPACA_API_KEY y ALPACA_SECRET_KEY.')
    paper=str(config.get('ALPACA_PAPER','True')).lower() in {'true','1','yes','on'}
    http=HTTP(min_interval=0.25); alp=Alpaca(key,secret,paper,http); public=Public(http,config.get('SEC_USER_AGENT','SmallCapRadar contact@example.com'))
    sources={}; discovered={}; names={}
    # 1) Massive full-market snapshot, one call, when configured.
    if config.get('MASSIVE_API_KEY'):
        try:
            ms=Massive(config['MASSIVE_API_KEY'],http); massive=_parse_massive(ms.full_snapshot()); sources['massive']=True
            for s,x in massive.items():
                p=x['price']; dv=p*x['volume'];
                if min_price<=p<=max_price and x['gap_pct']>=min_gap and dv>=min_dv:
                    discovered[s]=x; names[s]=x.get('name','')
        except Exception as e:sources['massive']=False; massive={};
    else: sources['massive']=False; massive={}
    # 2) FMP screener broadens discovery without touching thousands of Alpaca snapshots.
    if config.get('FMP_API_KEY'):
        try:
            fmp=FMP(config['FMP_API_KEY'],http); fr=_parse_fmp(fmp.screener(min_price,max_price,max(1000,min_dv/max(min_price,0.01))))
            sources['fmp']=True
            for s,x in fr.items():
                p=float(x.get('price') or 0); vol=float(x.get('volume') or 0); prev=float(x.get('previousClose') or 0); gap=(p/prev-1)*100 if prev else float(x.get('changesPercentage') or 0)
                if p and min_price<=p<=max_price and gap>=min_gap and p*vol>=min_dv: discovered.setdefault(s,{'price':p,'prev_close':prev,'volume':vol,'gap_pct':gap}); names[s]=x.get('companyName','')
        except Exception:sources['fmp']=False
    else:sources['fmp']=False
    # 3) Alpaca movers/active as a fallback and validation source.
    try:
        assets=alp.assets(); amap={str(a.get('symbol','')).upper():a for a in assets if a.get('tradable') and a.get('status')=='active' and a.get('exchange') in {'NYSE','NASDAQ','AMEX','ARCA','NYSEARCA'} and re.fullmatch(r'[A-Z]{1,5}',str(a.get('symbol','')).upper())}
        sources['alpaca']=True
    except Exception as e: amap={};sources['alpaca']=False
    try:
        syms=alp.symbols(alp.movers(50))+alp.symbols(alp.active(100));sources['alpaca_discovery']=True
        for s in syms:
            if s in amap: discovered.setdefault(s,{}); names.setdefault(s,amap[s].get('name',''))
    except Exception:sources['alpaca_discovery']=False
    for s in manual:
        s=s.upper();
        if s in amap or s in discovered:discovered[s]=discovered.get(s,{})
    symbols=list(discovered)[:max(60,max_candidates*3)]
    if not symbols: raise RuntimeError('Ninguna fuente devolvió candidatos con los filtros actuales.')
    # 4) Alpaca IEX snapshots only for the reduced candidate set.
    try: snaps=alp.snapshots(symbols[:60]);sources['alpaca_snapshots']=True
    except Exception:snaps={};sources['alpaca_snapshots']=False
    rows=[]
    for s in symbols:
        base=discovered.get(s,{})
        ss=snaps.get(s,{}) or {}; tr=ss.get('latestTrade') or {}; q=ss.get('latestQuote') or {}; day=ss.get('dailyBar') or {}; prev=ss.get('prevDailyBar') or {}
        p=float(tr.get('p') or day.get('c') or base.get('price') or 0); pc=float(prev.get('c') or base.get('prev_close') or 0); v=float(day.get('v') or base.get('volume') or 0); gap=(p/pc-1)*100 if pc else float(base.get('gap_pct') or 0)
        if not p or not pc or p<min_price or p>max_price or gap<min_gap or p*v<min_dv:continue
        bid=float(q.get('bp') or base.get('bid') or 0) or None; ask=float(q.get('ap') or base.get('ask') or 0) or None; spread=((ask-bid)/p*100) if bid and ask else None
        rows.append((s,p,pc,gap,v,p*v,bid,ask,spread))
    rows.sort(key=lambda x:(x[3],x[5]),reverse=True); rows=rows[:max_candidates]; symbols=[x[0] for x in rows]
    now=datetime.now(NY); start=now.replace(hour=4,minute=0,second=0,microsecond=0)
    try: bars=alp.bars(symbols,start,now);sources['alpaca_bars']=bool(bars)
    except Exception:bars={};sources['alpaca_bars']=False
    # 5) Optional Massive bars for the final candidates if configured. One request per symbol, capped.
    massive_bars={}
    if config.get('MASSIVE_API_KEY'):
        try:
            ms=Massive(config['MASSIVE_API_KEY'],http)
            for s in symbols[:8]:
                j=ms.bars(s,start,now); arr=j.get('results') or []
                if arr: massive_bars[s]=pd.DataFrame([{'timestamp':datetime.fromtimestamp(r['t']/1000,UTC).isoformat(),'open':r['o'],'high':r['h'],'low':r['l'],'close':r['c'],'volume':r['v']} for r in arr])
            sources['massive_bars']=bool(massive_bars)
        except Exception:sources['massive_bars']=False
    # 6) Public/news/SEC once, then vendor enrichment only for top 8.
    try: news=public.news();sources['news_rss']=bool(news)
    except Exception:news=[];sources['news_rss']=False
    try: halts=public.halts();sources['halts']=bool(halts)
    except Exception:halts={};sources['halts']=False
    try: sec=public.sec.events(symbols);sources['sec']=bool(sec)
    except Exception:sec={};sources['sec']=False
    fh=Finnhub(config['FINNHUB_API_KEY'],http) if config.get('FINNHUB_API_KEY') else None; td=TwelveData(config['TWELVE_DATA_API_KEY'],http) if config.get('TWELVE_DATA_API_KEY') else None; fmp=FMP(config['FMP_API_KEY'],http) if config.get('FMP_API_KEY') else None
    clinical=Clinical(http); fda=FDA(config.get('OPENFDA_API_KEY',''),http)
    finra=FINRA(http)
    fh_data={}; td_data={}; fmp_data={}; clinical_data={}; fda_data={}
    for s in symbols[:8]:
        if fh:
            try:fh_data[s]={'quote':fh.quote(s),'profile':fh.profile(s),'news':fh.news(s),'sentiment':fh.sentiment(s)}
            except:pass
        if td:
            try:td_data[s]=td.quote(s)
            except:pass
        if fmp:
            try:
                p=fmp.profile(s); fmp_data[s]=p[0] if p else {}
            except:pass
    sources['finnhub']=bool(fh_data) if fh else False; sources['twelvedata']=bool(td_data) if td else False; sources['fmp_profiles']=bool(fmp_data) if fmp else False
    # FINRA is context-only and may return no matching listed-symbol records.
    try: short=finra.short_volume(symbols); sources['finra']=bool(short)
    except: short={}; sources['finra']=False
    out=[]
    for r in rows:
        s,p,pc,gap,v,dv,bid,ask,spread=r; c=Candidate(s,names.get(s,''),price=p,prev_close=pc,gap_pct=gap,volume=v,dollar_volume=dv,bid=bid,ask=ask,spread_pct=spread)
        profile=fmp_data.get(s,{})
        if not profile and fh_data.get(s):profile=fh_data[s].get('profile') or {}
        c.name=profile.get('companyName') or profile.get('name') or c.name; c.exchange=profile.get('exchange') or profile.get('exchangeCode') or ''
        for k in ('float_shares','shares_outstanding','market_cap'):
            src={'float_shares':['floatShares','float'],'shares_outstanding':['sharesOutstanding','shareOutstanding'],'market_cap':['mktCap','marketCapitalization']}.get(k,[])
            for z in src:
                if profile.get(z):
                    try:setattr(c,k,float(profile[z]));break
                    except:pass
        if c.float_shares and c.float_shares>max_float:continue
        f=bar_features(massive_bars.get(s) if s in massive_bars else bars.get(s,pd.DataFrame()))
        for k,val in f.items():
            if hasattr(c,k):setattr(c,k,val)
        if c.rvol is None:
            avg=float(profile.get('volAvg') or 0) if profile else 0; c.rvol=v/avg if avg else c.intraday_rvol
        rsssc,n,first=_news_score(news,s); fhnews=fh_data.get(s,{}).get('news') or []; fsc,fn,ffirst=_news_score(fhnews,s); c.catalyst_score=max(-5,min(5,rsssc+fsc));c.catalyst_count=n+fn;c.catalyst_text=ffirst[0] or first[0];c.catalyst_url=ffirst[1] or first[1]
        if fh_data.get(s,{}).get('sentiment'):c.finnhub_sentiment=float(fh_data[s]['sentiment'].get('sentiment',{}).get('bullishPercent') or 0)-float(fh_data[s]['sentiment'].get('sentiment',{}).get('bearishPercent') or 0)
        se=sec.get(s,{});c.sec_risk_score=float(se.get('risk',0));c.sec_flags=se.get('flags','')
        if s in halts:c.halt=True;c.halt_reason=halts[s]
        c.short_volume_ratio=short.get(s)
        # Biotech enrichment only when a vendor profile provides a company name; fail-soft.
        if c.name and any(w in c.name.lower() for w in ('bio','therapeut','pharma','medical','oncology')):
            try:clinical_data[s]=clinical.sponsor_hits(c.name)
            except:pass
            try:fda_data[s]=fda.manufacturer_hits(c.name) if config.get('OPENFDA_API_KEY') else []
            except:pass
        c.clinical_hits=len(clinical_data.get(s,[]));c.fda_hits=len(fda_data.get(s,[]))
        c.source_count=sum(bool(x) for x in [snaps.get(s),massive.get(s),fmp_data.get(s),fh_data.get(s),td_data.get(s),se,news,halts])
        c.data_confidence=confidence(c);c.score=score(c);c.setup=setup(c)
        a=c.atr or p*.02;c.stop=round(max(.01,p-1.2*a),4);c.tp1=round(p*1.03,4);c.tp2=round(p*1.06,4);c.entry_zone=f'{p*.995:.4f}–{p*1.005:.4f}'
        used=[k for k,vv in sources.items() if vv];c.source_notes=' + '.join(used);out.append(c)
    out.sort(key=lambda x:x.score,reverse=True)
    meta={'universe_assets':len(amap),'discovery_candidates':len(discovered),'snapshot_candidates':len(rows),'final_candidates':len(out),'elapsed_sec':round(time.time()-started,2),'source_status':sources,'coverage_mode':coverage,'rate_limit_strategy':'candidate-first; shared HTTP pacing; bounded retries; no whole-universe Alpaca snapshots','discovery_methods':['Massive full snapshot' if sources.get('massive') else '', 'FMP screener' if sources.get('fmp') else '', 'Alpaca movers/most-active' if sources.get('alpaca_discovery') else ''],'vendor_keys':['Alpaca','Massive','FMP','Finnhub','Twelve Data']}
    return pd.DataFrame([x.to_dict() for x in out]),meta
