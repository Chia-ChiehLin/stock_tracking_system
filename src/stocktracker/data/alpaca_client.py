"""Alpaca 美股資料抓取：歷史與近即時分鐘線。

使用免費的 IEX feed（alpaca-py StockHistoricalDataClient）。
回傳統一格式的 pandas DataFrame，index 為 timestamp，欄位為
open / high / low / close / volume。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from config import settings

_TIMEFRAME_MAP = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "1Day": TimeFrame(1, TimeFrameUnit.Day),
}


def _client() -> StockHistoricalDataClient:
    if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
        raise RuntimeError(
            "缺少 Alpaca 金鑰。請複製 .env.example 為 .env 並填入 "
            "ALPACA_API_KEY / ALPACA_SECRET_KEY。"
        )
    return StockHistoricalDataClient(
        settings.ALPACA_API_KEY, settings.ALPACA_SECRET_KEY
    )


def get_bars(
    symbol: str,
    timeframe: str = "5Min",
    lookback_days: int = 5,
) -> pd.DataFrame:
    """抓取單一標的的分鐘線/日線。

    Args:
        symbol: 美股代號，例如 "AAPL"。
        timeframe: _TIMEFRAME_MAP 的鍵，預設 "5Min"。
        lookback_days: 往回抓幾天的資料。
    """
    if timeframe not in _TIMEFRAME_MAP:
        raise ValueError(f"不支援的 timeframe: {timeframe}")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=_TIMEFRAME_MAP[timeframe],
        start=start,
        end=end,
        feed=DataFeed.IEX,  # 免費帳戶用 IEX feed（SIP 需付費訂閱）
        adjustment=Adjustment.ALL,  # 分割/股息還原，避免拆股當成崩盤
    )
    bars = _client().get_stock_bars(request)
    df = bars.df

    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    # 多標的時 df 是 MultiIndex (symbol, timestamp)，取出單一標的
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")

    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index)
    return df


def get_bars_multi(
    symbols: list[str],
    timeframe: str = "1Hour",
    lookback_days: int = 60,
    batch_size: int = 200,
) -> dict[str, pd.DataFrame]:
    """一次抓多檔的 K 棒，回傳 {symbol: DataFrame}。

    用分批請求避免單一請求過大；缺資料的標的不會出現在回傳的 dict 裡。
    """
    if timeframe not in _TIMEFRAME_MAP:
        raise ValueError(f"不支援的 timeframe: {timeframe}")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    client = _client()
    out: dict[str, pd.DataFrame] = {}

    for i in range(0, len(symbols), batch_size):
        chunk = symbols[i:i + batch_size]
        request = StockBarsRequest(
            symbol_or_symbols=chunk,
            timeframe=_TIMEFRAME_MAP[timeframe],
            start=start,
            end=end,
            feed=DataFeed.IEX,
            adjustment=Adjustment.ALL,  # 分割/股息還原
        )
        try:
            df = client.get_stock_bars(request).df
        except Exception:
            continue
        if df.empty:
            continue
        # MultiIndex (symbol, timestamp) → 拆成各標的
        for sym in chunk:
            try:
                sub = df.xs(sym, level="symbol")
            except (KeyError, Exception):
                continue
            if sub.empty:
                continue
            sub = sub[["open", "high", "low", "close", "volume"]].copy()
            sub.index = pd.to_datetime(sub.index)
            out[sym] = sub
    return out


if __name__ == "__main__":
    # 簡單煙霧測試：python -m src.stocktracker.data.alpaca_client
    out = get_bars("AAPL", "5Min", lookback_days=2)
    print(out.tail())
    print(f"\n共 {len(out)} 根 K 棒")
