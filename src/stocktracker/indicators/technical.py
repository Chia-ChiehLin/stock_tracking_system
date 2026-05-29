"""技術指標：用 pandas/numpy 自行實作，避免第三方套件的相容性問題。

所有函式接受 OHLCV DataFrame（欄位 open/high/low/close/volume），
回傳 Series 或新增欄位後的 DataFrame。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder 平滑
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100)  # avg_loss=0 時視為極強勢


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "macd_signal": signal_line, "macd_hist": hist}
    )


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """逐日重置的 VWAP（當沖常用，每個交易日從頭累計）。"""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    day = df.index.normalize()
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = df["volume"].groupby(day).cumsum()
    return cum_pv / cum_vol.replace(0, np.nan)


def add_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """一次算齊策略需要的所有指標，回傳含新欄位的 DataFrame copy。"""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], params["ema_fast"])
    out["ema_slow"] = ema(out["close"], params["ema_slow"])
    out["trend_ema"] = ema(out["close"], params.get("trend_ema", 50))
    out["rsi"] = rsi(out["close"], params["rsi_period"])
    out["atr"] = atr(out, params["atr_period"])
    out["vwap"] = vwap(out)
    macd_df = macd(out["close"])
    out = out.join(macd_df)
    return out
