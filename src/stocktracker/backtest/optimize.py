"""參數最佳化：網格搜尋 + 樣本內/樣本外驗證，防止過擬合。

核心原則：
- 把資料切成前段（樣本內 in-sample）與後段（樣本外 out-of-sample）。
- 只在「樣本內」找最佳參數，再拿到「樣本外」驗證。
- 若樣本外也賺，才算相對穩健；若樣本內賺、樣本外賠，就是過擬合。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import pandas as pd

from ..signals import strategy
from . import engine

# 預設搜尋網格（控制在合理組合數，速度可接受）
DEFAULT_GRID = {
    "ema_fast": [5, 9, 12],
    "ema_slow": [21, 26],
    "trend_ema": [50, 100, 200],
    "atr_stop_mult": [1.5, 2.0],
    "risk_reward": [1.5, 2.0, 3.0],
}

MIN_TRADES = 5  # 樣本內交易數低於此不採用，避免少數幸運交易


@dataclass
class OptimizeResult:
    best_params: dict
    in_sample: engine.BacktestResult
    out_sample: engine.BacktestResult
    tested_combos: int
    leaderboard: list[tuple]  # (params, in_sample_return, in_sample_trades)

    def is_robust(self) -> bool:
        """樣本內與樣本外都為正報酬，視為相對穩健。"""
        return (self.in_sample.total_return_pct > 0
                and self.out_sample.total_return_pct > 0)


def split_train_test(df: pd.DataFrame, train_frac: float = 0.7):
    n = len(df)
    cut = int(n * train_frac)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def _evaluate(df: pd.DataFrame, params: dict) -> engine.BacktestResult | None:
    enriched = strategy.signal_series(df, params)
    if enriched.empty:
        return None
    try:
        return engine.run_backtest(enriched, params)
    except ValueError:
        return None


def optimize(
    df: pd.DataFrame,
    base_params: dict,
    grid: dict | None = None,
    train_frac: float = 0.7,
) -> OptimizeResult | None:
    """在樣本內網格搜尋最佳參數，並於樣本外驗證。"""
    grid = grid or DEFAULT_GRID
    train, test = split_train_test(df, train_frac)
    if len(train) < 50 or len(test) < 20:
        return None  # 資料太少，最佳化沒意義

    keys = list(grid.keys())
    best = None
    best_return = float("-inf")
    leaderboard = []
    tested = 0

    for combo in itertools.product(*[grid[k] for k in keys]):
        params = dict(base_params)
        params.update(dict(zip(keys, combo)))
        # 跳過不合理組合（快線需小於慢線）
        if params["ema_fast"] >= params["ema_slow"]:
            continue

        res = _evaluate(train, params)
        tested += 1
        if res is None or res.num_trades < MIN_TRADES:
            continue
        leaderboard.append((params, res.total_return_pct, res.num_trades))
        if res.total_return_pct > best_return:
            best_return = res.total_return_pct
            best = (params, res)

    if best is None:
        return None

    best_params, in_sample = best
    out_sample = _evaluate(test, best_params)
    if out_sample is None:
        return None

    leaderboard.sort(key=lambda x: x[1], reverse=True)
    return OptimizeResult(
        best_params=best_params,
        in_sample=in_sample,
        out_sample=out_sample,
        tested_combos=tested,
        leaderboard=leaderboard[:10],
    )
