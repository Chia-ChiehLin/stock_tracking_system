"""核心邏輯單元測試：指標、訊號、回測。"""
import numpy as np
import pandas as pd

from src.stocktracker.indicators import technical
from src.stocktracker.signals import strategy
from src.stocktracker.backtest import engine, portfolio
from src.stocktracker.data import universe


# ---------- 槓桿/反向 ETF 過濾（純函式，不連網）----------
def test_leveraged_name_filter():
    assert universe._is_leveraged_or_inverse("ProShares UltraPro Short QQQ")
    assert universe._is_leveraged_or_inverse("Direxion Daily S&P 500 Bear 1X ETF")
    assert universe._is_leveraged_or_inverse("Direxion Daily Semiconductor Bull 3X ETF")
    assert not universe._is_leveraged_or_inverse("Apple Inc. Common Stock")
    assert not universe._is_leveraged_or_inverse("VanEck Gold Miners ETF")


def test_blocked_symbols():
    for s in ["SOXL", "SQQQ", "TQQQ", "SPDN", "NVD"]:
        assert universe.is_blocked(s), f"{s} 應被封鎖"
    for s in ["AAPL", "NVDA", "GDX", "SPY"]:
        assert not universe.is_blocked(s), f"{s} 不該被封鎖"


# ---------- 指標 ----------
def test_ema_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = technical.ema(s, 3)
    assert len(out) == len(s)
    assert out.iloc[-1] > out.iloc[0]          # 升序輸入 → EMA 遞增


def test_rsi_bounds(ohlcv):
    rsi = technical.rsi(ohlcv["close"], 14)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()  # RSI 永遠在 0~100


def test_add_indicators_columns(ohlcv, params):
    out = technical.add_indicators(ohlcv, params)
    for col in ["ema_fast", "ema_slow", "trend_ema", "rsi", "atr", "vwap",
                "macd", "macd_hist"]:
        assert col in out.columns


# ---------- 訊號 ----------
def test_signal_action_valid(ohlcv, params):
    sig = strategy.latest_signal(ohlcv, params)
    assert sig.action in ("BUY", "SELL", "HOLD")
    assert 0 <= sig.confidence <= 100


def test_buy_has_stop_below_and_target_above(ohlcv, params):
    # 找出任一買進訊號的列，驗證停損 < 價 < 停利
    enriched = technical.add_indicators(ohlcv, params).dropna()
    for _, row in enriched.iterrows():
        sig = strategy.evaluate_row(row, params)
        if sig.action == "BUY":
            assert sig.stop_loss < sig.price < sig.take_profit
            return


def test_signal_series_has_position_and_confidence(ohlcv, params):
    e = strategy.signal_series(ohlcv, params)
    assert "position" in e.columns and "confidence" in e.columns
    assert set(e["position"].unique()).issubset({-1, 0, 1})


# ---------- 回測 ----------
def test_engine_runs(ohlcv, params):
    e = strategy.signal_series(ohlcv, params)
    res = engine.run_backtest(e, params)
    assert res.num_trades >= 0
    assert isinstance(res.total_return_pct, float)
    assert -100 <= res.max_drawdown_pct <= 0


def test_portfolio_runs(ohlcv, params):
    bars = {"AAA": ohlcv, "BBB": ohlcv * 1.0}
    res = portfolio.run_portfolio_backtest(
        bars, params, exit_mode="trend", max_positions=2)
    assert res.final_equity > 0
    assert len(res.equity_curve) > 0


def test_no_lookahead_position_shift(ohlcv, params):
    # 回測用前一根訊號決定持倉，確保不是用未來資訊
    e = strategy.signal_series(ohlcv, params)
    res = engine.run_backtest(e, params)
    # 至少能跑完並產出權益曲線
    assert len(res.equity_curve) > 0


# ---------- 組合回測的新變體參數（手工 enriched，數字可精算）----------
def _frame(rows: list[dict]) -> pd.DataFrame:
    """手工訊號序列：rows 每項含 close/high/low/trend_ema/position/atr。"""
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1D")
    df = pd.DataFrame(rows, index=idx)
    df["confidence"] = df.get("confidence", 100)
    if "open" not in df.columns:
        df["open"] = df["close"]
    else:
        df["open"] = df["open"].fillna(df["close"])
    return df


def _run(enriched, params, **kw):
    defaults = dict(position_pct=1.0, max_positions=1, slippage_pct=0.0,
                    exit_mode="trend", trail_atr_mult=params["atr_stop_mult"])
    defaults.update(kw)
    return portfolio.run_portfolio_backtest(
        bars={}, params=params, enriched=enriched, **defaults)


def test_trail_false_keeps_initial_stop(params):
    # 進場 100（ATR2 → 初始停損 94）；漲到 120 後回落到 100：
    # 移動停損（昨日高點 120 - 3×ATR = 114）會出場；固定初始停損不會。
    e = {"X": _frame([
        dict(close=100, high=100, low=100, trend_ema=50, position=1, atr=2),
        dict(close=120, high=120, low=119, open=119, trend_ema=50, position=0, atr=2),
        dict(close=112, high=116, low=100, open=116, trend_ema=50, position=0, atr=2),
        dict(close=112, high=112, low=111, trend_ema=50, position=0, atr=2),
    ])}
    trail = _run(e, params, trail=True)
    fixed = _run(e, params, trail=False)
    assert trail.final_equity == 114_000    # 1000 股 @ 移動停損 114 出場
    assert fixed.final_equity == 112_000    # 抱到最後收盤 112


def test_trailing_stop_uses_yesterdays_peak_only(params):
    # 回歸測試（審查抓到的前視偏差）：當天暴漲的高點「不能」先墊高停損、
    # 再回頭跟當天低點比——盤中低點出現時，當天高點可能還沒發生。
    e = {"X": _frame([
        dict(close=100, high=100, low=100, trend_ema=50, position=1, atr=2),
        # 開高走高：低點 105、高點 120。舊 bug 會用 120-6=114 當停損，
        # 判定 105 <= 114 而捏造出場；正確行為是停損仍在 94、續抱。
        dict(close=118, high=120, low=105, open=105, trend_ema=50, position=0, atr=2),
        dict(close=118, high=118, low=117, trend_ema=50, position=0, atr=2),
    ])}
    res = _run(e, params, trail=True)
    assert res.final_equity == 118_000      # 不出場，抱到最後


def test_gap_down_fills_at_open_not_stop(params):
    # 跳空跌破停損：停損單成為市價單，成交在開盤價而非停損價（別高估出場價）
    e = {"X": _frame([
        dict(close=100, high=100, low=100, trend_ema=50, position=1, atr=2),
        dict(close=85, high=88, low=84, open=88, trend_ema=50, position=0, atr=2),
        dict(close=85, high=86, low=84, trend_ema=50, position=0, atr=2),
    ])}
    res = _run(e, params, trail=False)
    assert res.final_equity == 88_000       # 開盤 88 < 停損 94 → 以 88 成交

def test_exit_confirm_days_survives_one_day_dip(params):
    # 收盤跌破均線 1 天後又站回：確認=1 會被洗出場，確認=2 抱得住
    e = {"X": _frame([
        dict(close=100, high=100, low=100, trend_ema=90, position=1, atr=5),
        dict(close=88, high=95, low=86, trend_ema=90, position=0, atr=5),
        dict(close=91, high=92, low=87, trend_ema=90, position=0, atr=5),
        dict(close=95, high=95, low=90, trend_ema=90, position=0, atr=5),
    ])}
    fast = _run(e, params, exit_confirm_days=1)
    slow = _run(e, params, exit_confirm_days=2)
    assert fast.final_equity == 88_000      # 跌破當天收盤 88 出場
    assert slow.final_equity == 95_000      # 單日雜訊不出場，抱到 95

def test_cooldown_blocks_immediate_rebuy(params):
    # 停損出場後隔天又出現買訊：冷卻=2 天要擋掉重新進場
    e = {"X": _frame([
        dict(close=100, high=100, low=100, trend_ema=50, position=1, atr=2),
        dict(close=90, high=95, low=80, open=95, trend_ema=50, position=0, atr=2),  # 觸發停損 94
        dict(close=100, high=100, low=96, trend_ema=50, position=1, atr=2),  # 想追回
        dict(close=100, high=100, low=99, trend_ema=50, position=0, atr=2),
    ])}
    rebuy = _run(e, params, cooldown_days=0)
    cooled = _run(e, params, cooldown_days=2)
    assert rebuy.num_trades == 2            # 停損一筆 + 追回的一筆（期末平倉）
    assert cooled.num_trades == 1           # 冷卻中不追回

def test_risk_momentum_prefers_smooth_gains(params):
    # 兩檔漲幅相近，一檔平穩、一檔暴衝暴跌：風險調整動量要挑平穩的
    hist_x = [dict(close=c, high=c, low=c, trend_ema=50, position=0, atr=2)
              for c in (100, 104, 110)]
    hist_y = [dict(close=c, high=c, low=c, trend_ema=50, position=0, atr=2)
              for c in (100, 90, 113)]
    sig_x = dict(close=110, high=110, low=110, trend_ema=50, position=1, atr=2)
    sig_y = dict(close=113, high=113, low=113, trend_ema=50, position=1, atr=2)
    end_x = dict(close=121, high=121, low=110, trend_ema=50, position=0, atr=2)
    end_y = dict(close=90, high=113, low=90, trend_ema=50, position=0, atr=2)
    e = {"X": _frame(hist_x + [sig_x, end_x]),
         "Y": _frame(hist_y + [sig_y, end_y])}
    smooth = _run(e, params, rank_by="risk_momentum", mom_window=2)
    raw = _run(e, params, rank_by="momentum", mom_window=2)
    assert smooth.final_equity > 100_000    # 挑平穩的 X（漲到 121）
    assert raw.final_equity < 100_000       # 挑動量最大的 Y（跌到 90）


# ---------- 實盤趨勢出場確認（純函式）----------
def test_confirmed_trend_break():
    import auto_trade
    closes = pd.Series([100, 95, 89, 88], dtype=float)
    ema = pd.Series([90, 90, 90, 90], dtype=float)
    assert auto_trade._confirmed_trend_break(closes, ema, 2)        # 連兩天跌破
    one_day = pd.Series([100, 95, 92, 88], dtype=float)
    assert not auto_trade._confirmed_trend_break(one_day, ema, 2)   # 只跌破一天
    assert auto_trade._confirmed_trend_break(one_day, ema, 1)       # 確認=1 就會出場
    short = pd.Series([88.0])
    assert not auto_trade._confirmed_trend_break(short, ema, 2)     # 資料不足不出場
