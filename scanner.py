#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "yfinance>=0.2.31",
#     "pandas>=2.0",
#     "rich>=13.0",
#     "requests>=2.31",
#     "numpy>=1.24",
# ]
# ///
"""
MOMENTUM STOCK SCANNER - Indian Markets (NSE)
=============================================
Based on: Minervini SEPA, Weinstein Stage Analysis, O'Neil CANSLIM,
          Antonacci Dual Momentum, Jegadeesh-Titman Momentum Factor

Usage:
    uv run scanner.py                       # Run all scans
    uv run scanner.py momentum              # Momentum universe scan (weekly)
    uv run scanner.py breakout              # Breakout entry scan (daily)
    uv run scanner.py pullback              # 50-DMA pullback scan (daily)
    uv run scanner.py all                   # Run all three scans
    uv run scanner.py all -f                # All scans + fundamental check
    uv run scanner.py all --export          # All scans + save CSV
    uv run scanner.py all --local           # Force yfinance (skip Chartink)
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".cache"
RESULTS_DIR = BASE_DIR / "results"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Cache expiry (hours) - price data cache
CACHE_EXPIRY_HOURS = 4

# Layer 1: Universe filters
MIN_MCAP_CR = 500
MIN_PRICE = 50
MIN_AVG_VOLUME = 50000

# Layer 2: Momentum thresholds
MIN_6M_RETURN = 20          # percent
MIN_RSI_MOMENTUM = 55
MIN_RSI_BREAKOUT = 50
MAX_DIST_FROM_52W_HIGH = 25  # percent (within 25% of high)
MIN_DIST_FROM_52W_LOW = 30   # percent (at least 30% above low)

# Layer 3: Fundamental thresholds
MIN_ROE = 12
MIN_ROCE = 12
MAX_DE_RATIO = 1.5
MIN_PROMOTER_HOLDING = 40
MIN_REVENUE_GROWTH = 10
MIN_PROFIT_GROWTH = 15

console = Console()

# ═══════════════════════════════════════════════════════════════════
# CHARTINK SCANNER (PRIMARY - FAST)
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

SCAN_DESCRIPTIONS = {
    "momentum": "Stocks in strong uptrend with bullish EMA stack, near 52W high, RS > 60",
    "breakout": "Stocks hitting 65-day highs on volume surge (>1.5x avg) with EMA alignment",
    "pullback": "Momentum stocks pulling back to 50-DMA support & bouncing on volume",
}


class ChartinkScanner:
    """Fetches scan results from Chartink's screener API."""

    BASE_URL = "https://chartink.com/screener/process"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://chartink.com/screener",
        })
        self._csrf_token = None

    def _get_csrf_token(self):
        try:
            r = self.session.get("https://chartink.com/screener", timeout=15)
            r.raise_for_status()
            # Try both meta tag orderings
            match = re.search(r'name="csrf-token"\s+content="([^"]+)"', r.text)
            if not match:
                match = re.search(r'content="([^"]+)"\s+name="csrf-token"', r.text)
            if match:
                self._csrf_token = match.group(1)
                return self._csrf_token
        except Exception as e:
            console.print(f"[dim]Chartink CSRF fetch failed: {e}[/dim]")
        return None

    def scan(self, scan_clause: str) -> list[dict]:
        """Run a scan on Chartink. Returns list of stock dicts."""
        if not self._csrf_token:
            token = self._get_csrf_token()
            if not token:
                raise ConnectionError("Could not get Chartink CSRF token")

        try:
            r = self.session.post(
                self.BASE_URL,
                data={"scan_clause": scan_clause},
                headers={
                    "X-Csrf-Token": self._csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=30,
            )
            r.raise_for_status()
            result = r.json()
            return result.get("data", [])
        except Exception as e:
            raise ConnectionError(f"Chartink scan failed: {e}")


def run_chartink_scan(scan_type: str) -> list[dict]:
    """Run a single scan via Chartink and return parsed results."""
    scanner = ChartinkScanner()
    raw = scanner.scan(SCAN_CLAUSES[scan_type])
    results = []
    for item in raw:
        stock = {
            "symbol": item.get("nsecode", item.get("stock_name", "")),
            "name": item.get("stock_name", item.get("nsecode", "")),
            "close": float(item.get("close", 0)),
            "per_chg": float(item.get("per_chg", 0)),
            "volume": int(item.get("volume", 0)),
            "sr": item.get("sr", ""),
        }
        # Normalize symbol (strip .NS if present)
        stock["symbol"] = stock["symbol"].replace(".NS", "").replace(".BO", "").strip()
        if stock["symbol"]:
            results.append(stock)
    return results


# ═══════════════════════════════════════════════════════════════════
# LOCAL SCANNER (FALLBACK - yfinance based)
# ═══════════════════════════════════════════════════════════════════

def fetch_nifty500_list() -> list[str]:
    """Fetch Nifty 500 stock symbols. Multiple fallback methods."""
    cache_file = CACHE_DIR / "nifty500.json"

    # Check cache first (refresh daily)
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < 86400:  # 24 hours
            return json.loads(cache_file.read_text())

    symbols = []

    # Method 1: NSE archives CSV
    try:
        console.print("[dim]Fetching Nifty 500 list from NSE archives...[/dim]")
        df = pd.read_csv(
            "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
            timeout=15,
        )
        symbols = df["Symbol"].dropna().tolist()
        if len(symbols) > 400:
            cache_file.write_text(json.dumps(symbols))
            return symbols
    except Exception:
        pass

    # Method 2: NSE API with session
    try:
        console.print("[dim]Fetching from NSE API...[/dim]")
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        })
        session.get("https://www.nseindia.com", timeout=10)
        r = session.get(
            "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500",
            timeout=15,
        )
        data = r.json()
        symbols = [
            item["symbol"]
            for item in data.get("data", [])
            if item.get("symbol") and item["symbol"] != "NIFTY 500"
        ]
        if len(symbols) > 400:
            cache_file.write_text(json.dumps(symbols))
            return symbols
    except Exception:
        pass

    # Method 3: Use Nifty 200 + Nifty Midcap 150 from yfinance (smaller universe)
    try:
        console.print("[dim]Falling back to broader index constituents...[/dim]")
        # Fetch tickers from multiple Nifty indices
        indices_to_try = [
            "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
            "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
            "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
        ]
        all_symbols = set()
        for url in indices_to_try:
            try:
                df = pd.read_csv(url, timeout=10)
                all_symbols.update(df["Symbol"].dropna().tolist())
            except Exception:
                continue
        if all_symbols:
            symbols = sorted(all_symbols)
            cache_file.write_text(json.dumps(symbols))
            return symbols
    except Exception:
        pass

    # Method 4: cached file from previous successful fetch
    if cache_file.exists():
        console.print("[yellow]Using cached stock list (may be stale)[/yellow]")
        return json.loads(cache_file.read_text())

    console.print("[bold red]ERROR: Could not fetch stock list from any source.[/bold red]")
    console.print("Please check your internet connection and try again.")
    sys.exit(1)


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute Wilder RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def download_price_data(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download 1 year of OHLCV data for all symbols via yfinance."""
    cache_file = CACHE_DIR / "price_data.pkl"

    # Check cache
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < CACHE_EXPIRY_HOURS * 3600:
            console.print(
                f"[dim]Using cached price data "
                f"({int(age / 60)} min old, expires in {CACHE_EXPIRY_HOURS}h)[/dim]"
            )
            try:
                return pd.read_pickle(cache_file)
            except Exception:
                pass

    tickers_ns = [f"{s}.NS" for s in symbols]
    console.print(
        f"[bold cyan]Downloading price data for {len(tickers_ns)} stocks "
        f"(this takes 2-4 minutes on first run)...[/bold cyan]"
    )

    try:
        raw = yf.download(
            tickers_ns,
            period="1y",
            group_by="ticker",
            threads=True,
            progress=True,
        )
    except Exception as e:
        console.print(f"[bold red]yfinance download failed: {e}[/bold red]")
        return {}

    # Parse into per-ticker DataFrames
    stock_data = {}
    for sym_ns in tickers_ns:
        sym = sym_ns.replace(".NS", "")
        try:
            if len(tickers_ns) == 1:
                df = raw.copy()
            else:
                df = raw[sym_ns].copy()
            # Drop rows where Close is NaN
            df = df.dropna(subset=["Close"])
            if len(df) >= 100:  # need at least ~100 trading days
                stock_data[sym] = df
        except (KeyError, TypeError):
            continue

    # Cache
    try:
        pd.to_pickle(stock_data, cache_file)
    except Exception:
        pass

    console.print(f"[green]Got data for {len(stock_data)} stocks[/green]")
    return stock_data


def compute_stock_metrics(df: pd.DataFrame, nifty_df: pd.DataFrame = None) -> dict:
    """Compute all technical indicators for a single stock's DataFrame."""
    if df is None or len(df) < 100:
        return None

    close = df["Close"].squeeze() if isinstance(df["Close"], pd.DataFrame) else df["Close"]
    volume = df["Volume"].squeeze() if isinstance(df["Volume"], pd.DataFrame) else df["Volume"]

    # Handle MultiIndex columns from yfinance
    if hasattr(close, 'columns'):
        close = close.iloc[:, 0]
    if hasattr(volume, 'columns'):
        volume = volume.iloc[:, 0]

    current_price = float(close.iloc[-1])
    if current_price <= 0:
        return None

    # EMAs
    ema_50 = close.ewm(span=50, adjust=False).mean()
    ema_150 = close.ewm(span=150, adjust=False).mean()
    ema_200 = close.ewm(span=200, adjust=False).mean()

    ema_50_val = float(ema_50.iloc[-1])
    ema_150_val = float(ema_150.iloc[-1])
    ema_200_val = float(ema_200.iloc[-1])

    # EMA stack check: price > 50 > 150 > 200
    ema_stacked = (
        current_price > ema_50_val
        and ema_50_val > ema_150_val
        and ema_150_val > ema_200_val
    )

    # 200-DMA slope (is it rising over last 20 days?)
    if len(ema_200) >= 20:
        ema_200_slope = float(ema_200.iloc[-1] - ema_200.iloc[-20])
    else:
        ema_200_slope = 0

    # RSI
    rsi_series = compute_rsi(close)
    current_rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50

    # 52-week high/low
    high_52w = float(close.max())
    low_52w = float(close.min())
    dist_from_high = ((high_52w - current_price) / high_52w) * 100 if high_52w > 0 else 100
    dist_from_low = ((current_price - low_52w) / low_52w) * 100 if low_52w > 0 else 0

    # 6-month return
    if len(close) >= 126:
        price_6m_ago = float(close.iloc[-126])
        ret_6m = ((current_price - price_6m_ago) / price_6m_ago) * 100 if price_6m_ago > 0 else 0
    else:
        ret_6m = 0

    # 3-month return
    if len(close) >= 63:
        price_3m_ago = float(close.iloc[-63])
        ret_3m = ((current_price - price_3m_ago) / price_3m_ago) * 100 if price_3m_ago > 0 else 0
    else:
        ret_3m = 0

    # 1-month return
    if len(close) >= 21:
        price_1m_ago = float(close.iloc[-21])
        ret_1m = ((current_price - price_1m_ago) / price_1m_ago) * 100 if price_1m_ago > 0 else 0
    else:
        ret_1m = 0

    # Relative strength vs Nifty (6-month)
    rs_vs_nifty = 0
    if nifty_df is not None and len(nifty_df) >= 126:
        nifty_close = nifty_df["Close"].squeeze() if isinstance(nifty_df["Close"], pd.DataFrame) else nifty_df["Close"]
        if hasattr(nifty_close, 'columns'):
            nifty_close = nifty_close.iloc[:, 0]
        nifty_6m_ret = (
            (float(nifty_close.iloc[-1]) - float(nifty_close.iloc[-126]))
            / float(nifty_close.iloc[-126])
            * 100
        )
        rs_vs_nifty = ret_6m - nifty_6m_ret

    # Volume metrics
    vol_sma_20 = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else 0
    vol_sma_50 = float(volume.rolling(50).mean().iloc[-1]) if len(volume) >= 50 else 0
    current_vol = float(volume.iloc[-1])
    vol_vs_avg = ((current_vol / vol_sma_20) - 1) * 100 if vol_sma_20 > 0 else 0
    avg_volume = vol_sma_20

    # Is making new 65-day high?
    if len(close) >= 65:
        high_65d = float(close.iloc[-65:].max())
        is_65d_high = current_price >= high_65d * 0.99  # within 1%
    else:
        is_65d_high = False

    # Distance from 50-DMA (for pullback scan)
    dist_from_50dma = ((current_price - ema_50_val) / ema_50_val) * 100 if ema_50_val > 0 else 0

    # Day change
    if len(close) >= 2:
        prev_close = float(close.iloc[-2])
        day_chg = ((current_price - prev_close) / prev_close) * 100 if prev_close > 0 else 0
    else:
        day_chg = 0

    return {
        "price": current_price,
        "ema_50": ema_50_val,
        "ema_150": ema_150_val,
        "ema_200": ema_200_val,
        "ema_stacked": ema_stacked,
        "ema_200_slope": ema_200_slope,
        "rsi": current_rsi,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "dist_from_high": dist_from_high,
        "dist_from_low": dist_from_low,
        "ret_6m": ret_6m,
        "ret_3m": ret_3m,
        "ret_1m": ret_1m,
        "rs_vs_nifty": rs_vs_nifty,
        "current_vol": current_vol,
        "avg_volume": avg_volume,
        "vol_vs_avg": vol_vs_avg,
        "vol_20_vs_50": (vol_sma_20 / vol_sma_50 - 1) * 100 if vol_sma_50 > 0 else 0,
        "is_65d_high": is_65d_high,
        "dist_from_50dma": dist_from_50dma,
        "day_chg": day_chg,
    }


def local_scan_momentum(stock_data: dict, nifty_df: pd.DataFrame) -> list[dict]:
    """Layer 2: Momentum universe scan using local data."""
    results = []
    for sym, df in stock_data.items():
        m = compute_stock_metrics(df, nifty_df)
        if m is None:
            continue
        # Momentum filters
        if (
            m["ema_stacked"]
            and m["ema_200_slope"] > 0
            and m["dist_from_high"] <= MAX_DIST_FROM_52W_HIGH
            and m["dist_from_low"] >= MIN_DIST_FROM_52W_LOW
            and m["ret_6m"] >= MIN_6M_RETURN
            and m["rsi"] >= MIN_RSI_MOMENTUM
            and m["avg_volume"] >= MIN_AVG_VOLUME
            and m["price"] >= MIN_PRICE
        ):
            results.append({"symbol": sym, **m})
    # Sort by relative strength (6M return)
    results.sort(key=lambda x: x["ret_6m"], reverse=True)
    return results


def local_scan_breakout(stock_data: dict, nifty_df: pd.DataFrame) -> list[dict]:
    """Layer 5a: Breakout entry scan - stocks hitting multi-week highs on volume."""
    results = []
    for sym, df in stock_data.items():
        m = compute_stock_metrics(df, nifty_df)
        if m is None:
            continue
        # Breakout filters
        if (
            m["price"] > m["ema_50"]
            and m["price"] > m["ema_150"]
            and m["price"] > m["ema_200"]
            and m["ema_50"] > m["ema_150"]
            and m["is_65d_high"]
            and m["vol_vs_avg"] >= 50  # volume > 1.5x average
            and m["avg_volume"] >= MIN_AVG_VOLUME
            and m["price"] >= MIN_PRICE
        ):
            results.append({"symbol": sym, **m})
    results.sort(key=lambda x: x["vol_vs_avg"], reverse=True)
    return results


def local_scan_pullback(stock_data: dict, nifty_df: pd.DataFrame) -> list[dict]:
    """Layer 5b: Pullback to 50-DMA support scan."""
    results = []
    for sym, df in stock_data.items():
        m = compute_stock_metrics(df, nifty_df)
        if m is None:
            continue
        # Pullback filters: near 50-DMA, bouncing, in uptrend
        if (
            m["ema_50"] > m["ema_150"]
            and m["ema_150"] > m["ema_200"]
            and -3 <= m["dist_from_50dma"] <= 3  # within 3% of 50-DMA
            and m["day_chg"] > 0  # bouncing (today > yesterday)
            and m["vol_vs_avg"] > 0  # volume above average
            and m["rsi"] >= MIN_RSI_MOMENTUM
            and m["avg_volume"] >= MIN_AVG_VOLUME
            and m["price"] >= MIN_PRICE
        ):
            results.append({"symbol": sym, **m})
    results.sort(key=lambda x: x["rs_vs_nifty"], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════
# FUNDAMENTAL CHECKER (Layer 3)
# ═══════════════════════════════════════════════════════════════════

def check_fundamentals(symbols: list[str]) -> list[dict]:
    """Fetch fundamental data from yfinance for shortlisted stocks."""
    results = []
    total = len(symbols)
    console.print(f"\n[bold cyan]Checking fundamentals for {total} stocks...[/bold cyan]")

    for i, sym in enumerate(symbols, 1):
        console.print(f"  [{i}/{total}] {sym}...", end=" ")
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            info = ticker.info or {}

            roe = info.get("returnOnEquity")
            roe = round(roe * 100, 1) if roe else None

            de_ratio = info.get("debtToEquity")
            de_ratio = round(de_ratio / 100, 2) if de_ratio else None  # yfinance gives as %

            revenue_growth = info.get("revenueGrowth")
            revenue_growth = round(revenue_growth * 100, 1) if revenue_growth else None

            earnings_growth = info.get("earningsGrowth")
            earnings_growth = round(earnings_growth * 100, 1) if earnings_growth else None

            market_cap = info.get("marketCap", 0)
            mcap_cr = round(market_cap / 1e7, 0) if market_cap else 0  # Convert to crores

            pe_ratio = info.get("trailingPE")
            pe_ratio = round(pe_ratio, 1) if pe_ratio else None

            peg = info.get("pegRatio")
            peg = round(peg, 2) if peg else None

            sector = info.get("sector", "N/A")
            industry = info.get("industry", "N/A")
            name = info.get("shortName", info.get("longName", sym))

            # Simple pass/fail
            passes_fundamental = True
            fail_reasons = []

            if roe is not None and roe < MIN_ROE:
                passes_fundamental = False
                fail_reasons.append(f"ROE={roe}%")
            if de_ratio is not None and de_ratio > MAX_DE_RATIO:
                passes_fundamental = False
                fail_reasons.append(f"D/E={de_ratio}")
            if revenue_growth is not None and revenue_growth < MIN_REVENUE_GROWTH:
                passes_fundamental = False
                fail_reasons.append(f"RevGr={revenue_growth}%")

            status = "[green]PASS[/green]" if passes_fundamental else "[red]FAIL[/red]"
            console.print(status)

            results.append({
                "symbol": sym,
                "name": name[:30],
                "mcap_cr": mcap_cr,
                "sector": sector,
                "industry": industry,
                "roe": roe,
                "de_ratio": de_ratio,
                "revenue_growth": revenue_growth,
                "earnings_growth": earnings_growth,
                "pe": pe_ratio,
                "peg": peg,
                "passes": passes_fundamental,
                "fail_reasons": ", ".join(fail_reasons),
            })
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            results.append({
                "symbol": sym,
                "name": sym,
                "mcap_cr": 0,
                "sector": "N/A",
                "industry": "N/A",
                "roe": None,
                "de_ratio": None,
                "revenue_growth": None,
                "earnings_growth": None,
                "pe": None,
                "peg": None,
                "passes": None,
                "fail_reasons": "Data unavailable",
            })
        time.sleep(0.3)  # Rate limit

    return results


# ═══════════════════════════════════════════════════════════════════
# MARKET REGIME CHECK
# ═══════════════════════════════════════════════════════════════════

def check_market_regime() -> dict:
    """Check overall market health: Nifty vs 200-DMA, VIX, etc."""
    console.print("\n[bold]Checking market regime...[/bold]")

    regime = {
        "nifty_price": 0,
        "nifty_200dma": 0,
        "nifty_50dma": 0,
        "nifty_above_200": False,
        "nifty_above_50": False,
        "ema_50_above_200": False,
        "vix": 0,
        "regime": "UNKNOWN",
        "action": "",
        "nifty_df": None,
    }

    try:
        nifty = yf.download("^NSEI", period="1y", progress=False)
        if nifty.empty:
            console.print("[yellow]Could not fetch Nifty data[/yellow]")
            return regime

        nifty_close = nifty["Close"].squeeze() if isinstance(nifty["Close"], pd.DataFrame) else nifty["Close"]
        if hasattr(nifty_close, 'columns'):
            nifty_close = nifty_close.iloc[:, 0]

        price = float(nifty_close.iloc[-1])
        ema_50 = float(nifty_close.ewm(span=50, adjust=False).mean().iloc[-1])
        ema_200 = float(nifty_close.ewm(span=200, adjust=False).mean().iloc[-1])

        regime["nifty_price"] = price
        regime["nifty_200dma"] = ema_200
        regime["nifty_50dma"] = ema_50
        regime["nifty_above_200"] = price > ema_200
        regime["nifty_above_50"] = price > ema_50
        regime["ema_50_above_200"] = ema_50 > ema_200
        regime["nifty_df"] = nifty
    except Exception as e:
        console.print(f"[yellow]Nifty data error: {e}[/yellow]")

    try:
        vix = yf.download("^INDIAVIX", period="5d", progress=False)
        if not vix.empty:
            vix_close = vix["Close"].squeeze() if isinstance(vix["Close"], pd.DataFrame) else vix["Close"]
            if hasattr(vix_close, 'columns'):
                vix_close = vix_close.iloc[:, 0]
            regime["vix"] = float(vix_close.iloc[-1])
    except Exception:
        pass

    # Determine regime
    if regime["nifty_above_200"] and regime["nifty_above_50"] and regime["ema_50_above_200"]:
        regime["regime"] = "STRONG BULL"
        regime["action"] = "Full exposure (80-100%). Run all scans aggressively."
    elif regime["nifty_above_200"] and not regime["nifty_above_50"]:
        regime["regime"] = "MILD BULL / PULLBACK"
        regime["action"] = "Moderate exposure (50-70%). Be selective, focus on pullback scan."
    elif not regime["nifty_above_200"] and regime["nifty_above_50"]:
        regime["regime"] = "RECOVERY / UNCERTAIN"
        regime["action"] = "Cautious (30-50%). Only highest-conviction ideas."
    else:
        regime["regime"] = "BEAR"
        regime["action"] = "Minimal exposure (0-20%). Mostly cash. Avoid new longs."

    return regime


def display_regime(regime: dict):
    """Display market regime panel."""
    vix = regime["vix"]
    if vix < 15:
        vix_label = f"{vix:.1f} (Low - Calm)"
        vix_color = "green"
    elif vix < 20:
        vix_label = f"{vix:.1f} (Normal)"
        vix_color = "yellow"
    elif vix < 25:
        vix_label = f"{vix:.1f} (Elevated - Caution)"
        vix_color = "red"
    else:
        vix_label = f"{vix:.1f} (High - Reduce exposure!)"
        vix_color = "bold red"

    regime_color = {
        "STRONG BULL": "bold green",
        "MILD BULL / PULLBACK": "yellow",
        "RECOVERY / UNCERTAIN": "red",
        "BEAR": "bold red",
    }.get(regime["regime"], "white")

    above_200 = "[green]YES[/green]" if regime["nifty_above_200"] else "[red]NO[/red]"
    above_50 = "[green]YES[/green]" if regime["nifty_above_50"] else "[red]NO[/red]"
    ema_stack = "[green]YES[/green]" if regime["ema_50_above_200"] else "[red]NO[/red]"

    text = (
        f"  Nifty 50:  [bold]{regime['nifty_price']:,.0f}[/bold]\n"
        f"  200-DMA:   {regime['nifty_200dma']:,.0f}  |  Above: {above_200}\n"
        f"  50-DMA:    {regime['nifty_50dma']:,.0f}   |  Above: {above_50}\n"
        f"  50 > 200:  {ema_stack}\n"
        f"  India VIX: [{vix_color}]{vix_label}[/{vix_color}]\n"
        f"\n"
        f"  Regime:    [{regime_color}]{regime['regime']}[/{regime_color}]\n"
        f"  Action:    {regime['action']}"
    )

    console.print(Panel(text, title="[bold white]MARKET REGIME[/bold white]", border_style="cyan"))


# ═══════════════════════════════════════════════════════════════════
# DISPLAY & OUTPUT
# ═══════════════════════════════════════════════════════════════════

def display_scan_results(
    scan_name: str,
    description: str,
    results: list[dict],
    source: str = "chartink",
):
    """Display scan results as a rich table."""
    count = len(results)
    color = "green" if count > 0 else "yellow"

    console.print(
        f"\n[bold white on blue]  SCAN: {scan_name.upper()}  [/bold white on blue]"
        f"  [{color}]{count} stocks found[/{color}]"
    )
    console.print(f"  [dim]{description}[/dim]\n")

    if count == 0:
        console.print("  [dim]No stocks matched this scan today.[/dim]\n")
        return

    table = Table(box=box.ROUNDED, show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Symbol", style="bold cyan", width=16)

    if source == "chartink":
        table.add_column("Close", justify="right", width=10)
        table.add_column("Chg%", justify="right", width=8)
        table.add_column("Volume", justify="right", width=12)
    else:
        table.add_column("Price", justify="right", width=10)
        table.add_column("Mcap(Cr)", justify="right", width=10)
        table.add_column("6M Ret%", justify="right", width=9)
        table.add_column("RS Nifty", justify="right", width=9)
        table.add_column("RSI", justify="right", width=6)
        table.add_column("Vol%", justify="right", width=8)
        table.add_column("52W H%", justify="right", width=8)

    for i, stock in enumerate(results[:50], 1):  # cap at 50
        if source == "chartink":
            chg = stock.get("per_chg", 0)
            chg_style = "green" if chg >= 0 else "red"
            table.add_row(
                str(i),
                stock["symbol"],
                f"{stock['close']:,.2f}",
                f"[{chg_style}]{chg:+.2f}%[/{chg_style}]",
                f"{stock['volume']:,}",
            )
        else:
            ret_6m = stock.get("ret_6m", 0)
            rs = stock.get("rs_vs_nifty", 0)
            rsi = stock.get("rsi", 0)
            vol_pct = stock.get("vol_vs_avg", 0)
            dist_h = stock.get("dist_from_high", 0)

            ret_style = "green" if ret_6m >= 0 else "red"
            rs_style = "green" if rs >= 0 else "red"
            rsi_style = "green" if rsi >= 60 else ("yellow" if rsi >= 50 else "red")
            vol_style = "green" if vol_pct > 0 else "red"

            mcap_cr = stock.get("mcap_cr", 0)
            if mcap_cr == 0:
                # Estimate from price if available
                mcap_cr = "-"
            else:
                mcap_cr = f"{mcap_cr:,.0f}"

            table.add_row(
                str(i),
                stock["symbol"],
                f"{stock['price']:,.2f}",
                mcap_cr if isinstance(mcap_cr, str) else f"{mcap_cr}",
                f"[{ret_style}]{ret_6m:+.1f}%[/{ret_style}]",
                f"[{rs_style}]{rs:+.1f}[/{rs_style}]",
                f"[{rsi_style}]{rsi:.0f}[/{rsi_style}]",
                f"[{vol_style}]{vol_pct:+.0f}%[/{vol_style}]",
                f"{dist_h:.1f}%",
            )

    console.print(table)

    if count > 50:
        console.print(f"  [dim]... and {count - 50} more (see CSV export for full list)[/dim]")


def display_fundamentals(results: list[dict]):
    """Display fundamental check results."""
    if not results:
        return

    passed = [r for r in results if r["passes"] is True]
    failed = [r for r in results if r["passes"] is False]
    unknown = [r for r in results if r["passes"] is None]

    console.print(
        f"\n[bold white on magenta]  FUNDAMENTAL VERIFICATION  [/bold white on magenta]"
        f"  [green]{len(passed)} PASS[/green] | [red]{len(failed)} FAIL[/red]"
        f" | [yellow]{len(unknown)} N/A[/yellow]"
    )

    table = Table(box=box.ROUNDED, show_lines=False, padding=(0, 1))
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Symbol", style="bold", width=12)
    table.add_column("Name", width=22)
    table.add_column("Sector", width=18)
    table.add_column("Mcap(Cr)", justify="right", width=10)
    table.add_column("ROE%", justify="right", width=7)
    table.add_column("D/E", justify="right", width=6)
    table.add_column("Rev Gr%", justify="right", width=8)
    table.add_column("Earn Gr%", justify="right", width=9)
    table.add_column("PE", justify="right", width=7)
    table.add_column("PEG", justify="right", width=6)
    table.add_column("Verdict", width=8)

    # Show passed first, then failed
    for i, r in enumerate(passed + failed + unknown, 1):
        roe_str = f"{r['roe']:.1f}" if r["roe"] is not None else "N/A"
        de_str = f"{r['de_ratio']:.2f}" if r["de_ratio"] is not None else "N/A"
        rev_str = f"{r['revenue_growth']:.1f}" if r["revenue_growth"] is not None else "N/A"
        earn_str = f"{r['earnings_growth']:.1f}" if r["earnings_growth"] is not None else "N/A"
        pe_str = f"{r['pe']:.1f}" if r["pe"] is not None else "N/A"
        peg_str = f"{r['peg']:.2f}" if r["peg"] is not None else "N/A"

        if r["passes"] is True:
            verdict = "[bold green]PASS[/bold green]"
            sym_style = "bold green"
        elif r["passes"] is False:
            verdict = "[bold red]FAIL[/bold red]"
            sym_style = "red"
        else:
            verdict = "[yellow]N/A[/yellow]"
            sym_style = "yellow"

        table.add_row(
            str(i),
            f"[{sym_style}]{r['symbol']}[/{sym_style}]",
            r["name"][:22],
            r["sector"][:18] if r["sector"] else "N/A",
            f"{r['mcap_cr']:,.0f}" if r["mcap_cr"] else "N/A",
            roe_str,
            de_str,
            rev_str,
            earn_str,
            pe_str,
            peg_str,
            verdict,
        )

    console.print(table)

    if failed:
        console.print(f"\n  [dim]Failed stocks detail:[/dim]")
        for r in failed:
            console.print(f"    [red]{r['symbol']}[/red]: {r['fail_reasons']}")


def export_results(all_results: dict, fundamentals: list[dict] = None):
    """Export all scan results to CSV."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    for scan_name, results in all_results.items():
        if not results:
            continue
        filepath = RESULTS_DIR / f"scan_{scan_name}_{timestamp}.csv"
        if results:
            keys = results[0].keys()
            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(results)
            console.print(f"  [dim]Saved: {filepath}[/dim]")

    if fundamentals:
        filepath = RESULTS_DIR / f"fundamentals_{timestamp}.csv"
        keys = fundamentals[0].keys()
        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(fundamentals)
        console.print(f"  [dim]Saved: {filepath}[/dim]")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Momentum Stock Scanner - Indian Markets (NSE)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run scanner.py                  Run all scans via Chartink\n"
            "  uv run scanner.py momentum          Momentum universe only\n"
            "  uv run scanner.py breakout -f        Breakout scan + fundamentals\n"
            "  uv run scanner.py all --local        Full local scan (yfinance)\n"
            "  uv run scanner.py all -f --export    Everything + save CSV\n"
        ),
    )
    parser.add_argument(
        "scan_type",
        nargs="?",
        default="all",
        choices=["momentum", "breakout", "pullback", "all"],
        help="Which scan to run (default: all)",
    )
    parser.add_argument(
        "-f", "--fundamentals",
        action="store_true",
        help="Run fundamental checks on shortlisted stocks",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export results to CSV in ~/momentum_scanner/results/",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Force local yfinance-based scan (slower but fully offline)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh cached data",
    )
    args = parser.parse_args()

    # ── Banner ──
    banner = (
        "[bold white]"
        "  MOMENTUM STOCK SCANNER\n"
        "  Indian Markets (NSE) | Momentum + Quality Strategy\n"
        "[/bold white]"
        "[dim]"
        "  Based on: Minervini SEPA | Weinstein Stages | O'Neil CANSLIM\n"
        "  Antonacci Dual Momentum | Jegadeesh-Titman Factor\n"
        f"  Scan Date: {datetime.now().strftime('%Y-%m-%d %H:%M')} IST"
        "[/dim]"
    )
    console.print(Panel(banner, border_style="bold blue"))

    # ── Clear cache if requested ──
    if args.refresh:
        for f in CACHE_DIR.glob("*"):
            f.unlink()
        console.print("[dim]Cache cleared.[/dim]")

    # ── Market Regime ──
    regime = check_market_regime()
    display_regime(regime)

    if regime["regime"] == "BEAR":
        console.print(
            "\n[bold red]WARNING: Market is in BEAR regime. "
            "New long positions are NOT recommended.[/bold red]"
        )
        console.print("[yellow]Scans will still run for informational purposes.\n[/yellow]")

    # ── Determine which scans to run ──
    scans_to_run = (
        ["momentum", "breakout", "pullback"]
        if args.scan_type == "all"
        else [args.scan_type]
    )

    all_results = {}
    all_symbols_found = set()
    use_local = args.local

    # ── Try Chartink first ──
    if not use_local:
        console.print("\n[bold]Running scans via Chartink (fast mode)...[/bold]")
        chartink_failed = False

        for scan_name in scans_to_run:
            try:
                results = run_chartink_scan(scan_name)
                all_results[scan_name] = results
                for r in results:
                    all_symbols_found.add(r["symbol"])
                display_scan_results(
                    scan_name,
                    SCAN_DESCRIPTIONS[scan_name],
                    results,
                    source="chartink",
                )
            except ConnectionError as e:
                console.print(
                    f"[yellow]Chartink failed for '{scan_name}': {e}[/yellow]"
                )
                chartink_failed = True
                break
            except Exception as e:
                console.print(
                    f"[yellow]Chartink error for '{scan_name}': {e}[/yellow]"
                )
                chartink_failed = True
                break

        if chartink_failed:
            console.print(
                "\n[yellow]Chartink unavailable. Falling back to local scan...[/yellow]"
            )
            use_local = True
            all_results = {}
            all_symbols_found = set()

    # ── Local scan (yfinance) ──
    if use_local:
        console.print("\n[bold]Running local scans via yfinance...[/bold]")

        # Get stock list
        symbols = fetch_nifty500_list()
        console.print(f"[dim]Universe: {len(symbols)} stocks[/dim]")

        # Download price data
        stock_data = download_price_data(symbols)
        if not stock_data:
            console.print("[bold red]No price data available. Exiting.[/bold red]")
            sys.exit(1)

        nifty_df = regime.get("nifty_df")

        scan_functions = {
            "momentum": local_scan_momentum,
            "breakout": local_scan_breakout,
            "pullback": local_scan_pullback,
        }

        for scan_name in scans_to_run:
            scan_fn = scan_functions[scan_name]
            results = scan_fn(stock_data, nifty_df)
            all_results[scan_name] = results
            for r in results:
                all_symbols_found.add(r["symbol"])
            display_scan_results(
                scan_name,
                SCAN_DESCRIPTIONS[scan_name],
                results,
                source="local",
            )

    # ── Fundamental verification ──
    fundamentals = None
    if args.fundamentals and all_symbols_found:
        symbols_list = sorted(all_symbols_found)[:30]  # Cap at 30 for speed
        if len(all_symbols_found) > 30:
            console.print(
                f"[dim]Checking fundamentals for top 30 of "
                f"{len(all_symbols_found)} stocks[/dim]"
            )
        fundamentals = check_fundamentals(symbols_list)
        display_fundamentals(fundamentals)

    # ── Export ──
    if args.export:
        console.print(f"\n[bold]Exporting results...[/bold]")
        export_results(all_results, fundamentals)

    # ── Summary ──
    console.print("\n" + "=" * 60)
    total_found = sum(len(v) for v in all_results.values())
    console.print(
        f"[bold]Total unique stocks across all scans: "
        f"{len(all_symbols_found)}[/bold]"
    )

    if not args.fundamentals and all_symbols_found:
        console.print(
            "\n[dim]Tip: Run with -f flag to check fundamentals for these stocks[/dim]"
        )
    if not args.export and all_symbols_found:
        console.print(
            "[dim]Tip: Run with --export flag to save results as CSV[/dim]"
        )

    # ── Next steps ──
    console.print(
        Panel(
            "[bold]Next Steps:[/bold]\n"
            "  1. Review the shortlisted stocks on TradingView/Dhan charts\n"
            "  2. Check institutional holdings on Tijori Finance\n"
            "  3. Verify quarterly results on Screener.in\n"
            "  4. Wait for entry trigger (breakout/pullback) before buying\n"
            "  5. Set stop loss 8-10% below entry BEFORE you trade",
            title="[bold white]ACTION ITEMS[/bold white]",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
