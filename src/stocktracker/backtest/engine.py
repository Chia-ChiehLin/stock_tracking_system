"""逐筆交易回測引擎：模擬進場、ATR 停損/停利、訊號反轉出場。

比向量化版本更貼近當沖實況：每筆交易在訊號出現的下一根開盤進場，
之後每根 K 棒檢查是否觸及停損或停利（用該根的 high/low），
或訊號反轉而出場。

重要：回測結果不代表未來；分鐘級回測仍無法完整模擬滑價與排隊成交，
這裡用「下一根開盤進場 + 固定滑價」做保守估計。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Trade:
    direction: int      # 1=做多, -1=做空
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    exit_reason: str    # "停利" | "停損" | "訊號反轉" | "資料結束"
    return_pct: float


@dataclass
class BacktestResult:
    total_return_pct: float
    win_rate_pct: float
    num_trades: int
    max_drawdown_pct: float
    sharpe: float
    avg_win_pct: float
    avg_loss_pct: float
    equity_curve: pd.Series
    trades: list[Trade] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"總報酬: {self.total_return_pct:+.2f}%  |  "
            f"勝率: {self.win_rate_pct:.1f}%  |  "
            f"交易次數: {self.num_trades}  |  "
            f"最大回撤: {self.max_drawdown_pct:.2f}%  |  "
            f"夏普: {self.sharpe:.2f}"
        )


def run_backtest(
    enriched: pd.DataFrame,
    params: dict,
    slippage_pct: float = 0.02,
) -> BacktestResult:
    """事件驅動回測，尊重 ATR 停損/停利。

    Args:
        enriched: signal_series() 輸出，需含 position/open/high/low/close/atr。
        params: STRATEGY_PARAMS，用 atr_stop_mult / risk_reward 設停損停利。
        slippage_pct: 單邊滑價百分比。
    """
    required = {"position", "open", "high", "low", "close", "atr"}
    if not required.issubset(enriched.columns) or enriched.empty:
        raise ValueError(f"enriched 需包含欄位 {required} 且不可為空")

    df = enriched.reset_index()
    time_col = df.columns[0]
    slip = slippage_pct / 100.0

    trades: list[Trade] = []
    equity = 1.0
    equity_points: list[tuple] = []

    in_trade = False
    direction = 0
    entry_price = stop = target = 0.0
    entry_time = None

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_signal = df.iloc[i - 1]["position"]
        price_open = row["open"]
        hi, lo, close = row["high"], row["low"], row["close"]

        if not in_trade:
            # 訊號出現 → 下一根開盤進場
            if prev_signal != 0:
                direction = int(prev_signal)
                entry_price = price_open * (1 + slip * direction)
                entry_time = row[time_col]
                atr = df.iloc[i - 1]["atr"]
                stop_dist = atr * params["atr_stop_mult"]
                target_dist = stop_dist * params["risk_reward"]
                if direction == 1:
                    stop, target = entry_price - stop_dist, entry_price + target_dist
                else:
                    stop, target = entry_price + stop_dist, entry_price - target_dist
                in_trade = True
            equity_points.append((row[time_col], equity))
            continue

        # 持倉中：依序檢查停損 → 停利 → 訊號反轉
        exit_price = None
        reason = ""
        if direction == 1:
            if lo <= stop:
                exit_price, reason = stop, "停損"
            elif hi >= target:
                exit_price, reason = target, "停利"
            elif prev_signal <= 0:
                exit_price, reason = close, "訊號反轉"
        else:  # 做空
            if hi >= stop:
                exit_price, reason = stop, "停損"
            elif lo <= target:
                exit_price, reason = target, "停利"
            elif prev_signal >= 0:
                exit_price, reason = close, "訊號反轉"

        if exit_price is not None:
            exit_fill = exit_price * (1 - slip * direction)
            ret = direction * (exit_fill - entry_price) / entry_price
            equity *= 1 + ret
            trades.append(Trade(
                direction=direction, entry_time=entry_time, entry_price=entry_price,
                exit_time=row[time_col], exit_price=exit_fill,
                exit_reason=reason, return_pct=ret * 100,
            ))
            in_trade = False
            direction = 0
        equity_points.append((row[time_col], equity))

    # 收尾：資料結束仍持倉，以最後收盤平倉
    if in_trade:
        last = df.iloc[-1]
        exit_fill = last["close"] * (1 - slip * direction)
        ret = direction * (exit_fill - entry_price) / entry_price
        equity *= 1 + ret
        trades.append(Trade(
            direction=direction, entry_time=entry_time, entry_price=entry_price,
            exit_time=last[time_col], exit_price=exit_fill,
            exit_reason="資料結束", return_pct=ret * 100,
        ))

    return _build_result(trades, equity_points, df, time_col)


def _build_result(trades, equity_points, df, time_col) -> BacktestResult:
    equity_curve = pd.Series(
        [e for _, e in equity_points],
        index=pd.to_datetime([t for t, _ in equity_points]),
    ) if equity_points else pd.Series(dtype=float)

    rets = [t.return_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    num_trades = len(trades)

    total_return = (equity_curve.iloc[-1] - 1) * 100 if len(equity_curve) else 0.0
    win_rate = (len(wins) / num_trades * 100) if num_trades else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    max_dd = _max_drawdown(equity_curve) * 100

    if rets and np.std(rets) > 0:
        sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(len(rets)))
    else:
        sharpe = 0.0

    return BacktestResult(
        total_return_pct=total_return,
        win_rate_pct=win_rate,
        num_trades=num_trades,
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        equity_curve=equity_curve,
        trades=trades,
    )


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min())
