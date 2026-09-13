import os
import json
import streamlit as st
import yfinance as yf
from fredapi import Fred
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timezone

# Page Setup
st.set_page_config(page_title="Gold Institutional Macro & GEX Engine", page_icon="🪙", layout="wide")

FRED_API_KEY = "73f33ecb948906c7197f3e0a042e5e3f"
fred = Fred(api_key=FRED_API_KEY)
GEX_CACHE_FILE = "gex_levels.json"

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

    # 2. Market Prices with Session Header to Avoid Headless Cloud Blocks
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    
    gold = yf.download("GC=F", period="1y", interval="1d", session=session, progress=False)['Close'].squeeze().dropna()
    dxy = yf.download("DX-Y.NYB", period="1y", interval="1d", session=session, progress=False)['Close'].squeeze().dropna()
    copper = yf.download("HG=F", period="1y", interval="1d", session=session, progress=False)['Close'].squeeze().dropna()
    silver = yf.download("SI=F", period="1y", interval="1d", session=session, progress=False)['Close'].squeeze().dropna()

    # Fallback to PAXG/USDT if CME futures are choked on cloud runner
    if gold.empty or (isinstance(gold, pd.Series) and gold.iloc[-1] <= 1500.0):
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT", timeout=4)
            live_px = float(r.json().get("price", 0.0))
            if live_px > 1500:
                gold = pd.Series([live_px], index=[pd.Timestamp.now()])
        except Exception:
            pass

    # Align Data for Correlations & Ratios
    combined = pd.DataFrame({'GC': gold, 'DXY': dxy, 'HG': copper, 'SI': silver}).dropna()
    if not combined.empty and 'GC' in combined and 'DXY' in combined:
        returns = combined[['GC', 'DXY']].pct_change().dropna()
        rolling_corr = returns['GC'].rolling(window=30).corr(returns['DXY']).dropna()
    else:
        rolling_corr = pd.Series([0.0])

    return real_yield, yield_curve, net_liq, combined, rolling_corr

@st.cache_data(ttl=86400)
def fetch_cot_data():
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
    except Exception:
        return 0, 0, False

def load_gex_telemetry():
    """Reads cached institutional options GEX, DEX & Gamma Blast metrics."""
    if os.path.exists(GEX_CACHE_FILE):
        try:
            with open(GEX_CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def render_gex_dashboard_section(current_spot: float):
    st.subheader("🛡️ Institutional Options Gamma & Net Delta Exposure (GEX / DEX)")

    gex_data = load_gex_telemetry()
    if not gex_data or gex_data.get("status") == "FAILED":
        st.warning("⚠️ Institutional Options GEX/DEX cache is syncing or unavailable.")
        return

    call_wall = float(gex_data.get("call_wall_xau", 0.0))
    put_wall = float(gex_data.get("put_wall_xau", 0.0))
    flip_point = float(gex_data.get("gamma_flip_xau", 0.0))
    regime = gex_data.get("net_gamma_regime", "UNKNOWN")
    sync_time = gex_data.get("timestamp_utc", "N/A")
    net_dex_m = float(gex_data.get("net_dex_m", 0.0))
    gamma_blast_active = bool(gex_data.get("gamma_blast_active", False))

    # Synchronize reference spot if combined history was flat
    spot_ref = float(gex_data.get("spot_xau", current_spot)) if current_spot <= 1500 else current_spot

    # 1. Metric Indicators Ribbon (5 Institutional Pillars)
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Institutional Call Wall", f"${call_wall:.2f}", delta=f"{call_wall - spot_ref:+.2f} USD")
    with col2:
        st.metric("Institutional Put Wall", f"${put_wall:.2f}", delta=f"{spot_ref - put_wall:+.2f} USD")
    with col3:
        st.metric("Gamma Neutral Flip", f"${flip_point:.2f}")
    with col4:
        dex_color = "normal" if net_dex_m >= 0 else "inverse"
        st.metric("Net Delta (DEX)", f"{net_dex_m:+.1f}M", delta="Dealer Net Long" if net_dex_m >= 0 else "Dealer Net Short", delta_color=dex_color)
    with col5:
        if gamma_blast_active:
            st.metric("Gamma Blast Status", "🚀 ACTIVE SQUEEZE", delta="Barrier Exemption ON", delta_color="normal")
        else:
            regime_icon = "🟩" if "LONG_GAMMA" in regime else "🟥"
            st.metric("Dealer Regime", f"{regime_icon} {regime.split('_')[0]}", delta="Corridors Binding")

    st.caption(f"Last Options Sweep: `{sync_time}` | Conv Ratio: `{gex_data.get('conv_ratio', 'N/A')}` | Regime: `{regime}`")

    # 2. Interactive Structural Plotly Chart
    fig = go.Figure()
    y_min = min(put_wall - 15, spot_ref - 20)
    y_max = max(call_wall + 15, spot_ref + 20)

    # Put Wall Band
    fig.add_hrect(
        y0=put_wall - 3.0, y1=put_wall,
        fillcolor="rgba(0, 230, 118, 0.15)", line_width=1, line_color="#00E676",
        annotation_text="Institutional Put Wall (Floor)", annotation_position="bottom right"
    )

    # Call Wall Band
    fig.add_hrect(
        y0=call_wall, y1=call_wall + 3.0,
        fillcolor="rgba(255, 23, 68, 0.15)", line_width=1, line_color="#FF1744",
        annotation_text="Institutional Call Wall (Ceiling)", annotation_position="top right"
    )

    # Flip Line
    fig.add_hline(
        y=flip_point, line_dash="dash", line_color="#FFD700",
        annotation_text="Gamma Neutral Flip", annotation_position="top left"
    )

    # Live Spot Point Marker
    fig.add_trace(go.Scatter(
        x=[datetime.now(timezone.utc).strftime("%H:%M:%S UTC")],
        y=[spot_ref],
        mode="markers+text",
        marker=dict(size=14, color="#00FFFF", symbol="diamond"),
        name="Spot Gold",
        text=[f"${spot_ref:.2f}"],
        textposition="top center"
    ))

    fig.update_layout(
        title="Dealer Gamma Corridors vs Live Price Action",
        yaxis=dict(title="XAU/USD (USD)", range=[y_min, y_max]),
        height=320,
        margin=dict(l=20, r=20, t=35, b=20),
        template="plotly_dark"
    )
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

    # 3. Dynamic Execution Guardrail & Gamma Blast Status
    dist_call = call_wall - spot_ref
    dist_put = spot_ref - put_wall

    if gamma_blast_active:
        st.info("🚀 **GAMMA BLAST EXEMPTION ACTIVE**: Dealers are short gamma with heavy directional DEX imbalance. Gatekeeper resistance walls are overridden for squeeze breakout trades.")
    elif 0 <= dist_call <= 4.0:
        st.error(f"🛑 **LONG GATEKEEPER ACTIVE**: Spot is within ${dist_call:.2f} of the Dealer Call Wall. Breakout upside is capped by dealer short covering absorption.")
    elif 0 <= dist_put <= 4.0:
        st.error(f"🛑 **SHORT GATEKEEPER ACTIVE**: Spot is within ${dist_put:.2f} of the Dealer Put Wall. Downward expansion blocked by dealer put cushioning.")
    else:
        st.success("✅ **GEX CLEARANCE**: Spot is navigating open volatility corridors. Algorithmic setups unblocked.")

try:
    real_yield, yield_curve, net_liq, combined, rolling_corr = fetch_macro_and_market()
    cot_net, cot_delta, cot_success = fetch_cot_data()

    # Metric Deltas
    ry_curr = real_yield.iloc[-1]
    ry_delta = ry_curr - real_yield.iloc[-5]

    liq_curr = net_liq.iloc[-1]
    liq_delta = liq_curr - net_liq.iloc[-2]

    yc_curr = yield_curve.iloc[-1]
    yc_delta = yc_curr - yield_curve.iloc[-5]

    dxy_curr = combined['DXY'].iloc[-1] if not combined.empty and 'DXY' in combined else 100.0
    dxy_delta = dxy_curr - combined['DXY'].iloc[-5] if not combined.empty and 'DXY' in combined and len(combined) >= 5 else 0.0
    corr_curr = rolling_corr.iloc[-1] if not rolling_corr.empty else -0.50

    cu_au_ratio = (combined['HG'].iloc[-1] / combined['GC'].iloc[-1]) if not combined.empty and 'HG' in combined and 'GC' in combined else 0.0
    au_ag_ratio = (combined['GC'].iloc[-1] / combined['SI'].iloc[-1]) if not combined.empty and 'SI' in combined and 'GC' in combined else 0.0
    
    # Priority Spot Resolution for UI
    cached_gex = load_gex_telemetry()
    if cached_gex and float(cached_gex.get("spot_xau", 0.0)) > 1500.0:
        live_gold_spot = float(cached_gex.get("spot_xau"))
    elif not combined.empty and 'GC' in combined and float(combined['GC'].iloc[-1]) > 1500.0:
        live_gold_spot = float(combined['GC'].iloc[-1])
    else:
        live_gold_spot = 4408.0

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

    # ----------------- UI: MASTER BIAS -----------------
    st.title("🪙 XAU/USD Institutional Macro Engine")
    st.caption("Pure Rule-Based Fundamental Bias | Central Bank & Options Quantitative Metrics")

    if score >= 4:
        st.success(f"### 🟢 OVERALL MACRO BIAS: STRONG LONG (Score: +{score}/8)")
        mandate = "Execute Long setups on technical pullbacks. Strictly avoid swing shorting."
    elif score <= -4:
        st.error(f"### 🔴 OVERALL MACRO BIAS: STRONG SHORT (Score: {score}/8)")
        mandate = "Execute Short setups on technical rallies. Strictly avoid swing buying."
    else:
        st.warning(f"### 🟡 OVERALL MACRO BIAS: NEUTRAL / RANGE-BOUND (Score: {score}/8)")
        mandate = "No clear macro directional edge. Play key support/resistance ranges or reduce size."

    st.info(f"**Execution Mandate:** {mandate}")
    st.markdown("---")

    # ----------------- SECTION 0: INSTITUTIONAL GEX & DEX MATRIX -----------------
    render_gex_dashboard_section(current_spot=live_gold_spot)
    st.markdown("---")

    # ----------------- SECTION 1: INSTITUTIONAL & DXY CORRELATION -----------------
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
            st.markdown("**Regime:** 🟢 Strong Inverse Correlation (Normal Macro Driver)")
        elif corr_curr > -0.20:
            st.markdown("**Regime:** ⚠️ Decoupled Correlation (Geopolitical / Sovereign OTC Driver)")

    st.markdown("---")

    # ----------------- SECTION 2: 10Y REAL YIELDS -----------------
    st.subheader("2. US 10Y Real Yield (DFII10)")
    c1, c2 = st.columns([1, 1])

    with c1:
        st.metric("10Y Real Yield", f"{ry_curr:.2f}%", delta=f"{ry_delta:.2f}% (5D)", delta_color="inverse")
        if ry_delta < 0:
            st.markdown("**Data Status:** 🟢 Bullish Driver")
            st.markdown("**Why:** Real yields falling reduces opportunity cost of holding non-yielding gold.")
            st.markdown("**What TO DO:** `Favor Long trades.`")
            st.markdown("**What NOT TO DO:** `Do not take counter-trend swing shorts.`")
        else:
            st.markdown("**Data Status:** 🔴 Bearish Driver")
            st.markdown("**Why:** Rising real yields offer risk-free real returns in bonds, pulling capital from gold.")
            st.markdown("**What TO DO:** `Favor Short trades or stay on cash.`")
            st.markdown("**What NOT TO DO:** `Do not buy breakouts without high technical volume.`")

    with c2:
        fig_ry = go.Figure()
        fig_ry.add_trace(go.Scatter(x=real_yield.index[-90:], y=real_yield.values[-90:], line=dict(color='#00FFCC', width=2), name="10Y Real Yield"))
        fig_ry.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0), template="plotly_dark")
        st.plotly_chart(fig_ry, use_container_width=True, config={'displayModeBar': False})

    st.markdown("---")

    # ----------------- SECTION 3: FED NET LIQUIDITY -----------------
    st.subheader("3. Fed Net Liquidity Index (Assets - TGA - RRP)")
    c3, c4 = st.columns([1, 1])

    with c3:
        st.metric("Net Liquidity", f"${liq_curr:.2f} T", delta=f"${liq_delta:.2f} T (DoD)")
        if liq_delta > 0:
            st.markdown("**Data Status:** 🟢 Expanding Liquidity")
            st.markdown("**Why:** Treasury spending / Fed balance expansion adds USD supply to markets.")
            st.markdown("**What TO DO:** `Look for long continuation setups.`")
            st.markdown("**What NOT TO DO:** `Do not fade sharp upward moves.`")
        else:
            st.markdown("**Data Status:** 🔴 Contracting Liquidity (QT)")
            st.markdown("**Why:** TGA accumulation or QT drains cash from banking system.")
            st.markdown("**What TO DO:** `Tighten stop losses on long positions.`")
            st.markdown("**What NOT TO DO:** `Do not hold overleveraged long swing trades.`")

    with c4:
        fig_liq = go.Figure()
        fig_liq.add_trace(go.Scatter(x=net_liq.index[-90:], y=net_liq.values[-90:], line=dict(color='#FFAA00', width=2), name="Net Liquidity"))
        fig_liq.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0), template="plotly_dark")
        st.plotly_chart(fig_liq, use_container_width=True, config={'displayModeBar': False})

    st.markdown("---")

    # ----------------- SECTION 4: INTERMARKET RATIOS -----------------
    st.subheader("4. Intermarket Ratios & Yield Curve")
    r1, r2, r3 = st.columns(3)
    
    with r1:
        st.metric("2Y-10Y Curve Spread", f"{yc_curr:.2f}%", delta=f"{yc_delta:.2f}%")
        st.caption("Un-inverting rapidly indicates late-cycle recessionary shift (Bullish Gold).")

    with r2:
        st.metric("Copper / Gold Ratio", f"{cu_au_ratio:.6f}")
        st.caption("Falling ratio = Global growth slowdown / Deflation hedge (Bullish Gold outperformance).")

    with r3:
        st.metric("Gold / Silver Ratio", f"{au_ag_ratio:.2f}")
        st.caption("Ratio > 85 indicates risk-off fear regime. Ratio < 75 indicates industrial risk-on expansion.")

except Exception as e:
    st.error(f"Error: {e}")
    
