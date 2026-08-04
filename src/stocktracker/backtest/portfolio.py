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
    rank_by: str = "confidence",     # 候選排名："confidence"/"momentum"/"risk_momentum"
    mom_window: int = 20,            # momentum 排名用的回看根數
    trail: bool = True,              # trend 模式：True=移動停損；False=只用初始停損（=目前實盤行為）
    exit_confirm_days: int = 1,      # 趨勢出場需「連續 N 天收盤跌破長期均線」才賣（抗雜訊）
    min_hold_days: int = 0,          # 進場後至少持有 N 天才允許趨勢出場（停損不受限）
    cooldown_days: int = 0,          # 出場後 N 天內不得重新買回同一檔
    dd_breaker_pct: float = 0.0,     # 組合權益從高點回撤超過此 % 時暫停新進場（0=停用）
    enriched: "dict[str, pd.DataFrame] | None" = None,  # 預算好的訊號序列（加速參數掃描用）
) -> PortfolioResult:
    """以日線（或任何單一粒度）模擬組合策略。

    Args:
        bars: {symbol: OHLCV DataFrame}，index 為時間。
        exit_mode: "bracket" 固定停利停損；"trend" 跟著趨勢抱、用移動停損、
                   跌破長期均線出場（讓賺錢的單繼續跑）。
        enriched: 若提供（{symbol: strategy.signal_series() 結果}），跳過訊號計算，
                  只重算排名欄位——參數掃描時可大幅加速。注意：訊號參數（EMA/RSI）
                  必須與產生 enriched 時一致，否則結果無效。
    """
    slip = slippage_pct / 100.0

    # 1) 預先算好每檔的訊號序列（含 position / confidence / atr / OHLC）
    if enriched is None:
        enriched = {}
        for sym, df in bars.items():
            if df is None or len(df) < 60:
                continue
            try:
                e = strategy.signal_series(df, params)
            except Exception:
                continue
            if not e.empty:
                enriched[sym] = e
    else:
        enriched = {sym: e.copy() for sym, e in enriched.items()
                    if e is not None and not e.empty}
    if not enriched:
        raise ValueError("沒有足夠資料可回測")
    for e in enriched.values():
        e["mom"] = e["close"].pct_change(mom_window)
        if rank_by == "risk_momentum":
            e["mom_vol"] = e["close"].pct_change().rolling(mom_window).std()

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
    positions: dict[str, dict] = {}     # sym -> {entry, qty, stop, target, ...}
    equity_points: list[tuple] = []
    trade_returns: list[float] = []
    cooldown_until: dict[str, int] = {}  # sym -> 最早可重新買回的日期索引

    for i, d in enumerate(all_dates):
        # 2a) 先處理出場
        for sym in list(positions.keys()):
            e = enriched[sym]
            if d not in e.index:
                continue
            row = e.loc[d]
            hi, lo, close = float(row["high"]), float(row["low"]), float(row["close"])
            openp = float(row.get("open", close))
            pos = positions[sym]
            pos["held_days"] += 1
            exit_price = None

            if exit_mode == "trend":
                # 讓獲利奔跑：停損（移動或初始）或跌破長期均線才出場，不設停利上限。
                # 停損水位只能用「昨天為止」的高點算：當天低點出現時、當天高點
                # 可能還沒發生，先用當天高點墊高停損再回測當天低點是偷看未來。
                if trail:
                    stop_level = max(pos["peak"] - trail_atr_mult * pos["atr"],
                                     pos["stop"])
                else:
                    stop_level = pos["stop"]  # 不上移（=目前實盤只掛初始 GTC 停損）
                trend = row.get("trend_ema")
                if lo <= stop_level:
                    # 跳空跌破停損：停損單成為市價單，實際約在開盤價成交
                    exit_price = min(stop_level, openp)
                else:
                    # 趨勢出場：連續 exit_confirm_days 天收盤跌破均線才賣（抗雜訊）
                    if trend is not None and close < float(trend):
                        pos["below_trend"] += 1
                    else:
                        pos["below_trend"] = 0
                    if (pos["below_trend"] >= exit_confirm_days
                            and pos["held_days"] >= min_hold_days):
                        exit_price = close
                pos["peak"] = max(pos["peak"], hi)  # 出場判定「之後」才更新高點
            else:
                # bracket：固定停損 / 停利（跳空穿越時以開盤價成交）
                if lo <= pos["stop"]:
                    exit_price = min(pos["stop"], openp)
                elif hi >= pos["target"]:
                    exit_price = max(pos["target"], openp)

            if exit_price is not None:
                fill = exit_price * (1 - slip)
                cash += pos["qty"] * fill
                ret = (fill - pos["entry"]) / pos["entry"]
                trade_returns.append(ret * 100)
                del positions[sym]
                if cooldown_days:
                    # 出場當天 + 之後 N 天都不得買回（i+N+1 才解禁）
                    cooldown_until[sym] = i + cooldown_days + 1

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
                if cooldown_days:
                    cooldown_until[sym] = i + cooldown_days + 1

        # 回撤斷路器：組合權益從「近半年高點」回撤過深時，暫停新進場（既有持倉照常出場）。
        # 用滾動高點而非歷史最高點：歷史最高點永不下修，崩盤後空手時回撤永遠超標，
        # 會鎖死永遠不再進場；滾動高點會隨時間自我復位。
        if dd_breaker_pct > 0:
            recent_peak = max(v for _, v in equity_points[-126:])
            drawdown = (recent_peak - equity) / recent_peak * 100
            if drawdown > dd_breaker_pct:
                continue

        # 2c) 進場：補滿可用名額，挑當日買進訊號、依排名取前幾名
        slots = max_positions - len(positions)
        if slots <= 0 or not market_up:
            continue
        candidates = []
        for sym, e in enriched.items():
            if sym in positions or d not in e.index:
                continue
            if cooldown_days and i < cooldown_until.get(sym, 0):
                continue  # 剛出場冷卻中，避免追高買回
            row = e.loc[d]
            if int(row["position"]) == 1:   # 買進訊號
                mom = row.get("mom")
                if rank_by == "momentum":
                    rank_val = float(mom) if mom == mom else float(row["confidence"])
                elif rank_by == "risk_momentum":
                    # 風險調整動量：漲幅 / 波動度（同樣的漲、越平穩越好）
                    vol = row.get("mom_vol")
                    if mom == mom and vol == vol and float(vol) > 0:
                        rank_val = float(mom) / float(vol)
                    else:
                        continue  # 資料不足以估波動就不買
                else:
                    rank_val = float(row["confidence"])
                candidates.append((sym, rank_val,
                                   float(row["close"]), float(row["atr"])))
        # 次要鍵用代號，排序穩定且與實盤一致
        candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)

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
                              "target": target, "atr": atr, "peak": entry,
                              "held_days": 0, "below_trend": 0}

    # 3) 收尾：以最後價格平倉所有持倉（同樣計滑價，別偏袒抱到最後的變體）
    last_d = all_dates[-1]
    for sym, pos in positions.items():
        e = enriched[sym]
        px = float(e.loc[last_d, "close"]) if last_d in e.index else pos["entry"]
        fill = px * (1 - slip)
        cash += pos["qty"] * fill
        trade_returns.append(((fill - pos["entry"]) / pos["entry"]) * 100)

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
