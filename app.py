import streamlit as st
import yfinance as yf
from fredapi import Fred
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np

# Page Configuration
st.set_page_config(page_title="Gold Macro Engine", page_icon="🪙", layout="wide")

# Custom Styling for Mobile Cards
st.markdown("""
    <style>
    .metric-card {
        background-color: #1E1E1E;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #FFD700;
        margin-bottom: 10px;
    }
    .rule-box {
        background-color: #262730;
        padding: 12px;
        border-radius: 8px;
        margin-top: 8px;
        font-size: 14px;
    }
    </style>
""", unsafe_allow_html=True)

# ----------------- CONFIG & DATA FETCHING -----------------
FRED_API_KEY = "5f5abaf5f6b2887e0f54337f65b8169d"  # Put your 32-char FRED API Key here
fred = Fred(api_key=FRED_API_KEY)

@st.cache_data(ttl=1800)
def fetch_all_data():
    # FRED Macro Series
    real_yield = fred.get_series('DFII10').dropna()
    yield_2y = fred.get_series('DGS2').dropna()
    yield_10y = fred.get_series('DGS10').dropna()
    walcl = fred.get_series('WALCL').dropna()
    tga = fred.get_series('WTREGEN').dropna()
    rrp = fred.get_series('RRPONTSYD').dropna()
    
    # Yield Curve
    yc_df = pd.DataFrame({'2Y': yield_2y, '10Y': yield_10y}).dropna()
    yield_curve = yc_df['10Y'] - yc_df['2Y']
    
    # Net Liquidity = Fed Assets - TGA - RRP (in Trillions)
    liq_df = pd.DataFrame({'WALCL': walcl, 'TGA': tga, 'RRP': rrp}).dropna()
    net_liq = (liq_df['WALCL'] - liq_df['TGA'] - liq_df['RRP']) / 1000000

    # Market Data
    tickers = yf.download(["GC=F", "DX-Y.NYB", "HG=F", "SI=F"], period="1y", interval="1d", progress=False)['Close']
    
    return real_yield, yield_curve, net_liq, tickers

try:
    real_yield, yield_curve, net_liq, market_data = fetch_all_data()

    # Current Readings & Changes
    ry_curr = real_yield.iloc[-1]
    ry_prev = real_yield.iloc[-5] # 5-day delta
    ry_delta = ry_curr - ry_prev

    yc_curr = yield_curve.iloc[-1]
    yc_prev = yield_curve.iloc[-5]
    yc_delta = yc_curr - yc_prev

    liq_curr = net_liq.iloc[-1]
    liq_prev = net_liq.iloc[-2] # Previous week delta
    liq_delta = liq_curr - liq_prev

    gold_curr = market_data['GC=F'].iloc[-1].item()
    dxy_curr = market_data['DX-Y.NYB'].iloc[-1].item()
    dxy_delta = dxy_curr - market_data['DX-Y.NYB'].iloc[-5].item()

    cu_au_ratio = (market_data['HG=F'] / market_data['GC=F']).iloc[-1].item()
    au_ag_ratio = (market_data['GC=F'] / market_data['SI=F']).iloc[-1].item()

    # ----------------- MACRO SCORING ENGINE -----------------
    score = 0
    # Rule 1: Real Yields (Weight: 3)
    if ry_delta < -0.05: score += 3
    elif ry_delta > 0.05: score -= 3

    # Rule 2: Net Liquidity (Weight: 2)
    if liq_delta > 0: score += 2
    elif liq_delta < 0: score -= 2

    # Rule 3: DXY Momentum (Weight: 2)
    if dxy_delta < -0.5: score += 2
    elif dxy_delta > 0.5: score -= 2

    # Rule 4: Yield Curve Direction (Weight: 1)
    if yc_curr > 0 and yc_delta > 0: score += 1 # Bull steepening
    elif yc_curr < 0: score -= 1 # Inverted/Tightening

    # ----------------- HEADER & BIAS DASHBOARD -----------------
    st.title("🪙 XAU/USD Macro Quantitative Engine")
    st.caption("Pure Rule-Based Fundamental Bias System | No Subjectivity")

    # Master Signal Banner
    if score >= 4:
        st.success(f"### 🟢 OVERALL MACRO BIAS: STRONG LONG (Score: +{score}/8)")
        trade_mandate = "Execute Long setups on technical pullbacks. Strictly avoid swing shorting."
    elif score <= -4:
        st.error(f"### 🔴 OVERALL MACRO BIAS: STRONG SHORT (Score: {score}/8)")
        trade_mandate = "Execute Short setups on technical rallies. Strictly avoid swing buying."
    else:
        st.warning(f"### 🟡 OVERALL MACRO BIAS: NEUTRAL / RANGE-BOUND (Score: {score}/8)")
        trade_mandate = "No clear macro directional edge. Play key support/resistance ranges or reduce size."

    st.info(f"**Execution Mandate:** {trade_mandate}")
    st.markdown("---")

    # ----------------- SECTION 1: 10Y REAL YIELDS (TIPS) -----------------
    st.subheader("1. US 10Y Real Yield (DFII10)")
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric("10Y Real Yield", f"{ry_curr:.2f}%", delta=f"{ry_delta:.2f}% (5D)", delta_color="inverse")
        if ry_delta < 0:
            status = "🟢 Bullish Driver"
            why = "Real yields falling reduces opportunity cost of holding non-yielding gold."
            do = "Favor Long trades."
            dont = "Do not take counter-trend swing shorts."
        else:
            status = "🔴 Bearish Driver"
            why = "Rising real yields offer risk-free real returns in bonds, pulling capital from gold."
            do = "Favor Short trades or stay on cash."
            dont = "Do not buy breakouts without high technical volume."

        st.markdown(f"**Data Status:** {status}")
        st.markdown(f"**Why:** {why}")
        st.markdown(f"**What TO DO:** `{do}`")
        st.markdown(f"**What NOT TO DO:** `{dont}`")

    with c2:
        fig_ry = go.Figure()
        fig_ry.add_trace(go.Scatter(x=real_yield.index[-120:], y=real_yield.values[-120:], line=dict(color='#00FFCC', width=2), name="10Y Real Yield"))
        fig_ry.update_layout(height=240, margin=dict(l=0, r=0, t=20, b=0), template="plotly_dark")
        st.plotly_chart(fig_ry, use_container_width=True)

    st.markdown("---")

    # ----------------- SECTION 2: FED NET LIQUIDITY -----------------
    st.subheader("2. Fed Net Liquidity Index (Assets - TGA - RRP)")
    c3, c4 = st.columns([1, 2])
    with c3:
        st.metric("Net Liquidity", f"${liq_curr:.2f} T", delta=f"${liq_delta:.2f} T (DoD)")
        if liq_delta > 0:
            status_liq = "🟢 Expanding Liquidity"
            why_liq = "Treasury spending / Fed balance expansion adds USD supply to markets."
            do_liq = "Look for long continuation setups."
            dont_liq = "Do not fade sharp upward moves."
        else:
            status_liq = "🔴 Contracting Liquidity (QT)"
            why_liq = "TGA accumulation or QT drains cash from banking system."
            do_liq = "Tighten stop losses on long positions."
            dont_liq = "Do not hold overleveraged long swing trades."

        st.markdown(f"**Data Status:** {status_liq}")
        st.markdown(f"**Why:** {why_liq}")
        st.markdown(f"**What TO DO:** `{do_liq}`")
        st.markdown(f"**What NOT TO DO:** `{dont_liq}`")

    with c4:
        fig_liq = go.Figure()
        fig_liq.add_trace(go.Scatter(x=net_liq.index[-120:], y=net_liq.values[-120:], line=dict(color='#FFAA00', width=2), name="Fed Net Liquidity ($T)"))
        fig_liq.update_layout(height=240, margin=dict(l=0, r=0, t=20, b=0), template="plotly_dark")
        st.plotly_chart(fig_liq, use_container_width=True)

    st.markdown("---")

    # ----------------- SECTION 3: YIELD CURVE & MACRO RATIOS -----------------
    st.subheader("3. Intermarket Ratios & Yield Curve")
    col_a, col_b, col_c = st.columns(3)
    
    with col_a:
        st.metric("2Y-10Y Curve Spread", f"{yc_curr:.2f}%", delta=f"{yc_delta:.2f}%")
        st.caption("Un-inverting rapidly indicates late-cycle recessionary shift (Bullish Gold).")

    with col_b:
        st.metric("Copper / Gold Ratio", f"{cu_au_ratio:.6f}")
        st.caption("Falling ratio = Global growth slowdown / Deflation hedge (Bullish Gold outperformance).")

    with col_c:
        st.metric("Gold / Silver Ratio", f"{au_ag_ratio:.2f}")
        st.caption("Ratio > 85 indicates risk-off fear regime. Ratio < 75 indicates industrial risk-on expansion.")

except Exception as e:
    st.error(f"Data Fetching Error: {e}")
    st.info("Check if your FRED API Key is valid and internet connection is active.")
  
