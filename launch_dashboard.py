#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "streamlit>=1.30",
#     "plotly>=5.18",
#     "yfinance>=0.2.31",
#     "pandas>=2.0",
#     "numpy>=1.24",
#     "requests>=2.31",
#     "beautifulsoup4>=4.12",
#     "lxml>=5.0",
# ]
# ///
"""Launcher for the Momentum Scanner Dashboard.

Usage: uv run ~/momentum_scanner/launch_dashboard.py
"""
import os
import subprocess
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.exit(subprocess.call([
    sys.executable, "-m", "streamlit", "run", "dashboard.py",
    "--server.headless=true",
    "--browser.gatherUsageStats=false",
    "--theme.base=dark",
] + sys.argv[1:]))
