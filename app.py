import streamlit as st
import yfinance as yf
from fredapi import Fred
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import requests

# Page Setup
st.set_page_config(page_title="Gold Institutional Macro Engine", page_icon="🪙", layout="wide")

# Styling
st.markdown("""
    <style>
    .metric-card { background-color: #1E1E1E; padding: 12px; border-radius: 8px; margin-bottom: 8px; }
    </style>
""", unsafe_allow_html=True)

FRED_API_KEY = "046a4bd419f0838d904a47cac99f6f86"  # अपनी 32-अक्षर की FRED API Key यहाँ डालें
fred = Fred(api_key=FRED_API_KEY)

# ----------------- DATA FETCHING -----------------
@st.cache_data(ttl=1800)
def fetch_macro_and_market():
    # 1. FRED Macro Data
    real_yield = fred.get_series('DFII10').dropna()
    yield_2y = fred.get_series('DGS2').dropna()
    yield_10y = fred.get_series('DGS10').dropna()
    walcl = fred.get_series('WALCL').dropna()
    tga = fred.get_series('WTREGEN').dropna()
    rrp = fred.get_series('RRPONTSYD').dropna()

    yc_df = pd.DataFrame({'2Y': yield_2y, '10Y': yield_10y}).dropna()
    yield_curve = yc_df['10Y'] - yc_df['2Y']

    liq_df = pd.DataFrame({'WALCL': walcl, 'TGA': tga, 'RRP': rrp}).dropna()
    net_liq = (liq_df['WALCL'] - liq_df['TGA'] - liq_df['RRP']) / 1000000

    # 2. Market Prices
    tickers = yf.download(["GC=F", "DX-Y.NYB", "HG=F", "SI=F"], period="1y", interval="1d", progress=False)['Close']
    
    # 3. Gold & DXY 30-Day Rolling Correlation
    returns = tickers[['GC=F', 'DX-Y.NYB']].pct_change()
    rolling_corr = returns['GC=F'].rolling(window=30).corr(returns['DX-Y.NYB']).dropna()

    return real_yield, yield_curve, net_liq, tickers, rolling_corr

@st.cache_data(ttl=86400)
def fetch_cot_data():
    """CFTC Public API से Gold Futures Managed Money/Non-Commercials डेटा"""
    try:
        url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?cftc_contract_market_code=088691&$limit=10&$order=report_date_as_yyyy_mm_dd%20DESC"
        res = requests.get(url, timeout=10)
        data = res.json()
        df_cot = pd.DataFrame(data)
        
        long_pos = float(df_cot['noncomm_positions_long_all'].iloc[0])
        short_pos = float(df_cot['noncomm_positions_short_all'].iloc[0])
        net_pos = long_pos - short_pos
        
        prev_net = float(df_cot['noncomm_positions_long_all'].iloc[1]) - float(df_cot['noncomm_positions_short_all'].iloc[1])
        cot_delta = net_pos - prev_net
        
        return net_pos, cot_delta, True
    except:
        return 0, 0, False

try:
    real_yield, yield_curve, net_liq, market_data, rolling_corr = fetch_macro_and_market()
    cot_net, cot_delta, cot_success = fetch_cot_data()

    # Calculations
    ry_curr = real_yield.iloc[-1]
    ry_delta = ry_curr - real_yield.iloc[-5]

    liq_curr = net_liq.iloc[-1]
    liq_delta = liq_curr - net_liq.iloc[-2]

    dxy_curr = market_data['DX-Y.NYB'].iloc[-1].item()
    dxy_delta = dxy_curr - market_data['DX-Y.NYB'].iloc[-5].item()
    corr_curr = rolling_corr.iloc[-1]

    cu_au_ratio = (market_data['HG=F'] / market_data['GC=F']).iloc[-1].item()
    au_ag_ratio = (market_data['GC=F'] / market_data['SI=F']).iloc[-1].item()

    # ----------------- SCORING ENGINE -----------------
    score = 0
    if ry_delta < -0.05: score += 3
    elif ry_delta > 0.05: score -= 3

    if liq_delta > 0: score += 2
    elif liq_delta < 0: score -= 2

    if dxy_delta < -0.5: score += 2
    elif dxy_delta > 0.5: score -= 2

    if cot_delta > 0: score += 1
    elif cot_delta < 0: score -= 1

    # ----------------- UI: HEADER & BIAS -----------------
    st.title("🪙 XAU/USD Institutional Macro Engine")
    st.caption("Pure Rule-Based Fundamental Bias | Central Bank & Liquidity Metrics")

    if score >= 4:
        st.success(f"### 🟢 OVERALL MACRO BIAS: STRONG LONG (Score: +{score}/8)")
        mandate = "Execute Long setups on technical pullbacks. Strictly avoid swing shorting."
    elif score <= -4:
        st.error(f"### 🔴 OVERALL MACRO BIAS: STRONG SHORT (Score: {score}/8)")
        mandate = "Execute Short setups on technical rallies. Strictly avoid swing buying."
    else:
        st.warning(f"### 🟡 OVERALL MACRO BIAS: NEUTRAL / RANGE-BOUND (Score: {score}/8)")
        mandate = "No macro trend advantage. Trade key support/resistance levels with strict stops."

    st.info(f"**Execution Mandate:** {mandate}")
    st.markdown("---")

    # ----------------- SECTION 1: COT INSTITUTIONAL & DXY CORRELATION -----------------
    st.subheader("1. Institutional Flows & DXY Correlation")
    col1, col2 = st.columns(2)

    with col1:
        if cot_success:
            st.metric("CFTC Net Managed Money", f"{int(cot_net):,} Contracts", delta=f"{int(cot_delta):,} (Weekly)")
            if cot_delta > 0:
                st.markdown("**Flow Status:** 🟢 Institutional Accumulation (Longs Adding)")
            else:
                st.markdown("**Flow Status:** 🔴 Institutional Distribution (Shorts Adding / Profit Booking)")
        else:
            st.warning("CFTC COT live feed is syncing.")

    with col2:
        st.metric("30D Gold vs DXY Correlation", f"{corr_curr:.2f}")
        if corr_curr < -0.60:
            st.markdown("**Regime:** 🟢 Strong Negative Correlation (Normal Macro Regime)")
        elif corr_curr > -0.20:
            st.markdown("**Regime:** ⚠️ Correlation Decoupled (War/Crisis or Sovereign Buying Driver)")

    st.markdown("---")

    # ----------------- SECTION 2: 10Y REAL YIELDS & LIQUIDITY -----------------
    st.subheader("2. US 10Y Real Yield & Fed Net Liquidity")
    c1, c2 = st.columns(2)

    with c1:
        st.metric("10Y Real Yield (DFII10)", f"{ry_curr:.2f}%", delta=f"{ry_delta:.2f}% (5D)", delta_color="inverse")
        fig_ry = go.Figure()
        fig_ry.add_trace(go.Scatter(x=real_yield.index[-90:], y=real_yield.values[-90:], line=dict(color='#00FFCC', width=2), name="10Y Real Yield"))
        fig_ry.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0), template="plotly_dark")
        st.plotly_chart(fig_ry, use_container_width=True, config={'displayModeBar': False})

    with c2:
        st.metric("Fed Net Liquidity", f"${liq_curr:.2f} T", delta=f"${liq_delta:.2f} T (DoD)")
        fig_liq = go.Figure()
        fig_liq.add_trace(go.Scatter(x=net_liq.index[-90:], y=net_liq.values[-90:], line=dict(color='#FFAA00', width=2), name="Net Liquidity"))
        fig_liq.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0), template="plotly_dark")
        st.plotly_chart(fig_liq, use_container_width=True, config={'displayModeBar': False})

    st.markdown("---")

    # ----------------- SECTION 3: INTERMARKET RATIOS -----------------
    st.subheader("3. Macro Spread & Ratios")
    r1, r2, r3 = st.columns(3)
    r1.metric("2Y-10Y Curve Spread", f"{yield_curve.iloc[-1]:.2f}%")
    r2.metric("Copper / Gold Ratio", f"{cu_au_ratio:.6f}")
    r3.metric("Gold / Silver Ratio", f"{au_ag_ratio:.2f}")

except Exception as e:
    st.error(f"Error updating dashboard: {e}")
    
