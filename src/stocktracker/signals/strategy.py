"""訊號策略引擎：綜合多個指標產生 買/賣/觀望 訊號。

設計重點：
- 每個訊號都附「信心分數」(0~100) 與「理由清單」，方便人為判斷。
- 採規則式投票：每個指標貢獻一票偏多/偏空，加總後決定方向。
- 純函式、不依賴外部 IO，方便回測重複呼叫。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..indicators import technical


@dataclass
class Signal:
    action: str  # "BUY" | "SELL" | "HOLD"
    confidence: int  # 0~100
    reasons: list[str] = field(default_factory=list)
    price: float = 0.0
    stop_loss: float = 0.0    # 建議停損價（0 表示不適用，如 HOLD）
    take_profit: float = 0.0  # 建議停利價
    atr: float = 0.0          # 當前 ATR，供顯示波動度


def _row_votes(row: pd.Series, params: dict) -> tuple[int, int, list[str], list[str]]:
    """回傳 (多方票數, 空方票數, 多方理由, 空方理由)。"""
    bull, bear = 0, 0
    bull_r, bear_r = [], []

    # 1) EMA 快慢線交叉方向
    if row["ema_fast"] > row["ema_slow"]:
        bull += 1
        bull_r.append(f"EMA{params['ema_fast']} 在 EMA{params['ema_slow']} 之上（偏多）")
    else:
        bear += 1
        bear_r.append(f"EMA{params['ema_fast']} 在 EMA{params['ema_slow']} 之下（偏空）")

    # 2) 價格相對 VWAP（當沖關鍵：站上 VWAP 偏多）
    if row["close"] > row["vwap"]:
        bull += 1
        bull_r.append("價格站上 VWAP（日內買方占優）")
    else:
        bear += 1
        bear_r.append("價格跌破 VWAP（日內賣方占優）")

    # 3) RSI 區間
    if row["rsi"] < params["rsi_oversold"]:
        bull += 1
        bull_r.append(f"RSI={row['rsi']:.0f} 進入超賣（反彈機會）")
    elif row["rsi"] > params["rsi_overbought"]:
        bear += 1
        bear_r.append(f"RSI={row['rsi']:.0f} 進入超買（回檔風險）")

    # 4) MACD 柱狀體方向
    if row["macd_hist"] > 0:
        bull += 1
        bull_r.append("MACD 柱狀體為正（動能偏多）")
    else:
        bear += 1
        bear_r.append("MACD 柱狀體為負（動能偏空）")

    return bull, bear, bull_r, bear_r


def _risk_levels(action: str, price: float, atr: float,
                 params: dict) -> tuple[float, float]:
    """依 ATR 算停損/停利價。BUY 停損在下方、停利在上方；SELL 相反。"""
    stop_dist = atr * params["atr_stop_mult"]
    target_dist = stop_dist * params["risk_reward"]
    if action == "BUY":
        return price - stop_dist, price + target_dist
    if action == "SELL":
        return price + stop_dist, price - target_dist
    return 0.0, 0.0


def evaluate_row(row: pd.Series, params: dict) -> Signal:
    bull, bear, bull_r, bear_r = _row_votes(row, params)
    total = bull + bear
    net = bull - bear

    if net >= 2:
        action, conf, reasons = "BUY", int(50 + (bull / total) * 50), bull_r
    elif net <= -2:
        action, conf, reasons = "SELL", int(50 + (bear / total) * 50), bear_r
    else:
        action, conf, reasons = "HOLD", 40, bull_r + bear_r

    # 趨勢過濾：逆著長期 EMA 方向的訊號一律改觀望，避免在盤整/逆勢中被巴
    trend = row.get("trend_ema")
    if trend is not None and params.get("use_trend_filter", True):
        if action == "BUY" and row["close"] < trend:
            action, conf = "HOLD", 40
            reasons = [f"原為買進，但價格在長期均線(EMA{int(params.get('trend_ema', 50))})之下，逆勢故觀望"]
        elif action == "SELL" and row["close"] > trend:
            action, conf = "HOLD", 40
            reasons = [f"原為賣出，但價格在長期均線(EMA{int(params.get('trend_ema', 50))})之上，逆勢故觀望"]

    price = float(row["close"])
    atr = float(row.get("atr", 0.0))
    stop, target = _risk_levels(action, price, atr, params)

    return Signal(action=action, confidence=conf, reasons=reasons,
                  price=price, stop_loss=stop, take_profit=target, atr=atr)


def latest_signal(df: pd.DataFrame, params: dict) -> Signal:
    """對最新一根 K 棒產生訊號（用於即時掃描）。"""
    enriched = technical.add_indicators(df, params)
    enriched = enriched.dropna()
    if enriched.empty:
        return Signal(action="HOLD", confidence=0, reasons=["資料不足"], price=0.0)
    return evaluate_row(enriched.iloc[-1], params)


def signal_series(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """對整段資料每根 K 棒產生方向，回傳含 indicators + position 欄位的 DataFrame。

    position: 1=做多, -1=做空, 0=空手。用於回測。
    """
    enriched = technical.add_indicators(df, params).dropna().copy()
    positions = []
    for _, row in enriched.iterrows():
        sig = evaluate_row(row, params)
        positions.append({"BUY": 1, "SELL": -1, "HOLD": 0}[sig.action])
    enriched["position"] = positions
    return enriched
