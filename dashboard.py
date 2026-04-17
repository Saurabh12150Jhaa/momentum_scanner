"""
MOMENTUM STOCK SCANNER — Interactive Dashboard
================================================
Full automation of:
  1. Chart analysis (Candlestick + EMA + RSI + Volume)
  2. Fundamental verification (ROE, D/E, Growth, Quarterly results)
  3. Institutional holdings (Promoter / FII / DII / Public)
  4. Entry trigger detection & stop loss calculation
  5. Position sizing
  6. Watchlist & portfolio tracking

Launched via: uv run ~/momentum_scanner/launch_dashboard.py
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from bs4 import BeautifulSoup
from plotly.subplots import make_subplots

# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".cache"
RESULTS_DIR = BASE_DIR / "results"
WATCHLIST_FILE = CACHE_DIR / "watchlist.json"
PORTFOLIO_FILE = CACHE_DIR / "portfolio.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SCREENER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

st.set_page_config(
    page_title="Momentum Scanner",
    page_icon="\U0001F4C8",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ──────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1rem; }
    div[data-testid="stMetric"] {
        background-color: #0e1117;
        border: 1px solid #262730;
        border-radius: 8px;
        padding: 12px 16px;
    }
    div[data-testid="stMetric"] label { font-size: 0.85rem; }
    .pass-badge {
        background: #00c85340; color: #00c853;
        padding: 2px 10px; border-radius: 4px; font-weight: 700;
    }
    .fail-badge {
        background: #ff174440; color: #ff1744;
        padding: 2px 10px; border-radius: 4px; font-weight: 700;
    }
    .caution-badge {
        background: #ffab0040; color: #ffab00;
        padding: 2px 10px; border-radius: 4px; font-weight: 700;
    }
    section[data-testid="stSidebar"] > div { padding-top: 1rem; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════
# CHARTINK SCANNER
# ═══════════════════════════════════════════════════════════════════

SCAN_CLAUSES = {
    "momentum": (
        '( {cash} ( '
        'latest close > latest ema( close,50 ) and '
        'latest close > latest ema( close,150 ) and '
        'latest close > latest ema( close,200 ) and '
        'latest ema( close,50 ) > latest ema( close,150 ) and '
        'latest ema( close,150 ) > latest ema( close,200 ) and '
        'latest close / latest max( 250, close ) > 0.75 and '
        'latest close / latest min( 250, close ) > 1.30 and '
        'latest volume > latest sma( volume,20 ) and '
        'weekly rsi( 14 ) > 60 and '
        'market capitalization > 500 '
        ') )'
    ),
    "breakout": (
        '( {cash} ( '
        'latest close > latest ema( close,50 ) and '
        'latest close > latest ema( close,150 ) and '
        'latest close > latest ema( close,200 ) and '
        'latest ema( close,50 ) > latest ema( close,150 ) and '
        'latest close = latest max( 65 , close ) and '
        'latest volume > 1.5 * latest sma( volume,20 ) and '
        'market capitalization > 500 '
        ') )'
    ),
    "pullback": (
        '( {cash} ( '
        'latest ema( close,50 ) > latest ema( close,150 ) and '
        'latest ema( close,150 ) > latest ema( close,200 ) and '
        'latest close / latest ema( close,50 ) > 0.97 and '
        'latest close / latest ema( close,50 ) < 1.03 and '
        'latest close > 1 day ago close and '
        'latest volume > latest sma( volume,20 ) and '
        'weekly rsi( 14 ) > 55 and '
        'market capitalization > 500 '
        ') )'
    ),
}

SCAN_LABELS = {
    "momentum": "Momentum Universe",
    "breakout": "Breakout Entry",
    "pullback": "50-DMA Pullback",
}


@st.cache_data(ttl=1800, show_spinner=False)
def run_chartink_scan(scan_type: str) -> list[dict]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://chartink.com/screener",
    })
    r = session.get("https://chartink.com/screener", timeout=15)
    match = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
    if not match:
        match = re.search(r'content="([^"]+)"\s+name="csrf-token"', r.text)
    if not match:
        return []
    csrf = match.group(1)
    resp = session.post(
        "https://chartink.com/screener/process",
        data={"scan_clause": SCAN_CLAUSES[scan_type]},
        headers={"X-Csrf-Token": csrf, "X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    raw = resp.json().get("data", [])
    results = []
    for item in raw:
        sym = item.get("nsecode", item.get("stock_name", "")).strip()
        if not sym:
            continue
        results.append({
            "Symbol": sym,
            "Name": item.get("stock_name", sym),
            "Close": float(item.get("close", 0)),
            "Chg%": float(item.get("per_chg", 0)),
            "Volume": int(item.get("volume", 0)),
        })
    return results


# ═══════════════════════════════════════════════════════════════════
# MARKET DATA HELPERS
# ═══════════════════════════════════════════════════════════════════

def _safe_series(df, col):
    """Extract a clean Series from yfinance DataFrame (handles MultiIndex)."""
    s = df[col]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return s.dropna()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_market_regime():
    out = {"price": 0, "ema50": 0, "ema200": 0, "vix": 0, "regime": "UNKNOWN", "action": ""}
    try:
        nifty = yf.download("^NSEI", period="1y", progress=False)
        if nifty.empty:
            return out
        c = _safe_series(nifty, "Close")
        out["price"] = round(float(c.iloc[-1]), 0)
        out["ema50"] = round(float(c.ewm(span=50, adjust=False).mean().iloc[-1]), 0)
        out["ema200"] = round(float(c.ewm(span=200, adjust=False).mean().iloc[-1]), 0)
    except Exception:
        pass
    try:
        vix = yf.download("^INDIAVIX", period="5d", progress=False)
        if not vix.empty:
            out["vix"] = round(float(_safe_series(vix, "Close").iloc[-1]), 1)
    except Exception:
        pass
    above_200 = out["price"] > out["ema200"]
    above_50 = out["price"] > out["ema50"]
    ema_stack = out["ema50"] > out["ema200"]
    if above_200 and above_50 and ema_stack:
        out["regime"] = "STRONG BULL"
        out["action"] = "Full exposure 80-100%. Scan aggressively."
    elif above_200 and not above_50:
        out["regime"] = "MILD BULL"
        out["action"] = "Moderate 50-70%. Be selective."
    elif not above_200 and above_50:
        out["regime"] = "RECOVERY"
        out["action"] = "Cautious 30-50%. Highest conviction only."
    else:
        out["regime"] = "BEAR"
        out["action"] = "0-20%. Mostly cash. Avoid new longs."
    return out


@st.cache_data(ttl=900, show_spinner="Downloading price data...")
def get_stock_data(symbol: str, period: str = "1y") -> pd.DataFrame | None:
    try:
        df = yf.download(f"{symbol}.NS", period=period, progress=False)
        if df.empty or len(df) < 30:
            return None
        # Flatten MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner="Fetching fundamentals...")
def get_fundamentals(symbol: str) -> dict:
    try:
        t = yf.Ticker(f"{symbol}.NS")
        info = t.info or {}
        # Quarterly financials
        qf = t.quarterly_financials
        q_revenue, q_profit = [], []
        if qf is not None and not qf.empty:
            for col in qf.columns[:4]:
                rev = qf.loc["Total Revenue", col] if "Total Revenue" in qf.index else None
                npi = qf.loc["Net Income", col] if "Net Income" in qf.index else None
                q_revenue.append({"quarter": col.strftime("%b %Y"), "value": rev})
                q_profit.append({"quarter": col.strftime("%b %Y"), "value": npi})

        def _g(key, mult=1, rd=1):
            v = info.get(key)
            return round(v * mult, rd) if v is not None else None

        return {
            "name": info.get("shortName", info.get("longName", symbol)),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "mcap": info.get("marketCap", 0),
            "pe": _g("trailingPE"),
            "pb": _g("priceToBook"),
            "peg": _g("pegRatio", rd=2),
            "roe": _g("returnOnEquity", mult=100),
            "de": _g("debtToEquity", mult=0.01, rd=2),
            "rev_growth": _g("revenueGrowth", mult=100),
            "earn_growth": _g("earningsGrowth", mult=100),
            "operating_margin": _g("operatingMargins", mult=100),
            "profit_margin": _g("profitMargins", mult=100),
            "dividend_yield": _g("dividendYield", mult=100),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "promoter_pct": None,  # yfinance doesn't give this directly for Indian stocks
            "fii_pct": None,
            "dii_pct": None,
            "public_pct": None,
            "q_revenue": q_revenue,
            "q_profit": q_profit,
            "ok": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=3600, show_spinner="Fetching shareholding...")
def get_shareholding(symbol: str) -> dict:
    """Get full shareholding pattern from Screener.in, fall back to yfinance."""
    out = {
        "promoter": None, "fii": None, "dii": None, "public": None,
        "government": None, "num_shareholders": None,
        "holders": [],          # top institutional holders (yfinance)
        "quarterly": None,      # DataFrame: quarterly shareholding trend
        "source": None,
    }

    # ── Primary: Screener.in (has SEBI-mandated breakdown) ──
    try:
        out = _fetch_screener_shareholding(symbol, out)
    except Exception:
        pass

    # ── Fallback: yfinance (limited but still useful) ──
    if out["promoter"] is None:
        try:
            out = _fetch_yfinance_shareholding(symbol, out)
        except Exception:
            pass

    return out


def _fetch_screener_shareholding(symbol: str, out: dict) -> dict:
    """Scrape Screener.in for quarterly shareholding pattern."""
    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    resp = requests.get(url, headers=SCREENER_HEADERS, timeout=20)
    if resp.status_code == 404:
        # Try standalone (non-consolidated) URL
        url = f"https://www.screener.in/company/{symbol}/"
        resp = requests.get(url, headers=SCREENER_HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    section = soup.find("section", id="shareholding")
    if section is None:
        return out

    table = section.find("table")
    if table is None:
        return out

    # ── Parse headers (quarter labels) ──
    header_row = table.find("thead")
    if header_row:
        headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
    else:
        first_row = table.find("tr")
        headers = [c.get_text(strip=True) for c in first_row.find_all(["th", "td"])] if first_row else []

    # ── Parse body rows ──
    tbody = table.find("tbody") or table
    rows: list[list[str]] = []
    for tr in tbody.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)

    if not rows or not headers:
        return out

    # Build DataFrame
    max_cols = max(len(headers), max(len(r) for r in rows))
    headers += [f"Col_{i}" for i in range(len(headers), max_cols)]
    rows = [r + [""] * (max_cols - len(r)) for r in rows]
    df = pd.DataFrame(rows, columns=headers[:max_cols])

    # Clean: Screener uses "Promoters+" with a button element, strip trailing +
    first_col = df.columns[0]
    df[first_col] = df[first_col].str.replace(r"\+$", "", regex=True).str.strip()

    # Store the quarterly DataFrame
    out["quarterly"] = df
    out["source"] = "screener"

    # Extract latest quarter values
    quarter_cols = [c for c in df.columns[1:] if c.strip()]
    if quarter_cols:
        latest = quarter_cols[-1]
        for _, row in df.iterrows():
            category = str(row.iloc[0]).lower().strip()
            raw_val = str(row[latest]).replace(",", "").replace("%", "").strip()
            try:
                val = float(raw_val)
            except (ValueError, TypeError):
                continue
            if "promoter" in category:
                out["promoter"] = round(val, 2)
            elif "fii" in category or "foreign" in category:
                out["fii"] = round(val, 2)
            elif "dii" in category or ("institution" in category and "foreign" not in category):
                out["dii"] = round(val, 2)
            elif "government" in category:
                out["government"] = round(val, 2)
            elif "public" in category:
                out["public"] = round(val, 2)
            elif "shareholder" in category or "no." in category:
                try:
                    out["num_shareholders"] = raw_val
                except (ValueError, TypeError):
                    pass

    return out


def _fetch_yfinance_shareholding(symbol: str, out: dict) -> dict:
    """Fallback: extract what we can from yfinance."""
    t = yf.Ticker(f"{symbol}.NS")
    mh = t.major_holders
    if mh is not None and not mh.empty:
        for _, row in mh.iterrows():
            label = str(row.iloc[1]).lower() if len(row) > 1 else ""
            val = row.iloc[0]
            if isinstance(val, str) and "%" in val:
                val = float(val.replace("%", ""))
            elif isinstance(val, (int, float)):
                val = float(val)
            else:
                continue
            if "insider" in label or "promoter" in label:
                out["promoter"] = round(val, 2)
            elif "institution" in label and out["dii"] is None:
                out["dii"] = round(val, 2)
    ih = t.institutional_holders
    if ih is not None and not ih.empty:
        for _, row in ih.head(10).iterrows():
            out["holders"].append({
                "name": str(row.get("Holder", "Unknown"))[:40],
                "shares": int(row.get("Shares", 0)),
                "pct": round(float(row.get("pctHeld", 0)) * 100, 2) if row.get("pctHeld") else None,
            })
    if out["promoter"] is not None:
        out["source"] = "yfinance"
    return out


# ═══════════════════════════════════════════════════════════════════
# TECHNICAL ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h = _safe_series(df, "High")
    l = _safe_series(df, "Low")
    c = _safe_series(df, "Close")
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def determine_stage(df: pd.DataFrame) -> dict:
    """Weinstein stage analysis."""
    c = _safe_series(df, "Close")
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema150 = c.ewm(span=150, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    price = float(c.iloc[-1])
    e50, e150, e200 = float(ema50.iloc[-1]), float(ema150.iloc[-1]), float(ema200.iloc[-1])

    # 200 DMA slope (last 20 days)
    slope_200 = float(ema200.iloc[-1] - ema200.iloc[-20]) if len(ema200) >= 20 else 0

    if price > e50 > e150 > e200 and slope_200 > 0:
        return {"stage": 2, "label": "Stage 2 — Advancing", "color": "green",
                "desc": "Strong uptrend. EMA stack bullish, 200-DMA rising. IDEAL for buying."}
    elif price > e200 and slope_200 >= 0 and not (price > e50 > e150 > e200):
        return {"stage": 2, "label": "Stage 2 — Early / Pullback", "color": "green",
                "desc": "Above rising 200-DMA but EMAs not fully stacked. May be pulling back."}
    elif price > e200 and slope_200 < 0:
        return {"stage": 1, "label": "Stage 1 — Basing", "color": "orange",
                "desc": "Price above 200-DMA but it's declining. Accumulation phase. Wait."}
    elif price < e200 and slope_200 > 0:
        return {"stage": 3, "label": "Stage 3 — Topping", "color": "red",
                "desc": "Price broke below rising 200-DMA. Distribution. SELL or avoid."}
    else:
        return {"stage": 4, "label": "Stage 4 — Declining", "color": "red",
                "desc": "Price below falling 200-DMA. Downtrend. DO NOT buy."}


def detect_entry_signals(df: pd.DataFrame) -> list[dict]:
    """Detect actionable entry triggers."""
    signals = []
    c = _safe_series(df, "Close")
    v = _safe_series(df, "Volume")
    price = float(c.iloc[-1])

    ema50 = c.ewm(span=50, adjust=False).mean()
    ema150 = c.ewm(span=150, adjust=False).mean()
    vol_avg = v.rolling(20).mean()

    # Breakout: 65-day high on volume
    if len(c) >= 65:
        high_65 = float(c.iloc[-65:].max())
        va_last = float(vol_avg.iloc[-1]) if not np.isnan(vol_avg.iloc[-1]) else 0
        if va_last > 0 and price >= high_65 * 0.99 and float(v.iloc[-1]) > 1.5 * va_last:
            vol_ratio = float(v.iloc[-1]) / va_last
            signals.append({
                "type": "BREAKOUT",
                "color": "green",
                "desc": f"Hitting 65-day high ({high_65:.2f}) on {vol_ratio:.1f}x avg volume",
            })

    # Pullback to 50-DMA
    e50 = float(ema50.iloc[-1])
    dist_50 = abs(price - e50) / e50 * 100
    if dist_50 <= 3 and float(c.iloc[-1]) > float(c.iloc[-2]):
        signals.append({
            "type": "50-DMA PULLBACK",
            "color": "blue",
            "desc": f"Within {dist_50:.1f}% of 50-DMA ({e50:.2f}), bouncing on green candle",
        })

    # VCP: Decreasing volatility (range contraction)
    if len(c) >= 40:
        ranges = []
        for i in range(4):
            sl = c.iloc[-(i + 1) * 10: len(c) - i * 10] if i > 0 else c.iloc[-10:]
            if len(sl) >= 5:
                ranges.append((float(sl.max()) - float(sl.min())) / float(sl.mean()) * 100)
        if len(ranges) >= 3 and ranges[0] < ranges[1] < ranges[2]:
            signals.append({
                "type": "VCP PATTERN",
                "color": "purple",
                "desc": f"Volatility contracting: {ranges[2]:.1f}% -> {ranges[1]:.1f}% -> {ranges[0]:.1f}%",
            })

    # 52-week high breakout
    if len(c) >= 200:
        high_52 = float(c.max())
        if price >= high_52 * 0.98:
            signals.append({
                "type": "52-WEEK HIGH",
                "color": "green",
                "desc": f"Near/at 52-week high of {high_52:.2f}",
            })

    # Earnings gap-up hold (check last 5 days for large gap)
    if len(c) >= 5:
        for i in range(1, 5):
            gap = (float(c.iloc[-i]) - float(c.iloc[-i - 1])) / float(c.iloc[-i - 1]) * 100
            if gap >= 3 and all(float(c.iloc[-j]) >= float(c.iloc[-i]) * 0.97 for j in range(1, i)):
                signals.append({
                    "type": "GAP-UP HOLD",
                    "color": "green",
                    "desc": f"Gapped up {gap:.1f}% {i} day(s) ago and holding",
                })
                break

    if not signals:
        signals.append({
            "type": "NO TRIGGER",
            "color": "gray",
            "desc": "No clear entry trigger today. Add to watchlist and wait.",
        })
    return signals


def calculate_stop_losses(df: pd.DataFrame) -> list[dict]:
    """Calculate stop losses using multiple methods."""
    c = _safe_series(df, "Close")
    price = float(c.iloc[-1])
    stops = []

    # Method 1: Fixed 8% below
    sl_fixed = price * 0.92
    stops.append({"method": "Fixed 8%", "level": sl_fixed, "risk": 8.0,
                  "desc": "Simple 8% below current price"})

    # Method 2: Fixed 10% below
    sl_fixed10 = price * 0.90
    stops.append({"method": "Fixed 10%", "level": sl_fixed10, "risk": 10.0,
                  "desc": "Simple 10% below current price"})

    # Method 3: Below 50-DMA
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])
    sl_ema50 = ema50 * 0.98  # 2% below 50-DMA
    risk_ema50 = (price - sl_ema50) / price * 100
    stops.append({"method": "Below 50-DMA", "level": sl_ema50, "risk": round(risk_ema50, 1),
                  "desc": f"2% below 50-DMA ({ema50:.2f})"})

    # Method 4: 2x ATR
    atr = compute_atr(df)
    if not atr.empty and not np.isnan(atr.iloc[-1]):
        atr_val = float(atr.iloc[-1])
        sl_atr = price - 2 * atr_val
        risk_atr = (price - sl_atr) / price * 100
        stops.append({"method": "2x ATR", "level": sl_atr, "risk": round(risk_atr, 1),
                      "desc": f"2x ATR ({atr_val:.2f}) below price"})

    # Method 5: Recent swing low (lowest low in last 20 days)
    if len(df) >= 20:
        low_col = _safe_series(df, "Low")
        swing_low = float(low_col.iloc[-20:].min())
        sl_swing = swing_low * 0.99  # 1% below swing low
        risk_swing = (price - sl_swing) / price * 100
        stops.append({"method": "Swing Low", "level": sl_swing, "risk": round(risk_swing, 1),
                      "desc": f"1% below 20-day swing low ({swing_low:.2f})"})

    return stops


# ═══════════════════════════════════════════════════════════════════
# CHARTING
# ═══════════════════════════════════════════════════════════════════

def create_stock_chart(df: pd.DataFrame, symbol: str, stop_levels: list[dict] = None) -> go.Figure:
    c = _safe_series(df, "Close")
    o = _safe_series(df, "Open")
    h = _safe_series(df, "High")
    l = _safe_series(df, "Low")
    v = _safe_series(df, "Volume")

    ema50 = c.ewm(span=50, adjust=False).mean()
    ema150 = c.ewm(span=150, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    rsi = compute_rsi(c)

    colors = ["#26a69a" if cl >= op else "#ef5350" for cl, op in zip(c, o)]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=[f"{symbol} — Price + EMAs", "Volume", "RSI (14)"],
    )

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index, open=o, high=h, low=l, close=c,
        name="Price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ), row=1, col=1)

    # EMAs
    fig.add_trace(go.Scatter(x=df.index, y=ema50, name="EMA 50",
                             line=dict(color="#42a5f5", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=ema150, name="EMA 150",
                             line=dict(color="#ffa726", width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=ema200, name="EMA 200",
                             line=dict(color="#ef5350", width=2)), row=1, col=1)

    # Stop loss lines
    if stop_levels:
        for sl in stop_levels[:3]:
            fig.add_hline(
                y=sl["level"], line_dash="dash", line_color="#ff1744",
                annotation_text=f"SL: {sl['method']} ({sl['level']:.2f})",
                annotation_position="bottom right",
                row=1, col=1, opacity=0.5,
            )

    # Volume
    fig.add_trace(go.Bar(x=df.index, y=v, name="Volume",
                         marker_color=colors, opacity=0.7), row=2, col=1)

    vol_avg = v.rolling(20).mean()
    fig.add_trace(go.Scatter(x=df.index, y=vol_avg, name="Vol 20-SMA",
                             line=dict(color="#ffab00", width=1)), row=2, col=1)

    # RSI
    fig.add_trace(go.Scatter(x=df.index, y=rsi, name="RSI",
                             line=dict(color="#ab47bc", width=1.5)), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#ef5350", row=3, col=1, opacity=0.5)
    fig.add_hline(y=30, line_dash="dot", line_color="#26a69a", row=3, col=1, opacity=0.5)
    fig.add_hrect(y0=30, y1=70, fillcolor="#42a5f5", opacity=0.05, row=3, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=700,
        margin=dict(l=50, r=20, t=40, b=20),
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    fig.update_xaxes(type="category", nticks=20, row=3, col=1)
    fig.update_xaxes(type="category", nticks=20, row=2, col=1)
    fig.update_xaxes(type="category", nticks=20, row=1, col=1)

    return fig


def create_shareholding_pie(sh: dict) -> go.Figure | None:
    """Donut chart of latest shareholding breakdown."""
    labels, values, colors = [], [], []
    color_map = {
        "Promoters": "#42a5f5",
        "FIIs": "#ffa726",
        "DIIs": "#66bb6a",
        "Government": "#ab47bc",
        "Public": "#ef5350",
    }
    for key, label in [("promoter", "Promoters"), ("fii", "FIIs"), ("dii", "DIIs"),
                        ("government", "Government"), ("public", "Public")]:
        if sh.get(key) is not None and sh[key] > 0:
            labels.append(label)
            values.append(sh[key])
            colors.append(color_map.get(label, "#78909c"))
    remainder = 100 - sum(values)
    if remainder > 1:
        labels.append("Other")
        values.append(round(remainder, 2))
        colors.append("#78909c")
    if not values:
        return None
    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        hole=0.45,
        marker=dict(colors=colors),
        textinfo="label+percent",
    ))
    fig.update_layout(
        template="plotly_dark", height=320,
        margin=dict(l=10, r=10, t=35, b=10),
        title_text="Latest Shareholding",
        showlegend=False,
    )
    return fig


def create_shareholding_trend(sh: dict) -> go.Figure | None:
    """Stacked area/bar chart of quarterly shareholding trend."""
    qdf = sh.get("quarterly")
    if qdf is None or qdf.empty:
        return None

    first_col = qdf.columns[0]
    quarter_cols = [c for c in qdf.columns[1:] if c.strip()]
    if not quarter_cols:
        return None

    # Categories to plot (skip "No. of Shareholders" row)
    color_map = {
        "promoters": "#42a5f5",
        "fiis": "#ffa726",
        "diis": "#66bb6a",
        "government": "#ab47bc",
        "public": "#ef5350",
    }
    fig = go.Figure()
    for _, row in qdf.iterrows():
        cat = str(row[first_col]).strip()
        cat_lower = cat.lower()
        if "shareholder" in cat_lower or "no." in cat_lower:
            continue
        y_vals = []
        for qc in quarter_cols:
            raw = str(row[qc]).replace(",", "").replace("%", "").strip()
            try:
                y_vals.append(float(raw))
            except (ValueError, TypeError):
                y_vals.append(0)
        color = "#78909c"
        for key, c in color_map.items():
            if key in cat_lower:
                color = c
                break
        fig.add_trace(go.Bar(
            x=quarter_cols, y=y_vals, name=cat,
            marker_color=color,
        ))

    fig.update_layout(
        barmode="stack",
        template="plotly_dark", height=350,
        margin=dict(l=40, r=10, t=35, b=40),
        title_text="Quarterly Shareholding Trend",
        yaxis_title="%",
        xaxis_title="Quarter",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# ═══════════════════════════════════════════════════════════════════
# WATCHLIST & PORTFOLIO PERSISTENCE
# ═══════════════════════════════════════════════════════════════════

def load_watchlist() -> list[str]:
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text())
    return []


def save_watchlist(wl: list[str]):
    WATCHLIST_FILE.write_text(json.dumps(sorted(set(wl))))


def load_portfolio() -> list[dict]:
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    return []


def save_portfolio(pf: list[dict]):
    PORTFOLIO_FILE.write_text(json.dumps(pf, indent=2, default=str))


# ═══════════════════════════════════════════════════════════════════
# MAIN APP
# ═══════════════════════════════════════════════════════════════════

def main():
    # ── Session state init ──
    if "watchlist" not in st.session_state:
        st.session_state.watchlist = load_watchlist()
    if "portfolio" not in st.session_state:
        st.session_state.portfolio = load_portfolio()
    if "selected_stock" not in st.session_state:
        st.session_state.selected_stock = None
    if "scan_results" not in st.session_state:
        st.session_state.scan_results = {}

    # ── Sidebar ──
    with st.sidebar:
        st.markdown("### :chart_with_upwards_trend: Momentum Scanner")
        st.caption(f"Scan date: {datetime.now().strftime('%d %b %Y, %H:%M')}")

        st.divider()
        st.markdown("##### Capital & Risk")
        capital = st.number_input("Total Capital (Rs.)", value=1000000, step=100000, format="%d")
        risk_pct = st.slider("Risk per trade (%)", 1.0, 5.0, 2.0, 0.5)
        max_positions = st.slider("Max positions", 4, 20, 10)

        st.divider()
        st.markdown("##### Watchlist")
        if st.session_state.watchlist:
            for sym in st.session_state.watchlist:
                col_w1, col_w2 = st.columns([3, 1])
                with col_w1:
                    if st.button(sym, key=f"wl_{sym}", use_container_width=True):
                        st.session_state.selected_stock = sym
                with col_w2:
                    if st.button(":x:", key=f"wl_del_{sym}"):
                        st.session_state.watchlist.remove(sym)
                        save_watchlist(st.session_state.watchlist)
                        st.rerun()
        else:
            st.caption("No stocks in watchlist yet.")

        new_sym = st.text_input("Add to watchlist", placeholder="e.g. TITAN")
        if new_sym:
            clean = new_sym.strip().upper()
            if clean and clean not in st.session_state.watchlist:
                st.session_state.watchlist.append(clean)
                save_watchlist(st.session_state.watchlist)
                st.rerun()

        st.divider()
        st.markdown("##### Quick Links")
        st.markdown("[Chartink Screener](https://chartink.com/screener)")
        st.markdown("[Screener.in](https://www.screener.in/)")
        st.markdown("[Tijori Finance](https://www.tijorifinance.com/)")
        st.markdown("[TradingView India](https://www.tradingview.com/markets/stocks-india/)")

    # ── Header ──
    st.markdown("## :chart_with_upwards_trend: Momentum Stock Scanner")
    st.caption("Minervini SEPA | Weinstein Stages | CANSLIM | Dual Momentum | Jegadeesh-Titman")

    # ── Market Regime ──
    regime = fetch_market_regime()
    regime_colors = {"STRONG BULL": "green", "MILD BULL": "orange", "RECOVERY": "orange", "BEAR": "red"}
    r_color = regime_colors.get(regime["regime"], "gray")

    col_r1, col_r2, col_r3, col_r4, col_r5 = st.columns(5)
    col_r1.metric("Nifty 50", f"{regime['price']:,.0f}",
                  delta=f"{'Above' if regime['price'] > regime['ema200'] else 'Below'} 200-DMA")
    col_r2.metric("200-DMA", f"{regime['ema200']:,.0f}")
    col_r3.metric("50-DMA", f"{regime['ema50']:,.0f}")
    vix_label = "Low" if regime["vix"] < 15 else ("Normal" if regime["vix"] < 20 else "High")
    col_r4.metric("India VIX", f"{regime['vix']:.1f}", delta=vix_label,
                  delta_color="inverse" if regime["vix"] > 20 else "off")
    col_r5.metric("Regime", regime["regime"])

    if regime["regime"] == "BEAR":
        st.warning(f"**BEAR market regime.** {regime['action']}", icon="\u26A0\uFE0F")
    elif regime["regime"] in ("RECOVERY", "MILD BULL"):
        st.info(f"**{regime['regime']}** — {regime['action']}", icon="\u2139\uFE0F")
    else:
        st.success(f"**{regime['regime']}** — {regime['action']}", icon="\u2705")

    st.divider()

    # ── Scan Results ──
    st.markdown("### Scan Results")

    scan_btn = st.button(":mag: Run Scans Now", type="primary", use_container_width=True)
    if scan_btn or not st.session_state.scan_results:
        with st.spinner("Running scans via Chartink..."):
            for stype in ("momentum", "breakout", "pullback"):
                try:
                    st.session_state.scan_results[stype] = run_chartink_scan(stype)
                except Exception as e:
                    st.session_state.scan_results[stype] = []
                    st.warning(f"Scan '{stype}' failed: {e}")

    tab_m, tab_b, tab_p = st.tabs([
        f":rocket: Momentum ({len(st.session_state.scan_results.get('momentum', []))})",
        f":boom: Breakout ({len(st.session_state.scan_results.get('breakout', []))})",
        f":arrow_heading_down: Pullback ({len(st.session_state.scan_results.get('pullback', []))})",
    ])

    def _render_scan_tab(scan_type, tab):
        with tab:
            results = st.session_state.scan_results.get(scan_type, [])
            if not results:
                st.info("No stocks found for this scan.")
                return

            df = pd.DataFrame(results)

            # Color the Chg% column
            def _color_chg(val):
                color = "#26a69a" if val >= 0 else "#ef5350"
                return f"color: {color}; font-weight: bold"

            styled = df.style.map(_color_chg, subset=["Chg%"])
            styled = styled.format({"Close": "{:,.2f}", "Chg%": "{:+.2f}%", "Volume": "{:,.0f}"})

            st.dataframe(styled, use_container_width=True, height=min(400, 40 + 35 * len(df)))

            # Stock selector
            symbols = [r["Symbol"] for r in results]
            col_sel, col_wl = st.columns([3, 1])
            with col_sel:
                sel = st.selectbox(
                    "Select a stock to analyze",
                    options=[""] + symbols,
                    key=f"sel_{scan_type}",
                    format_func=lambda x: f"{x} — Rs.{next((r['Close'] for r in results if r['Symbol'] == x), '')}" if x else "Click to select...",
                )
            with col_wl:
                if sel and st.button(":star: Add to Watchlist", key=f"wl_add_{scan_type}"):
                    if sel not in st.session_state.watchlist:
                        st.session_state.watchlist.append(sel)
                        save_watchlist(st.session_state.watchlist)
                        st.toast(f"Added {sel} to watchlist!")

            if sel:
                st.session_state.selected_stock = sel

    _render_scan_tab("momentum", tab_m)
    _render_scan_tab("breakout", tab_b)
    _render_scan_tab("pullback", tab_p)

    # ── Stock Deep Dive ──
    st.divider()

    # Manual stock input
    manual = st.text_input("Or type any NSE symbol to analyze", placeholder="e.g. RELIANCE, TCS, INFY")
    if manual:
        st.session_state.selected_stock = manual.strip().upper()

    selected = st.session_state.selected_stock
    if not selected:
        st.info("Select a stock from scan results above or type a symbol to see full analysis.")
        _render_portfolio_section(capital, risk_pct)
        return

    st.markdown(f"## :mag_right: Deep Dive: **{selected}**")

    # External links
    lnk1, lnk2, lnk3, lnk4 = st.columns(4)
    lnk1.markdown(f"[:bar_chart: Screener.in](https://www.screener.in/company/{selected}/)")
    lnk2.markdown(f"[:office_building: Tijori](https://www.tijorifinance.com/in/company/{selected}/)")
    lnk3.markdown(f"[:chart_with_upwards_trend: TradingView](https://www.tradingview.com/chart/?symbol=NSE%3A{selected})")
    lnk4.markdown(f"[:bank: Dhan](https://web.dhan.co/)")

    # Fetch data
    df_price = get_stock_data(selected)
    if df_price is None or df_price.empty:
        st.error(f"Could not fetch price data for {selected}. Check the symbol.")
        return

    fund = get_fundamentals(selected)
    sh = get_shareholding(selected)
    stage = determine_stage(df_price)
    signals = detect_entry_signals(df_price)
    stop_losses = calculate_stop_losses(df_price)

    # ── Stage & Signal Summary ──
    col_stage, col_signals = st.columns(2)
    with col_stage:
        badge_class = "pass-badge" if stage["stage"] == 2 else ("caution-badge" if stage["stage"] == 1 else "fail-badge")
        st.markdown(f'**Weinstein Stage:** <span class="{badge_class}">{stage["label"]}</span>',
                    unsafe_allow_html=True)
        st.caption(stage["desc"])

    with col_signals:
        st.markdown("**Entry Signals:**")
        for sig in signals:
            badge = "pass-badge" if sig["color"] == "green" else ("caution-badge" if sig["color"] in ("blue", "purple") else "fail-badge")
            st.markdown(f'<span class="{badge}">{sig["type"]}</span> {sig["desc"]}',
                        unsafe_allow_html=True)

    # ── Chart ──
    st.markdown("#### Price Chart")
    chart_period = st.radio("Period", ["6mo", "1y", "2y"], index=1, horizontal=True, key="chart_period")
    df_chart = get_stock_data(selected, period=chart_period)
    if df_chart is not None:
        fig = create_stock_chart(df_chart, selected, stop_losses)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("Chart data unavailable for selected period.")

    # ── Fundamentals ──
    st.markdown("#### Fundamental Metrics")
    if fund.get("ok"):
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)
        fc1.metric("Market Cap", f"{fund['mcap'] / 1e7:,.0f} Cr" if fund["mcap"] else "N/A")
        fc2.metric("PE Ratio", f"{fund['pe']:.1f}" if fund["pe"] else "N/A")
        fc3.metric("ROE", f"{fund['roe']:.1f}%" if fund["roe"] is not None else "N/A",
                   delta="Good" if fund["roe"] and fund["roe"] > 15 else ("Low" if fund["roe"] else None),
                   delta_color="normal" if fund["roe"] and fund["roe"] > 15 else "inverse")
        fc4.metric("Debt/Equity", f"{fund['de']:.2f}" if fund["de"] is not None else "N/A",
                   delta="Safe" if fund["de"] is not None and fund["de"] < 1 else ("High" if fund["de"] else None),
                   delta_color="normal" if fund["de"] is not None and fund["de"] < 1 else "inverse")
        fc5.metric("Rev Growth", f"{fund['rev_growth']:.1f}%" if fund["rev_growth"] is not None else "N/A",
                   delta="Growing" if fund["rev_growth"] and fund["rev_growth"] > 10 else None)
        fc6.metric("Earnings Growth", f"{fund['earn_growth']:.1f}%" if fund["earn_growth"] is not None else "N/A",
                   delta="Growing" if fund["earn_growth"] and fund["earn_growth"] > 15 else None)

        fc7, fc8, fc9, fc10, fc11, fc12 = st.columns(6)
        fc7.metric("PB Ratio", f"{fund['pb']:.2f}" if fund["pb"] else "N/A")
        fc8.metric("PEG Ratio", f"{fund['peg']:.2f}" if fund["peg"] else "N/A",
                   delta="Attractive" if fund["peg"] and fund["peg"] < 1.5 else None)
        fc9.metric("OPM", f"{fund['operating_margin']:.1f}%" if fund["operating_margin"] is not None else "N/A")
        fc10.metric("NPM", f"{fund['profit_margin']:.1f}%" if fund["profit_margin"] is not None else "N/A")
        fc11.metric("Div Yield", f"{fund['dividend_yield']:.1f}%" if fund["dividend_yield"] is not None else "N/A")
        fc12.metric("Sector", fund.get("sector", "N/A")[:15])

        # Fundamental pass/fail summary
        checks = []
        if fund["roe"] is not None:
            checks.append(("ROE > 12%", fund["roe"] >= 12))
        if fund["de"] is not None:
            checks.append(("D/E < 1.5", fund["de"] <= 1.5))
        if fund["rev_growth"] is not None:
            checks.append(("Rev Growth > 10%", fund["rev_growth"] >= 10))
        if fund["earn_growth"] is not None:
            checks.append(("Earn Growth > 15%", fund["earn_growth"] >= 15))
        if fund["peg"] is not None:
            checks.append(("PEG < 2", fund["peg"] <= 2))

        if checks:
            passed = sum(1 for _, ok in checks if ok)
            total = len(checks)
            score_color = "pass-badge" if passed == total else ("caution-badge" if passed >= total / 2 else "fail-badge")
            check_str = " | ".join(
                f'<span class="{"pass-badge" if ok else "fail-badge"}">{name}</span>'
                for name, ok in checks
            )
            st.markdown(
                f'**Fundamental Score: {passed}/{total}** — {check_str}',
                unsafe_allow_html=True,
            )

        # Quarterly results
        if fund.get("q_revenue"):
            st.markdown("##### Quarterly Results")
            q_data = []
            for rev, prof in zip(fund["q_revenue"], fund["q_profit"]):
                q_data.append({
                    "Quarter": rev["quarter"],
                    "Revenue (Cr)": f"{rev['value'] / 1e7:,.0f}" if (rev["value"] is not None and not (isinstance(rev["value"], float) and np.isnan(rev["value"]))) else "N/A",
                    "Profit (Cr)": f"{prof['value'] / 1e7:,.0f}" if (prof["value"] is not None and not (isinstance(prof["value"], float) and np.isnan(prof["value"]))) else "N/A",
                })
            st.dataframe(pd.DataFrame(q_data), use_container_width=True, hide_index=True)
    else:
        st.warning(f"Fundamental data unavailable: {fund.get('error', 'unknown error')}")

    # ── Shareholding ──
    st.markdown("#### Shareholding Pattern")
    if sh.get("source"):
        src_label = "Screener.in" if sh["source"] == "screener" else "yfinance"
        st.caption(f"Source: {src_label}")

    # Latest breakdown metrics row
    if sh.get("promoter") is not None:
        sh_cols = st.columns(6)
        sh_cols[0].metric("Promoters", f"{sh['promoter']:.2f}%")
        sh_cols[1].metric("FIIs", f"{sh['fii']:.2f}%" if sh.get("fii") is not None else "N/A")
        sh_cols[2].metric("DIIs", f"{sh['dii']:.2f}%" if sh.get("dii") is not None else "N/A")
        sh_cols[3].metric("Government", f"{sh['government']:.2f}%" if sh.get("government") is not None else "N/A")
        sh_cols[4].metric("Public", f"{sh['public']:.2f}%" if sh.get("public") is not None else "N/A")
        sh_cols[5].metric("Shareholders", sh.get("num_shareholders", "N/A"))

    # Tabs for charts + raw data
    sh_tab_pie, sh_tab_trend, sh_tab_data = st.tabs(["Pie Chart", "Quarterly Trend", "Raw Data"])

    with sh_tab_pie:
        sh_fig = create_shareholding_pie(sh)
        if sh_fig:
            st.plotly_chart(sh_fig, use_container_width=True)
        else:
            st.info("Shareholding breakdown not available. Check Screener.in/Tijori.")

    with sh_tab_trend:
        trend_fig = create_shareholding_trend(sh)
        if trend_fig:
            st.plotly_chart(trend_fig, use_container_width=True)
        else:
            st.info("Quarterly trend requires Screener.in data. Try refreshing.")

    with sh_tab_data:
        qdf = sh.get("quarterly")
        if qdf is not None and not qdf.empty:
            st.dataframe(qdf, use_container_width=True, hide_index=True)
        elif sh.get("holders"):
            st.markdown("**Top Institutional Holders (yfinance):**")
            holders_df = pd.DataFrame(sh["holders"])
            if not holders_df.empty:
                holders_df["shares"] = holders_df["shares"].apply(lambda x: f"{x:,.0f}")
                holders_df["pct"] = holders_df["pct"].apply(lambda x: f"{x:.2f}%" if x else "N/A")
                st.dataframe(holders_df, use_container_width=True, hide_index=True)
        else:
            st.info("No shareholding data available. Check [Screener.in](https://www.screener.in) / [Tijori Finance](https://www.tijorifinance.com).")

    # ── Stop Loss & Entry Analysis ──
    st.markdown("#### Stop Loss & Entry Analysis")
    col_sl, col_pos = st.columns(2)

    with col_sl:
        st.markdown("**Stop Loss Levels (choose one):**")
        sl_df = pd.DataFrame(stop_losses)
        sl_df["level"] = sl_df["level"].apply(lambda x: f"Rs.{x:,.2f}")
        sl_df["risk"] = sl_df["risk"].apply(lambda x: f"{x:.1f}%")
        sl_df.columns = ["Method", "Stop Loss", "Risk %", "Description"]
        st.dataframe(sl_df, use_container_width=True, hide_index=True)

        # Recommendation
        recommended = min(stop_losses, key=lambda x: abs(x["risk"] - 8))
        st.success(f"**Recommended:** {recommended['method']} at Rs.{recommended['level']:,.2f} ({recommended['risk']:.1f}% risk)")

    with col_pos:
        st.markdown("**Position Sizing Calculator:**")
        close_price = float(_safe_series(df_price, "Close").iloc[-1])

        entry_price = st.number_input("Entry Price (Rs.)", value=round(close_price, 2), step=1.0, key="entry")
        chosen_sl = st.selectbox(
            "Choose Stop Loss Method",
            options=[f"{s['method']} — Rs.{s['level']:,.2f} ({s['risk']:.1f}%)" for s in stop_losses],
            key="sl_choice",
        )
        sl_idx = [f"{s['method']} — Rs.{s['level']:,.2f} ({s['risk']:.1f}%)" for s in stop_losses].index(chosen_sl)
        sl_price = stop_losses[sl_idx]["level"]

        risk_per_share = entry_price - sl_price
        shares = 0
        target_2r = 0.0
        target_3r = 0.0
        if risk_per_share > 0:
            risk_amount = capital * (risk_pct / 100)
            shares = int(risk_amount / risk_per_share)
            position_value = shares * entry_price
            position_pct = (position_value / capital) * 100
            max_loss = shares * risk_per_share

            # Targets
            target_2r = entry_price + 2 * risk_per_share
            target_3r = entry_price + 3 * risk_per_share

            st.markdown(
                f"| Metric | Value |\n"
                f"|---|---|\n"
                f"| **Shares to buy** | **{shares:,}** |\n"
                f"| **Position size** | Rs.{position_value:,.0f} ({position_pct:.1f}% of capital) |\n"
                f"| **Max loss** | Rs.{max_loss:,.0f} ({risk_pct:.1f}% of capital) |\n"
                f"| **Stop loss** | Rs.{sl_price:,.2f} |\n"
                f"| **Target 2:1 RR** | Rs.{target_2r:,.2f} (+{(target_2r/entry_price-1)*100:.1f}%) |\n"
                f"| **Target 3:1 RR** | Rs.{target_3r:,.2f} (+{(target_3r/entry_price-1)*100:.1f}%) |"
            )

            # Warn if position is too large
            max_per_stock = capital / max_positions
            if position_value > max_per_stock:
                st.warning(f"Position exceeds {100/max_positions:.0f}% of capital (1/{max_positions} max). Consider reducing to {int(max_per_stock / entry_price)} shares.")
        else:
            st.error("Stop loss must be below entry price.")

    # ── Add to Portfolio ──
    st.divider()
    st.markdown("#### Add to Portfolio")
    col_a1, col_a2, col_a3, col_a4 = st.columns(4)
    with col_a1:
        add_entry = st.number_input("Entry Price", value=round(close_price, 2), key="pf_entry")
    with col_a2:
        add_qty = st.number_input("Quantity", value=shares if risk_per_share > 0 else 0, min_value=0, key="pf_qty")
    with col_a3:
        add_sl = st.number_input("Stop Loss", value=round(sl_price, 2), key="pf_sl")
    with col_a4:
        add_target = st.number_input("Target", value=round(target_2r if risk_per_share > 0 else 0, 2), key="pf_tgt")

    if st.button(":heavy_plus_sign: Add Position to Portfolio", key="add_pf"):
        new_pos = {
            "symbol": selected,
            "entry": add_entry,
            "qty": add_qty,
            "sl": add_sl,
            "target": add_target,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
        st.session_state.portfolio.append(new_pos)
        save_portfolio(st.session_state.portfolio)
        st.success(f"Added {selected} to portfolio!")
        st.rerun()

    # ── Portfolio Section ──
    _render_portfolio_section(capital, risk_pct, max_positions)


def _render_portfolio_section(capital: float, risk_pct: float, max_positions: int = 10):
    """Render the portfolio tracker at the bottom."""
    st.divider()
    st.markdown("### :briefcase: Portfolio Tracker")

    portfolio = st.session_state.portfolio
    if not portfolio:
        st.info("No positions in portfolio yet. Analyze a stock and add it above.")
        return

    if len(portfolio) >= max_positions:
        st.warning(f"Portfolio has {len(portfolio)}/{max_positions} positions (max). Avoid adding new positions.")
    else:
        st.caption(f"Positions: {len(portfolio)}/{max_positions}")

    # Fetch current prices for all portfolio stocks
    pf_data = []
    for pos in portfolio:
        sym = pos["symbol"]
        try:
            df = get_stock_data(sym, period="5d")
            if df is not None:
                current = float(_safe_series(df, "Close").iloc[-1])
            else:
                current = pos["entry"]
        except Exception:
            current = pos["entry"]

        entry = pos["entry"]
        qty = pos["qty"]
        sl = pos["sl"]
        target = pos["target"]
        pnl = (current - entry) * qty
        pnl_pct = (current / entry - 1) * 100
        sl_hit = current <= sl
        target_hit = current >= target

        status = "SL HIT!" if sl_hit else ("TARGET HIT!" if target_hit else "Active")

        pf_data.append({
            "Symbol": sym,
            "Entry": f"{entry:,.2f}",
            "CMP": f"{current:,.2f}",
            "Qty": qty,
            "P&L": f"{pnl:+,.0f}",
            "P&L %": f"{pnl_pct:+.1f}%",
            "Stop Loss": f"{sl:,.2f}",
            "Target": f"{target:,.2f}",
            "Date": pos["date"],
            "Status": status,
        })

    pf_df = pd.DataFrame(pf_data)

    def _style_pf(row):
        styles = [""] * len(row)
        # Color P&L
        pnl_val = float(row["P&L"].replace(",", "").replace("+", ""))
        color = "#26a69a" if pnl_val >= 0 else "#ef5350"
        pnl_idx = list(row.index).index("P&L")
        pnl_pct_idx = list(row.index).index("P&L %")
        styles[pnl_idx] = f"color: {color}; font-weight: bold"
        styles[pnl_pct_idx] = f"color: {color}; font-weight: bold"
        # Color status
        status_idx = list(row.index).index("Status")
        if row["Status"] == "SL HIT!":
            styles[status_idx] = "color: #ef5350; font-weight: bold"
        elif row["Status"] == "TARGET HIT!":
            styles[status_idx] = "color: #26a69a; font-weight: bold"
        return styles

    st.dataframe(pf_df.style.apply(_style_pf, axis=1), use_container_width=True, hide_index=True)

    # Portfolio summary
    total_invested = sum(p["entry"] * p["qty"] for p in portfolio)
    total_current = 0
    for pos, row in zip(portfolio, pf_data):
        cmp = float(row["CMP"].replace(",", ""))
        total_current += cmp * pos["qty"]
    total_pnl = total_current - total_invested
    total_pnl_pct = (total_current / total_invested - 1) * 100 if total_invested else 0

    col_p1, col_p2, col_p3, col_p4 = st.columns(4)
    col_p1.metric("Invested", f"Rs.{total_invested:,.0f}")
    col_p2.metric("Current Value", f"Rs.{total_current:,.0f}")
    col_p3.metric("Total P&L", f"Rs.{total_pnl:+,.0f}", delta=f"{total_pnl_pct:+.1f}%")
    col_p4.metric("Positions", len(portfolio))

    # Remove position button
    if st.button(":wastebasket: Clear All Positions", key="clear_pf"):
        st.session_state.portfolio = []
        save_portfolio([])
        st.rerun()

    # Remove individual position
    syms_in_pf = [p["symbol"] for p in portfolio]
    remove_sym = st.selectbox("Remove a position", [""] + syms_in_pf, key="remove_pf")
    if remove_sym and st.button(f"Remove {remove_sym}", key="remove_pf_btn"):
        st.session_state.portfolio = [p for p in portfolio if p["symbol"] != remove_sym]
        save_portfolio(st.session_state.portfolio)
        st.rerun()


if __name__ == "__main__":
    main()
