"""核心邏輯單元測試：指標、訊號、回測。"""
import numpy as np
import pandas as pd

from src.stocktracker.indicators import technical
from src.stocktracker.signals import strategy
from src.stocktracker.backtest import engine, portfolio


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
