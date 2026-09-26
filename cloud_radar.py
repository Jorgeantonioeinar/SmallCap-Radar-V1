"""Cloud-first Small Cap Radar.

This module is intentionally independent from the legacy execution engine. It is
safe for Streamlit/GitHub: it scans, scores and explains candidates but never
submits an order.
"""
from __future__ import annotations

import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass
class Candidate:
    symbol: str
    price: float = 0.0
    prev_close: float = 0.0
    gap_pct: float = 0.0
    volume: float = 0.0
    dollar_volume: float = 0.0
    rvol: Optional[float] = None
    intraday_rvol: Optional[float] = None
    volume_accel: Optional[float] = None
    price_accel_1m: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_pct: Optional[float] = None
    float_shares: Optional[float] = None
    shares_outstanding: Optional[float] = None
    market_cap: Optional[float] = None
    vwap: Optional[float] = None
    vwap_slope: Optional[float] = None
    ema9: Optional[float] = None
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    rsi: Optional[float] = None
    atr: Optional[float] = None
    adx: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_lower: Optional[float] = None
    pmh: Optional[float] = None
    pml: Optional[float] = None
    hod: Optional[float] = None
    halt: bool = False
    halt_reason: str = ""
    catalyst_score: float = 0.0
    catalyst_count: int = 0
    catalyst_text: str = ""
    catalyst_url: str = ""
    sec_risk_score: float = 0.0
    sec_flags: str = ""
    short_volume_ratio: Optional[float] = None
    data_confidence: float = 0.0
    score: float = 0.0
    setup: str = "NO_SETUP"
    entry_zone: str = ""
    stop: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    source_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HTTP:
    def __init__(self, timeout: int = 12):
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "SmallCapRadar/1.0 contact@example.com"})

    def get_json(self, url: str, headers: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        r = self.s.get(url, headers=headers, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_text(self, url: str, headers: Optional[dict] = None, params: Optional[dict] = None) -> str:
        r = self.s.get(url, headers=headers, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.text


class AlpacaMarket:
    BASE = "https://data.alpaca.markets/v2"

    def __init__(self, key: str, secret: str, paper: bool = True, http: Optional[HTTP] = None):
        self.key, self.secret = key, secret
        self.paper = bool(paper)
        self.http = http or HTTP()
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        # Alpaca Trading API uses a DIFFERENT domain for Paper vs Live.
        # Market Data remains on data.alpaca.markets for both environments.
        self.TRADING = (
            "https://paper-api.alpaca.markets/v2"
            if self.paper else "https://api.alpaca.markets/v2"
        )

    def assets(self) -> List[dict]:
        return self.http.get_json(
            f"{self.TRADING}/assets",
            headers=self.headers,
            params={"status": "active", "asset_class": "us_equity"},
        )

    def snapshots(self, symbols: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i+100]
            data = self.http.get_json(
                f"{self.BASE}/stocks/snapshots",
                headers=self.headers,
                params={"symbols": ",".join(chunk), "feed": "iex"},
            )
            out.update(data or {})
        return out

    def bars(self, symbols: List[str], start: datetime, end: datetime) -> Dict[str, pd.DataFrame]:
        if not symbols:
            return {}
        params = {
            "symbols": ",".join(symbols), "timeframe": "1Min",
            "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "limit": 10000, "feed": "iex", "adjustment": "raw",
        }
        data = self.http.get_json(f"{self.BASE}/stocks/bars", headers=self.headers, params=params)
        result = {}
        for sym, rows in (data.get("bars") or {}).items():
            result[sym] = pd.DataFrame(rows)
        return result

    def clock(self) -> dict:
        return self.http.get_json(f"{self.TRADING}/clock", headers=self.headers)


class PublicSources:
    def __init__(self, http: Optional[HTTP] = None, sec_user_agent: str = "SmallCapRadar contact@example.com"):
        self.http = http or HTTP()
        self.sec_headers = {"User-Agent": sec_user_agent, "Accept-Encoding": "gzip, deflate"}
        self._sec_map: Optional[Dict[str, str]] = None

    def sec_map(self) -> Dict[str, str]:
        if self._sec_map is None:
            raw = self.http.get_json("https://www.sec.gov/files/company_tickers.json", headers=self.sec_headers)
            self._sec_map = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
        return self._sec_map

    def sec_events(self, symbols: Iterable[str], days: int = 90) -> Dict[str, dict]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        mapping = self.sec_map()
        out = {}
        risky = {"S-1", "S-1/A", "S-3", "S-3/A", "424B4", "424B5", "EFFECT", "RW", "POS AM"}
        watched = {"8-K", "10-Q", "10-K", "6-K", "20-F"}
        for symbol in list(symbols)[:15]:
            cik = mapping.get(symbol.upper())
            if not cik:
                continue
            try:
                j = self.http.get_json(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=self.sec_headers)
                recent = j.get("filings", {}).get("recent", {})
                forms = recent.get("form", [])
                dates = recent.get("filingDate", [])
                accs = recent.get("accessionNumber", [])
                recent_rows = []
                for form, ds, acc in zip(forms, dates, accs):
                    try:
                        dt = datetime.fromisoformat(ds).replace(tzinfo=UTC)
                    except Exception:
                        continue
                    if dt < cutoff:
                        continue
                    if form in risky or form in watched:
                        recent_rows.append((dt, form, acc))
                recent_rows.sort(reverse=True)
                risk = 0.0
                flags = []
                for _, form, _ in recent_rows[:8]:
                    if form in risky:
                        risk += 20 if form.startswith("424") else 15
                        flags.append(form)
                    elif form in watched:
                        flags.append(form)
                out[symbol] = {"risk": min(risk, 100), "flags": ", ".join(dict.fromkeys(flags)), "latest": recent_rows[0][0].isoformat() if recent_rows else ""}
            except Exception:
                out[symbol] = {"risk": 0.0, "flags": "SEC unavailable", "latest": ""}
        return out

    def _rss_items(self, url: str, limit: int = 80) -> List[dict]:
        try:
            root = ET.fromstring(self.http.get_text(url))
        except Exception:
            return []
        items = []
        for item in root.iter():
            tag = item.tag.lower().split("}")[-1]
            if tag not in ("item", "entry"):
                continue
            fields = {}
            for child in list(item):
                k = child.tag.lower().split("}")[-1]
                fields[k] = (child.text or "").strip()
                if k == "link" and not fields[k]:
                    fields[k] = child.attrib.get("href", "")
            items.append(fields)
            if len(items) >= limit:
                break
        return items

    def news(self) -> List[dict]:
        feeds = [
            "https://www.globenewswire.com/RssFeed/subjectcode/20-Business%20Services/feedTitle/GlobeNewswire%20-%20Business%20Services",
            "https://www.prnewswire.com/rss/news-releases-list.rss",
        ]
        out = []
        for feed in feeds:
            out.extend(self._rss_items(feed, 60))
        return out

    def halt_symbols(self) -> Dict[str, str]:
        # Nasdaq Trader's public halt RSS can change formatting; fail soft.
        urls = [
            "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts",
            "https://www.nasdaqtrader.com/rss.aspx?feed=tradehaltsrss",
        ]
        out = {}
        for url in urls:
            try:
                for item in self._rss_items(url, 120):
                    text = " ".join(str(v) for v in item.values())
                    m = re.search(r"\b([A-Z]{1,5})\b", text)
                    if m:
                        out[m.group(1)] = text[:240]
                if out:
                    break
            except Exception:
                pass
        return out

    @staticmethod
    def news_for_symbol(items: List[dict], symbol: str) -> Tuple[float, int, str, str]:
        positive = ("approval", "approved", "fda", "clinical", "trial", "contract", "agreement", "partnership", "acquisition", "merger", "award", "record", "launch", "results", "clearance", "phase 2", "phase 3", "revenue", "orders")
        negative = ("offering", "registered direct", "dilution", "warrant", "convertible", "reverse split", "going concern", "bankruptcy", "delisting", "lawsuit", "investigation", "terminated", "failure", "default")
        hits = []
        score = 0.0
        for x in items:
            title = str(x.get("title") or x.get("name") or "")
            desc = str(x.get("description") or x.get("summary") or "")
            text = f"{title} {desc}".lower()
            if re.search(rf"(?<![A-Z])\${re.escape(symbol)}(?![A-Z])|\b{re.escape(symbol.lower())}\b", text):
                delta = 1.0
                if any(w in text for w in positive): delta += 1.5
                if any(w in text for w in negative): delta -= 2.0
                score += delta
                hits.append((title[:180], str(x.get("link") or "")))
        if not hits:
            return 0.0, 0, "", ""
        title, url = hits[0]
        return float(max(-5, min(5, score))), len(hits), title, url


# ----------------------------- indicators ---------------------------------
def _ema(s: pd.Series, n: int) -> Optional[float]:
    if len(s) < n: return None
    return float(s.ewm(span=n, adjust=False).mean().iloc[-1])


def _rsi(s: pd.Series, n: int = 14) -> Optional[float]:
    if len(s) < n + 1: return None
    d = s.diff(); gain = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean(); loss = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return float((100 - 100/(1+rs)).iloc[-1]) if pd.notna(rs.iloc[-1]) else 100.0


def _atr(df: pd.DataFrame, n: int = 14) -> Optional[float]:
    if len(df) < n + 1: return None
    h, l, c = df.high, df.low, df.close
    prev = c.shift(1)
    tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1/n, adjust=False).mean().iloc[-1])


def _adx(df: pd.DataFrame, n: int = 14) -> Optional[float]:
    if len(df) < 2*n+2: return None
    h, l, c = df.high, df.low, df.close
    up = h.diff(); dn = -l.diff()
    plus = up.where((up > dn) & (up > 0), 0.0); minus = dn.where((dn > up) & (dn > 0), 0.0)
    prev = c.shift(1); tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean(); pdi = 100*plus.ewm(alpha=1/n, adjust=False).mean()/atr; mdi = 100*minus.ewm(alpha=1/n, adjust=False).mean()/atr
    dx = (100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)).ewm(alpha=1/n, adjust=False).mean()
    return float(dx.iloc[-1]) if pd.notna(dx.iloc[-1]) else None


def _bar_features(df: pd.DataFrame, now: datetime) -> dict:
    if df.empty: return {}
    d = df.copy()
    for c in ["open","high","low","close","volume"]: d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["close"]).sort_values("timestamp")
    if d.empty: return {}
    ts = pd.to_datetime(d.timestamp, utc=True).dt.tz_convert(NY)
    d["ny"] = ts
    regular = d[(d.ny.dt.time >= pd.Timestamp("09:30").time()) & (d.ny.dt.time <= pd.Timestamp("16:00").time())]
    pre = d[(d.ny.dt.time >= pd.Timestamp("04:00").time()) & (d.ny.dt.time < pd.Timestamp("09:30").time())]
    x = regular if len(regular) >= 10 else d
    close, vol = x.close, x.volume
    typical = (x.high+x.low+x.close)/3
    cv = float((typical*vol).sum()/vol.sum()) if vol.sum() else None
    prior_vwap = None
    if len(x) >= 6 and x.volume.iloc[:-5].sum():
        pv = x.iloc[:-5]; prior_vwap = float((((pv.high+pv.low+pv.close)/3)*pv.volume).sum()/pv.volume.sum())
    vol_base = float(vol.iloc[-30:-5].mean()) if len(vol) >= 30 else None
    out = {
        "vwap": cv, "vwap_slope": ((cv-prior_vwap)/prior_vwap*100 if cv and prior_vwap else 0.0),
        "ema9": _ema(close,9), "ema20": _ema(close,20), "ema50": _ema(close,50),
        "rsi": _rsi(close), "atr": _atr(x), "adx": _adx(x),
        "hod": float(x.high.max()), "pmh": float(pre.high.max()) if not pre.empty else None,
        "pml": float(pre.low.min()) if not pre.empty else None,
        "price_accel_1m": float((close.iloc[-1]/close.iloc[-2]-1)*100) if len(close)>1 else None,
        "volume_accel": float(vol.iloc[-1]/vol_base) if vol_base and vol_base>0 else None,
        "intraday_rvol": float(vol.iloc[-5:].mean()/vol.iloc[-30:-5].mean()) if len(vol)>=30 and vol.iloc[-30:-5].mean()>0 else None,
        "bar_count": len(d),
    }
    ma = close.rolling(20).mean(); sd = close.rolling(20).std()
    out["bollinger_upper"] = float((ma+2*sd).iloc[-1]) if len(close)>=20 else None
    out["bollinger_lower"] = float((ma-2*sd).iloc[-1]) if len(close)>=20 else None
    return out


def _clip(x, lo=0.0, hi=1.0): return max(lo, min(hi, float(x)))


def _score(c: Candidate) -> float:
    s = 0.0
    s += 15*_clip(c.gap_pct/30)
    s += 15*_clip((c.rvol or 0)/8)
    s += 10*_clip((c.volume_accel or 0)/3)
    s += 10*_clip(max(c.price_accel_1m or 0, 0)/3)
    if c.vwap and c.price > c.vwap: s += 10*(1 if (c.vwap_slope or 0)>=0 else .6)
    if c.pmh:
        if c.price >= c.pmh*.995: s += 10
        elif c.price >= c.pmh*.97: s += 5
    if c.float_shares: s += 8*_clip(20_000_000/c.float_shares)
    if c.spread_pct is not None: s += 7*_clip(1-c.spread_pct/2)
    s += 10*_clip((c.catalyst_score+2)/4)
    if c.ema9 and c.ema20 and c.price > c.ema9 > c.ema20: s += 2
    if c.ema20 and c.ema50 and c.ema20 > c.ema50: s += 1
    if c.rsi is not None and 50 <= c.rsi <= 78: s += 2
    if c.adx is not None and c.adx >= 20: s += 2
    s += 5*(1-_clip(c.sec_risk_score/100))
    if c.halt: s *= .25
    if c.data_confidence < 70: s *= .85
    return round(_clip(s/100)*100, 1)


def _setup(c: Candidate) -> str:
    if c.halt: return "HALT"
    if c.sec_risk_score >= 60: return "DILUTION_RISK"
    if c.data_confidence < 70: return "LOW_CONFIDENCE"
    if c.score >= 75:
        if c.pmh and c.price >= c.pmh*.995: return "PMH_BREAKOUT"
        if c.vwap and c.price > c.vwap: return "VWAP_MOMENTUM"
        return "MOMENTUM"
    if c.score >= 60: return "WATCH"
    return "NO_SETUP"


def _confidence(c: Candidate) -> float:
    checks = [
        c.price>0, c.prev_close>0, c.volume>0, c.bid is not None and c.ask is not None,
        c.vwap is not None, c.ema9 is not None, c.rsi is not None, c.atr is not None,
        c.pmh is not None, c.catalyst_count>0 or c.catalyst_score==0, c.sec_flags != "SEC unavailable",
    ]
    weights = [12,8,10,10,10,8,8,8,8,8,10]
    return round(sum(w for ok,w in zip(checks,weights) if ok)/sum(weights)*100,1)


def scan(config: dict, min_price: float, max_price: float, min_gap: float, min_dollar_volume: float, max_float: float, max_candidates: int, manual: List[str]) -> Tuple[pd.DataFrame, dict]:
    if not config.get("ALPACA_API_KEY") or not config.get("ALPACA_SECRET_KEY"):
        raise RuntimeError("Faltan ALPACA_API_KEY y/o ALPACA_SECRET_KEY.")
    http = HTTP()
    alp = AlpacaMarket(config["ALPACA_API_KEY"], config["ALPACA_SECRET_KEY"], http)
    pub = PublicSources(http, config.get("SEC_USER_AGENT") or "SmallCapRadar contact@example.com")
    started = time.time()
    assets = alp.assets()
    allowed = []
    exchanges = {"NYSE","NASDAQ","AMEX","ARCA"}
    for a in assets:
        if not a.get("tradable") or a.get("status") != "active" or a.get("exchange") not in exchanges: continue
        sym = a.get("symbol","")
        if re.fullmatch(r"[A-Z]{1,5}", sym): allowed.append(sym)
    # Dynamic universe: snapshots first, then deep scan only top candidates.
    snaps = alp.snapshots(allowed)
    rows=[]
    for sym, s in snaps.items():
        tr=s.get("latestTrade") or {}; q=s.get("latestQuote") or {}; day=s.get("dailyBar") or {}; prev=s.get("prevDailyBar") or {}
        price=float(tr.get("p") or day.get("c") or 0); prevc=float(prev.get("c") or 0); vol=float(day.get("v") or 0)
        if not price or not prevc: continue
        gap=(price/prevc-1)*100
        dv=price*vol
        if price < min_price or price > max_price or gap < min_gap or dv < min_dollar_volume: continue
        bid=float(q.get("bp") or 0) or None; ask=float(q.get("ap") or 0) or None
        spread=((ask-bid)/price*100) if bid and ask and price else None
        rows.append((sym,price,prevc,gap,vol,dv,bid,ask,spread))
    rows.sort(key=lambda x:(x[3],x[5]), reverse=True)
    if manual:
        have={r[0] for r in rows}
        manual_snaps={k:v for k,v in snaps.items() if k in {m.upper() for m in manual}}
        for sym,s in manual_snaps.items():
            tr=s.get("latestTrade") or {}; day=s.get("dailyBar") or {}; prev=s.get("prevDailyBar") or {}; q=s.get("latestQuote") or {}
            price=float(tr.get("p") or day.get("c") or 0); prevc=float(prev.get("c") or 0)
            if price and prevc and sym not in have:
                rows.append((sym,price,prevc,(price/prevc-1)*100,float(day.get("v") or 0),price*float(day.get("v") or 0),float(q.get("bp") or 0) or None,float(q.get("ap") or 0) or None,None))
    rows=rows[:max_candidates]
    symbols=[r[0] for r in rows]
    now=datetime.now(NY); start=now.replace(hour=4,minute=0,second=0,microsecond=0)
    bars=alp.bars(symbols,start,now)
    news=pub.news(); halts=pub.halt_symbols(); sec=pub.sec_events(symbols)
    fmp_key=config.get("FMP_API_KEY")
    fmp_profiles={}
    if fmp_key:
        for sym in symbols[:15]:
            try:
                j=http.get_json("https://financialmodelingprep.com/api/v3/profile/"+sym,params={"apikey":fmp_key})
                if j: fmp_profiles[sym]=j[0]
            except Exception: pass
    out=[]
    for r in rows:
        sym,price,prevc,gap,vol,dv,bid,ask,spread=r
        c=Candidate(sym,price,prevc,gap,vol,dv,bid=bid,ask=ask,spread_pct=spread)
        prof=fmp_profiles.get(sym,{})
        c.float_shares=float(prof.get("floatShares")) if prof.get("floatShares") else None
        c.shares_outstanding=float(prof.get("sharesOutstanding")) if prof.get("sharesOutstanding") else None
        c.market_cap=float(prof.get("mktCap")) if prof.get("mktCap") else None
        if c.float_shares and c.float_shares>max_float: continue
        f=_bar_features(bars.get(sym,pd.DataFrame()),now)
        for k,v in f.items():
            if hasattr(c,k): setattr(c,k,v)
        c.catalyst_score,c.catalyst_count,c.catalyst_text,c.catalyst_url=PublicSources.news_for_symbol(news,sym)
        se=sec.get(sym,{})
        c.sec_risk_score=float(se.get("risk",0)); c.sec_flags=se.get("flags","")
        if sym in halts: c.halt=True; c.halt_reason=halts[sym]
        # RVOL proxy using current daily volume versus the average daily volume in FMP when available.
        if prof.get("volAvg"):
            avg=float(prof["volAvg"]); c.rvol=vol/avg if avg else None
        elif c.intraday_rvol is not None:
            c.rvol=c.intraday_rvol
        c.data_confidence=_confidence(c); c.score=_score(c); c.setup=_setup(c)
        if price>0:
            atr=c.atr or price*.02
            c.stop=round(max(0.01,price-1.2*atr),4); c.tp1=round(price*1.03,4); c.tp2=round(price*1.06,4)
            c.entry_zone=f"{price*.995:.4f}–{price*1.005:.4f}"
        c.source_notes="Alpaca IEX + SEC + Nasdaq halts + GlobeNewswire/PR Newswire" + (" + FMP" if fmp_key else "")
        out.append(c)
    out.sort(key=lambda x:x.score,reverse=True)
    meta={"universe_assets":len(allowed),"snapshot_candidates":len(rows),"final_candidates":len(out),"elapsed_sec":round(time.time()-started,2),"source_status":{"alpaca":True,"sec":bool(sec),"news":bool(news),"halts":bool(halts),"fmp":bool(fmp_key)}}
    return pd.DataFrame([c.to_dict() for c in out]), meta
