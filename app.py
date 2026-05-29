"""Streamlit 儀表板：美股當沖訊號系統（清楚易懂版）。

啟動：streamlit run app.py
"""
from __future__ import annotations

import os

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config import settings
from src.stocktracker.backtest import engine, optimize
from src.stocktracker.data import alpaca_client
from src.stocktracker.signals import strategy

st.set_page_config(page_title="美股當沖訊號系統", layout="wide",
                   page_icon="📈")

# 密碼保護：雲端公開網址時設定 APP_PASSWORD 環境變數即啟用；本機不設則略過
_APP_PASSWORD = os.getenv("APP_PASSWORD")
if _APP_PASSWORD:
    if not st.session_state.get("authed"):
        pw = st.text_input("請輸入密碼", type="password")
        if pw == _APP_PASSWORD:
            st.session_state["authed"] = True
            st.rerun()
        elif pw:
            st.error("密碼錯誤")
        st.stop()

# ---------- 樣式 ----------
st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
.hero {border-radius: 16px; padding: 28px 32px; margin-bottom: 8px;
       color: white; box-shadow: 0 4px 20px rgba(0,0,0,0.12);}
.hero-buy  {background: linear-gradient(135deg,#16a34a,#22c55e);}
.hero-sell {background: linear-gradient(135deg,#dc2626,#ef4444);}
.hero-hold {background: linear-gradient(135deg,#6b7280,#9ca3af);}
.hero h1 {font-size: 44px; margin: 0; font-weight: 800;}
.hero p  {font-size: 17px; margin: 6px 0 0; opacity: .95;}
.pricebox {border-radius: 12px; padding: 16px 18px; text-align: center;
           background: #f8fafc; border: 1px solid #e2e8f0;}
.pricebox .label {font-size: 13px; color: #64748b; margin-bottom: 4px;}
.pricebox .value {font-size: 24px; font-weight: 700;}
.green {color:#16a34a;} .red {color:#dc2626;} .slate {color:#334155;}
.verdict {border-radius: 12px; padding: 14px 18px; font-size: 15px;
          margin-top: 8px;}
.verdict-good {background:#dcfce7; color:#166534;}
.verdict-bad  {background:#fee2e2; color:#991b1b;}
.verdict-mid  {background:#fef9c3; color:#854d0e;}
</style>
""", unsafe_allow_html=True)

st.title("📈 美股當沖訊號系統")
st.caption("僅供決策輔助，非投資建議。當沖風險極高，請務必先用模擬倉（Paper Trading）長期驗證再碰真錢。")

# ---------- 側欄 ----------
with st.sidebar:
    st.header("⚙️ 設定")
    symbol = st.text_input("股票代號", value="AAPL",
                           help="輸入美股代號，例如 AAPL、TSLA、NVDA").upper().strip()
    timeframe = st.selectbox(
        "K 棒粒度", ["1Min", "5Min", "15Min", "1Hour", "1Day"], index=1,
        help="每根 K 棒代表多久。當沖常用 1~15 分鐘")
    lookback = st.slider("回看天數", 1, 60, 5,
                         help="抓取最近幾天的資料")
    trend_filter = st.checkbox(
        "啟用趨勢過濾", value=True,
        help="只順著長期均線方向交易，避免逆勢被巴。實測在 1 小時以上明顯提升勝率")
    st.divider()
    st.caption("資料來源：Alpaca 免費 IEX feed（約涵蓋全美 2~3% 成交量，"
               "適合學習與驗證；實戰建議升級付費 SIP 全市場資料）")


@st.cache_data(ttl=60, show_spinner=False)
def fetch(symbol: str, timeframe: str, lookback: int):
    return alpaca_client.get_bars(symbol, timeframe, lookback_days=lookback)


if not symbol:
    st.info("請在左側輸入股票代號。")
    st.stop()

try:
    with st.spinner(f"正在抓取 {symbol} 的資料…"):
        df = fetch(symbol, timeframe, lookback)
except Exception as exc:
    st.error(f"抓取資料失敗：{exc}")
    st.stop()

if df.empty:
    st.warning("查無資料，請確認代號或調整回看天數。")
    st.stop()

# 目前使用的參數（可被最佳化結果覆寫，存在 session_state）
params = st.session_state.get("params", dict(settings.STRATEGY_PARAMS))
using_optimized = st.session_state.get("optimized_for") == (symbol, timeframe)
if not using_optimized and "params" in st.session_state:
    # 換了標的/粒度就回到預設，避免套用到不適用的最佳化參數
    params = dict(settings.STRATEGY_PARAMS)

params["use_trend_filter"] = trend_filter

enriched = strategy.signal_series(df, params)
sig = strategy.latest_signal(df, params)

if using_optimized:
    st.success(f"✅ 目前套用 {symbol} ({timeframe}) 的最佳化參數")

# ---------- 主建議卡 ----------
hero_class = {"BUY": "hero-buy", "SELL": "hero-sell", "HOLD": "hero-hold"}[sig.action]
action_zh = {"BUY": "建議買進 🟢", "SELL": "建議賣出 🔴", "HOLD": "建議觀望 ⚪"}[sig.action]
action_desc = {
    "BUY": "目前多項指標偏多，是相對有利的進場時機。",
    "SELL": "目前多項指標偏空，宜減碼或放空（當沖）。",
    "HOLD": "多空訊號不明確，建議先觀望、不要進場。",
}[sig.action]

st.markdown(f"""
<div class="hero {hero_class}">
  <h1>{symbol}　{action_zh}</h1>
  <p>{action_desc}　現價 ${sig.price:.2f}　|　訊號信心 {sig.confidence}/100</p>
</div>
""", unsafe_allow_html=True)

# 信心度進度條
st.progress(sig.confidence / 100,
            text=f"訊號信心：{sig.confidence}/100（越高代表越多指標一致）")

# ---------- 進場 / 停損 / 停利 ----------
if sig.action in ("BUY", "SELL"):
    risk = abs(sig.price - sig.stop_loss)
    reward = abs(sig.take_profit - sig.price)
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"""<div class="pricebox"><div class="label">建議進場價</div>
                <div class="value slate">${sig.price:.2f}</div></div>""",
                unsafe_allow_html=True)
    c2.markdown(f"""<div class="pricebox"><div class="label">停損價（虧損出場）</div>
                <div class="value red">${sig.stop_loss:.2f}</div></div>""",
                unsafe_allow_html=True)
    c3.markdown(f"""<div class="pricebox"><div class="label">停利價（獲利出場）</div>
                <div class="value green">${sig.take_profit:.2f}</div></div>""",
                unsafe_allow_html=True)
    st.caption(
        f"💡 風險報酬比約 1:{params['risk_reward']:.0f}"
        f"（願意冒 ${risk:.2f} 風險，目標賺 ${reward:.2f}）。"
        f" 停損停利依當前波動度 ATR=${sig.atr:.2f} 自動計算。"
    )
else:
    st.info("目前為觀望訊號，暫不提供進場價位。等出現明確的買進或賣出訊號再行動。")

# ---------- 訊號理由 ----------
with st.expander("📋 為什麼是這個建議？（點開看理由）", expanded=True):
    for r in sig.reasons:
        st.markdown(f"- {r}")
    st.caption("訊號由 4 個技術指標投票決定：EMA 快慢線、價格相對 VWAP、"
               "RSI 超買超賣、MACD 動能。淨票數達標才出買/賣訊號。")

st.divider()

# ---------- 走勢圖 ----------
st.subheader(f"📊 {symbol} 走勢與指標")
fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28],
    vertical_spacing=0.04,
    subplot_titles=("價格 / EMA / VWAP", "RSI（超買超賣）"),
)
fig.add_trace(go.Candlestick(
    x=enriched.index, open=enriched["open"], high=enriched["high"],
    low=enriched["low"], close=enriched["close"], name="K線",
    increasing_line_color="#22c55e", decreasing_line_color="#ef4444"),
    row=1, col=1)
fig.add_trace(go.Scatter(x=enriched.index, y=enriched["ema_fast"],
                         name="EMA快(9)", line=dict(width=1.2, color="#3b82f6")), row=1, col=1)
fig.add_trace(go.Scatter(x=enriched.index, y=enriched["ema_slow"],
                         name="EMA慢(21)", line=dict(width=1.2, color="#f59e0b")), row=1, col=1)
fig.add_trace(go.Scatter(x=enriched.index, y=enriched["vwap"],
                         name="VWAP", line=dict(width=1.4, dash="dot", color="#8b5cf6")), row=1, col=1)
if trend_filter and "trend_ema" in enriched.columns:
    fig.add_trace(go.Scatter(x=enriched.index, y=enriched["trend_ema"],
                             name=f"趨勢線(EMA{params.get('trend_ema',50)})",
                             line=dict(width=1.6, color="#64748b")), row=1, col=1)

# 停損/停利參考線
if sig.action in ("BUY", "SELL"):
    fig.add_hline(y=sig.take_profit, line_dash="dash", line_color="#16a34a",
                  annotation_text="停利", annotation_position="right", row=1, col=1)
    fig.add_hline(y=sig.stop_loss, line_dash="dash", line_color="#dc2626",
                  annotation_text="停損", annotation_position="right", row=1, col=1)

fig.add_trace(go.Scatter(x=enriched.index, y=enriched["rsi"],
                         name="RSI", line=dict(width=1.4, color="#0ea5e9")), row=2, col=1)
fig.add_hline(y=params["rsi_overbought"],
              line_dash="dot", line_color="#ef4444", row=2, col=1)
fig.add_hline(y=params["rsi_oversold"],
              line_dash="dot", line_color="#22c55e", row=2, col=1)
fig.update_layout(height=620, xaxis_rangeslider_visible=False,
                  margin=dict(t=40, b=20, l=10, r=10),
                  legend=dict(orientation="h", yanchor="bottom", y=1.02,
                              xanchor="right", x=1),
                  hovermode="x unified")
st.plotly_chart(fig, use_container_width=True)

st.divider()

# ---------- 回測績效 ----------
st.subheader("🧪 策略歷史回測（此區間）")
st.caption("把這套訊號規則套到上面這段歷史資料，看看「假設照訊號交易」的結果。"
           "已含 ATR 停損停利與滑價，為保守估計。歷史表現不代表未來。")
try:
    result = engine.run_backtest(enriched, params)
    if result.num_trades == 0:
        st.info("此區間沒有產生任何完整交易，無法計算績效。試著拉長回看天數。")
    else:
        m = st.columns(5)
        m[0].metric("總報酬", f"{result.total_return_pct:+.2f}%")
        m[1].metric("勝率", f"{result.win_rate_pct:.1f}%")
        m[2].metric("交易次數", result.num_trades)
        m[3].metric("最大回撤", f"{result.max_drawdown_pct:.2f}%",
                    help="從高點下跌的最大幅度，越小越好")
        m[4].metric("夏普值", f"{result.sharpe:.2f}",
                    help="風險調整後報酬，>1 算不錯")

        # 白話判讀
        if result.total_return_pct > 0 and result.win_rate_pct >= 45:
            st.markdown('<div class="verdict verdict-good">✅ 這段期間策略整體獲利，'
                        '但樣本可能偏少，別急著上真錢——先用模擬倉跑久一點。</div>',
                        unsafe_allow_html=True)
        elif result.total_return_pct <= 0:
            st.markdown('<div class="verdict verdict-bad">⚠️ 這段期間策略是虧損的。'
                        '代表這套參數在此標的/區間不適用，需要調整或換標的。</div>',
                        unsafe_allow_html=True)
        else:
            st.markdown('<div class="verdict verdict-mid">🟡 結果普通。'
                        '建議調整參數或拉長測試區間再評估。</div>',
                        unsafe_allow_html=True)

        st.line_chart(result.equity_curve, height=220,
                      x_label="時間", y_label="資金倍數（起始=1）")

        with st.expander("查看每一筆交易明細"):
            import pandas as pd
            trade_df = pd.DataFrame([{
                "方向": "做多" if t.direction == 1 else "做空",
                "進場時間": t.entry_time, "進場價": round(t.entry_price, 2),
                "出場時間": t.exit_time, "出場價": round(t.exit_price, 2),
                "出場原因": t.exit_reason, "報酬%": round(t.return_pct, 2),
            } for t in result.trades])
            st.dataframe(trade_df, use_container_width=True, hide_index=True)
except Exception as exc:
    st.info(f"回測無法計算：{exc}")

st.divider()

# ---------- 參數最佳化 ----------
st.subheader("🔧 自動最佳化參數")
st.caption("自動嘗試多組參數，在「前 70% 資料（樣本內）」找最賺的設定，"
           "再拿「後 30% 資料（樣本外）」驗證。只有兩段都賺，才算真的有優勢"
           "（否則就是過擬合——在歷史上硬湊出好看數字，未來不管用）。")

col_a, col_b = st.columns([1, 3])
run_opt = col_a.button("🚀 開始最佳化", type="primary")
col_b.caption("建議用 1Hour 或 1Day 粒度、回看天數拉大（資料越多越可靠）。"
              "5/15 分鐘的當沖通常找不到穩健優勢。")

if run_opt:
    with st.spinner("正在搜尋最佳參數（測試多組組合）…"):
        opt = optimize.optimize(df, dict(settings.STRATEGY_PARAMS))
    if opt is None:
        st.warning("資料不足或找不到有效組合。請拉長回看天數，或改用較長的 K 棒粒度。")
    else:
        st.session_state["last_opt"] = opt
        st.session_state["last_opt_key"] = (symbol, timeframe)

opt = st.session_state.get("last_opt")
if opt is not None and st.session_state.get("last_opt_key") == (symbol, timeframe):
    bp = opt.best_params
    st.markdown(f"**找到的最佳參數**（共測試 {opt.tested_combos} 組）："
                f"EMA 快線 `{bp['ema_fast']}` / 慢線 `{bp['ema_slow']}`、"
                f"停損 `{bp['atr_stop_mult']}×ATR`、風報比 `1:{bp['risk_reward']:.0f}`")

    cc = st.columns(2)
    with cc[0]:
        st.markdown("**📘 樣本內（前 70%，用來找參數）**")
        st.metric("總報酬", f"{opt.in_sample.total_return_pct:+.2f}%")
        st.caption(f"勝率 {opt.in_sample.win_rate_pct:.1f}%・"
                   f"{opt.in_sample.num_trades} 筆・"
                   f"夏普 {opt.in_sample.sharpe:.2f}")
    with cc[1]:
        st.markdown("**📗 樣本外（後 30%，沒看過的資料）**")
        st.metric("總報酬", f"{opt.out_sample.total_return_pct:+.2f}%")
        st.caption(f"勝率 {opt.out_sample.win_rate_pct:.1f}%・"
                   f"{opt.out_sample.num_trades} 筆・"
                   f"夏普 {opt.out_sample.sharpe:.2f}")

    if opt.is_robust():
        st.markdown('<div class="verdict verdict-good">✅ <b>通過驗證！</b>'
                    '樣本內與樣本外都獲利，代表這組參數在這個標的/粒度上'
                    '有相對穩健的優勢。可以套用，並先用模擬倉實測。</div>',
                    unsafe_allow_html=True)
        if st.button(f"套用這組參數到 {symbol} 的分析"):
            st.session_state["params"] = bp
            st.session_state["optimized_for"] = (symbol, timeframe)
            st.rerun()
    else:
        st.markdown('<div class="verdict verdict-bad">⚠️ <b>未通過驗證。</b>'
                    '樣本外沒有同樣獲利，代表這只是對歷史的過度配適，'
                    '別套用。建議改用較長的 K 棒粒度（如 1Hour、1Day）或換標的再試。</div>',
                    unsafe_allow_html=True)

    with st.expander("查看樣本內前 10 名參數組合"):
        import pandas as pd
        lb = pd.DataFrame([{
            "EMA快": p["ema_fast"], "EMA慢": p["ema_slow"],
            "停損×ATR": p["atr_stop_mult"], "風報比": p["risk_reward"],
            "樣本內報酬%": round(ret, 2), "交易數": n,
        } for p, ret, n in opt.leaderboard])
        st.dataframe(lb, use_container_width=True, hide_index=True)
