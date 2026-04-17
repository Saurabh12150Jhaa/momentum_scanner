# Momentum Stock Scanner - Indian Markets (NSE)

**[Launch Live App](https://momentumscanner-9fstalaqwzehtix3tba7cg.streamlit.app/)**

A comprehensive momentum stock screening and analysis toolkit for Indian markets, combining CLI scanning with an interactive Streamlit dashboard.

Built on proven momentum strategies: **Minervini SEPA**, **Weinstein Stage Analysis**, **O'Neil CANSLIM**, **Antonacci Dual Momentum**, and **Jegadeesh-Titman Momentum Factor**.

---

## Features

### CLI Scanner (`scanner.py`)
- **3 scan types**: Momentum Universe, Breakout Candidates, Pullback-to-EMA entries
- **Market regime detection**: Nifty 50 vs 200-DMA, India VIX analysis (BULL/BEAR)
- **Fundamental verification**: ROE, D/E, revenue/earnings growth checks
- **Dual data sources**: Chartink API (primary) with yfinance fallback
- **Rich terminal output**: Color-coded tables, panels, actionable tips
- **CSV export**: Auto-saves scan results for record keeping

### Web Dashboard (`dashboard.py`)
- **Interactive candlestick charts** with EMA 50/150/200 overlays, RSI, Volume
- **Weinstein Stage detection** (Stage 1-4) with buy/sell recommendations
- **Entry signal detection**: Breakout, Gap-Up Hold, Pullback, Volume Surge, EMA Crossover
- **Stop loss calculator**: 5 methods (Fixed 8%/10%, ATR-based, Below 50-DMA/200-DMA)
- **Position sizing calculator**: Risk-based sizing with R:R targets
- **Shareholding pattern**: Full SEBI-mandated breakdown (Promoters/FIIs/DIIs/Government/Public) with quarterly trends from Screener.in
- **Fundamental metrics**: PE, ROE, D/E, PEG, OPM, NPM, quarterly results
- **Watchlist & Portfolio tracker**: Persistent storage with P&L tracking

---

## Prerequisites

- **Python 3.9+**
- **[uv](https://docs.astral.sh/uv/)** — Fast Python package manager (handles all dependencies automatically)

Install `uv` if you don't have it:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

No other setup needed. All Python dependencies are managed via inline script metadata (PEP 723) and installed automatically on first run.

---

## Quick Start

### CLI Scanner
```bash
# Run from the project directory
cd momentum_scanner

# Quick momentum scan
uv run scanner.py momentum

# Breakout candidates
uv run scanner.py breakout

# Pullback-to-EMA entries
uv run scanner.py pullback

# Full scan: all 3 types + fundamentals + CSV export
uv run scanner.py all -f --export

# Force yfinance (skip Chartink API)
uv run scanner.py all --local
```

### Web Dashboard
```bash
cd momentum_scanner
uv run launch_dashboard.py
# Opens at http://localhost:8502
```

### Shell Aliases (Optional)

Add these to your `~/.zshrc` or `~/.bashrc` for quick access:
```bash
alias scan='~/momentum_scanner/scan'
alias scan-momentum='~/momentum_scanner/scan momentum'
alias scan-breakout='~/momentum_scanner/scan breakout'
alias scan-pullback='~/momentum_scanner/scan pullback'
alias scan-full='~/momentum_scanner/scan all -f --export'
alias dashboard='~/momentum_scanner/dashboard'
```

---

## How It Works

### 5-Layer Filtering Funnel

1. **Universe Filter** — Market cap > 500 Cr, Price > Rs.50, Avg volume > 50K
2. **Momentum Filter** — 6-month return > 20%, RSI > 55, within 25% of 52-week high, at least 30% above 52-week low
3. **Technical Filter** — Weinstein Stage 2 (advancing), EMA stack (50 > 150 > 200), rising 200-DMA
4. **Fundamental Filter** — ROE > 12%, D/E < 1.5, revenue growth > 10%, earnings growth > 15%
5. **Entry Trigger** — Breakout above resistance, pullback to 50-DMA, gap-up with hold, volume surge, EMA crossover

### Market Regime Detection

| Regime | Condition | Allocation |
|--------|-----------|------------|
| BULL   | Nifty > 200-DMA, VIX < 20 | 80-100% invested |
| CAUTION | Mixed signals | 40-60% invested |
| BEAR   | Nifty < 200-DMA or VIX > 25 | 0-20%, mostly cash |

### Shareholding Data

The dashboard scrapes **Screener.in** for the full SEBI-mandated quarterly shareholding pattern:
- Promoters %
- FIIs (Foreign Institutional Investors) %
- DIIs (Domestic Institutional Investors) %
- Government %
- Public %
- Number of Shareholders

Falls back to yfinance if Screener.in is unavailable.

---

## Project Structure

```
momentum_scanner/
├── scanner.py           # CLI scanner (1185 lines)
├── dashboard.py         # Streamlit web dashboard
├── launch_dashboard.py  # Dashboard launcher with uv dependency management
├── scan                 # Shell wrapper for CLI scanner
├── dashboard            # Shell wrapper for dashboard
├── .cache/              # Runtime cache (watchlist, price data)
├── results/             # CSV export directory
└── README.md
```

---

## Data Sources

| Source | Usage |
|--------|-------|
| [Chartink](https://chartink.com) | Primary stock screener API (momentum/breakout/pullback scans) |
| [yfinance](https://github.com/ranaroussi/yfinance) | Price data, fundamentals, fallback scanning |
| [Screener.in](https://www.screener.in) | Shareholding patterns, quarterly results |
| [NSE India](https://www.nseindia.com) | Nifty 50, India VIX data |

---

## Typical Workflow

```
1. Morning: Run full scan
   $ scan-full

2. Open dashboard for visual analysis
   $ dashboard

3. In the dashboard:
   - Check market regime (BULL/BEAR)
   - Browse scan results across 3 tabs
   - Click a stock for deep dive:
     → Charts + EMAs + RSI + Volume
     → Weinstein stage & entry signals
     → Fundamentals + quarterly results
     → Shareholding pattern (Promoter/FII/DII/Public)
     → Stop loss levels drawn on chart
     → Position sizing calculator
   - Add to watchlist or portfolio

4. Execute trades on your broker (Dhan/Zerodha/etc.)
   - Set stop loss BEFORE entering
   - Position size based on 2% risk rule
```

---

## Dependencies

All managed automatically via `uv`. No manual `pip install` needed.

**CLI Scanner**: yfinance, pandas, rich, requests, numpy

**Dashboard**: streamlit, plotly, yfinance, pandas, numpy, requests, beautifulsoup4, lxml

---

## Disclaimer

This tool is for **educational and research purposes only**. It is not financial advice. Always do your own research before making any investment decisions. Past performance does not guarantee future results. Trading in the stock market involves risk of loss.

---

## License

MIT
