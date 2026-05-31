"""組合層級回測：模擬「掃描股票池→依信心排名挑前 N 檔→ATR 停損停利」的實際策略。

這回測的是 auto_trade.py 真正在跑的邏輯（不是單一股票），才能誠實回答
「這套全市場排名策略歷史上到底賺不賺」。

重要誠實聲明：
- 用「現在的流動性股票池」回測過去 → 有存活者偏差（survivorship bias），
  會高估報酬（因為只納入活到今天、現在還夠大的股票）。結果偏樂觀，要打折看。
- 仍無法完整模擬滑價、開盤跳空、停損與停利在同一根 K 棒誰先觸發。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..signals import strategy


@dataclass
class PortfolioResult:
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    num_trades: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    final_equity: float
    equity_curve: pd.Series
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"總報酬 {self.total_return_pct:+.1f}%｜年化 {self.cagr_pct:+.1f}%｜"
            f"最大回撤 {self.max_drawdown_pct:.1f}%｜夏普 {self.sharpe:.2f}｜"
            f"交易 {self.num_trades} 筆｜勝率 {self.win_rate_pct:.1f}%"
        )


def run_portfolio_backtest(
    bars: dict[str, pd.DataFrame],
    params: dict,
    position_pct: float = 0.10,
    max_positions: int = 5,
    starting_cash: float = 100_000.0,
    slippage_pct: float = 0.05,
    exit_mode: str = "bracket",      # "bracket"=固定停損停利；"trend"=讓獲利奔跑
    trail_atr_mult: float = 3.0,     # trend 模式：移動停損 = 高點 - N×ATR
    regime_ok: "pd.Series | None" = None,  # 大盤趨勢過濾：False 的日子不進場
    liquidate_on_regime_off: bool = False,  # 大盤轉空時是否全部出清（避崩盤）
    rank_by: str = "confidence",     # 候選排名："confidence" 或 "momentum"
    mom_window: int = 20,            # momentum 排名用的回看根數
) -> PortfolioResult:
    """以日線（或任何單一粒度）模擬組合策略。

    Args:
        bars: {symbol: OHLCV DataFrame}，index 為時間。
        exit_mode: "bracket" 固定停利停損；"trend" 跟著趨勢抱、用移動停損、
                   跌破長期均線出場（讓賺錢的單繼續跑）。
    """
    slip = slippage_pct / 100.0

    # 1) 預先算好每檔的訊號序列（含 position / confidence / atr / OHLC）
    enriched: dict[str, pd.DataFrame] = {}
    for sym, df in bars.items():
        if df is None or len(df) < 60:
            continue
        try:
            e = strategy.signal_series(df, params)
        except Exception:
            continue
        if not e.empty:
            e["mom"] = e["close"].pct_change(mom_window)
            enriched[sym] = e
    if not enriched:
        raise ValueError("沒有足夠資料可回測")

    def _regime_ok(d) -> bool:
        if regime_ok is None:
            return True
        try:
            return bool(regime_ok.asof(d))
        except Exception:
            return True

    # 2) 所有交易日（聯集、排序）
    all_dates = sorted(set().union(*[set(e.index) for e in enriched.values()]))

    cash = starting_cash
    positions: dict[str, dict] = {}     # sym -> {entry, qty, stop, target}
    equity_points: list[tuple] = []
    trade_returns: list[float] = []

    for d in all_dates:
        # 2a) 先處理出場
        for sym in list(positions.keys()):
            e = enriched[sym]
            if d not in e.index:
                continue
            row = e.loc[d]
            hi, lo, close = float(row["high"]), float(row["low"]), float(row["close"])
            pos = positions[sym]
            exit_price = None

            if exit_mode == "trend":
                # 讓獲利奔跑：移動停損（高點回落）或跌破長期均線才出場，不設停利上限
                pos["peak"] = max(pos["peak"], hi)
                trail = pos["peak"] - trail_atr_mult * pos["atr"]
                trail = max(trail, pos["stop"])  # 不低於初始停損
                trend = row.get("trend_ema")
                if lo <= trail:
                    exit_price = trail
                elif trend is not None and close < float(trend):
                    exit_price = close
            else:
                # bracket：固定停損 / 停利
                if lo <= pos["stop"]:
                    exit_price = pos["stop"]
                elif hi >= pos["target"]:
                    exit_price = pos["target"]

            if exit_price is not None:
                fill = exit_price * (1 - slip)
                cash += pos["qty"] * fill
                ret = (fill - pos["entry"]) / pos["entry"]
                trade_returns.append(ret * 100)
                del positions[sym]

        # 2b) 計算目前組合權益（現金 + 持倉市值）
        holdings_value = 0.0
        for sym, pos in positions.items():
            e = enriched[sym]
            if d in e.index:
                holdings_value += pos["qty"] * float(e.loc[d, "close"])
            else:
                holdings_value += pos["qty"] * pos["entry"]
        equity = cash + holdings_value
        equity_points.append((d, equity))

        # 大盤趨勢過濾：轉空時可選擇全部出清、且不進場
        market_up = _regime_ok(d)
        if not market_up and liquidate_on_regime_off:
            for sym in list(positions.keys()):
                e = enriched[sym]
                px = float(e.loc[d, "close"]) if d in e.index else positions[sym]["entry"]
                fill = px * (1 - slip)
                cash += positions[sym]["qty"] * fill
                trade_returns.append(((fill - positions[sym]["entry"])
                                      / positions[sym]["entry"]) * 100)
                del positions[sym]

        # 2c) 進場：補滿可用名額，挑當日買進訊號、依排名取前幾名
        slots = max_positions - len(positions)
        if slots <= 0 or not market_up:
            continue
        candidates = []
        for sym, e in enriched.items():
            if sym in positions or d not in e.index:
                continue
            row = e.loc[d]
            if int(row["position"]) == 1:   # 買進訊號
                mom = row.get("mom")
                rank_val = (float(mom) if rank_by == "momentum" and mom == mom
                            else float(row["confidence"]))
                candidates.append((sym, rank_val,
                                   float(row["close"]), float(row["atr"])))
        candidates.sort(key=lambda x: x[1], reverse=True)

        for sym, _rank, price, atr in candidates[:slots]:
            if price <= 0 or atr <= 0:
                continue
            alloc = equity * position_pct
            qty = int(alloc // price)
            if qty < 1 or qty * price > cash:
                continue
            entry = price * (1 + slip)
            stop, target = strategy._risk_levels("BUY", entry, atr, params)
            cash -= qty * entry
            positions[sym] = {"entry": entry, "qty": qty, "stop": stop,
                              "target": target, "atr": atr, "peak": entry}

    # 3) 收尾：以最後價格平倉所有持倉
    last_d = all_dates[-1]
    for sym, pos in positions.items():
        e = enriched[sym]
        px = float(e.loc[last_d, "close"]) if last_d in e.index else pos["entry"]
        cash += pos["qty"] * px
        trade_returns.append(((px - pos["entry"]) / pos["entry"]) * 100)

    return _summarize(equity_points, trade_returns, starting_cash, all_dates)


def _summarize(equity_points, trade_returns, starting_cash, all_dates):
    curve = pd.Series([e for _, e in equity_points],
                      index=pd.to_datetime([t for t, _ in equity_points]))
    final = float(curve.iloc[-1]) if len(curve) else starting_cash
    total_ret = (final / starting_cash - 1) * 100

    # 年化（用實際天數）
    days = max((all_dates[-1] - all_dates[0]).days, 1)
    years = days / 365.25
    cagr = ((final / starting_cash) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    # 最大回撤
    running_max = curve.cummax()
    dd = ((curve - running_max) / running_max).min() * 100 if len(curve) else 0.0

    # 夏普（用每根權益變化）
    rets = curve.pct_change().dropna()
    sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0

    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r <= 0]
    n = len(trade_returns)
    win_rate = (len(wins) / n * 100) if n else 0.0

    return PortfolioResult(
        total_return_pct=total_ret, cagr_pct=cagr, max_drawdown_pct=float(dd),
        sharpe=float(sharpe), num_trades=n, win_rate_pct=win_rate,
        avg_win_pct=float(np.mean(wins)) if wins else 0.0,
        avg_loss_pct=float(np.mean(losses)) if losses else 0.0,
        final_equity=final, equity_curve=curve,
    )
