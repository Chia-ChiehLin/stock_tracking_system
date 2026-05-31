"""共用測試資料：合成一段有趨勢 + 雜訊的 OHLCV。"""
import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def ohlcv():
    n = 400
    idx = pd.date_range("2025-01-01 09:30", periods=n, freq="1h")
    base = 100 + np.cumsum(np.sin(np.arange(n) / 25) * 0.5)
    close = base + np.cumsum(np.full(n, 0.01))  # 緩升趨勢
    high = close + 0.3
    low = close - 0.3
    openp = close - 0.05
    vol = np.full(n, 10000.0)
    return pd.DataFrame(
        {"open": openp, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


@pytest.fixture
def params():
    from config import settings
    return dict(settings.STRATEGY_PARAMS)
