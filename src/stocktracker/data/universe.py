"""股票池：從全市場美股自動篩出「流動性夠、值得交易」的標的。

流程：
1. 取得所有可交易的美股（NYSE / NASDAQ 等主要交易所）。
2. 用最近的日線過濾：股價 >= 最低價、每日成交金額 >= 門檻（剔除雞蛋水餃股、
   成交量過低、資料不足的標的——這些只會拖累總收益）。

回傳通過篩選的代號清單，供自動交易/掃描使用。
"""
from __future__ import annotations

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from config import settings
from . import alpaca_client

# 主要交易所（排除 OTC 等流動性差的場外市場）
_MAJOR_EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS"}

# 槓桿型/反向型 ETF 的名稱關鍵字（這類會放大波動、長期耗損、且賭市場反向，
# 自動交易絕不該碰，例如 SQQQ、SOXL、SPDN、TQQQ）
_LEVERAGED_KEYWORDS = (
    "ultrapro", "ultrashort", "ultra ", "leveraged", "inverse",
    "bear", "bull", "2x", "3x", "1.5x", "-1x", "short",
)


def _is_leveraged_or_inverse(name: str | None) -> bool:
    if not name:
        return False
    low = name.lower()
    return any(k in low for k in _LEVERAGED_KEYWORDS)

# 流動性門檻
MIN_PRICE = 5.0                 # 股價至少 $5（避開水餃股）
MIN_DOLLAR_VOLUME = 30_000_000  # 每日成交金額至少 $30M（夠你進出不影響價格）


def all_tradable_equities() -> list[str]:
    """所有可交易的美股代號（主要交易所、無特殊符號）。"""
    client = TradingClient(settings.ALPACA_API_KEY, settings.ALPACA_SECRET_KEY,
                           paper=True)
    req = GetAssetsRequest(status=AssetStatus.ACTIVE,
                           asset_class=AssetClass.US_EQUITY)
    assets = client.get_all_assets(req)
    out = []
    for a in assets:
        if not a.tradable:
            continue
        if a.exchange and str(a.exchange).split(".")[-1] not in _MAJOR_EXCHANGES:
            continue
        # 排除含特殊字元（權證、優先股等）的代號，留乾淨的普通股
        if not a.symbol.isalpha():
            continue
        # 排除槓桿型/反向型 ETF（賭反向、長期耗損，自動交易不該碰）
        if _is_leveraged_or_inverse(getattr(a, "name", None)):
            continue
        out.append(a.symbol)
    return out


def liquid_universe(
    min_price: float = MIN_PRICE,
    min_dollar_volume: float = MIN_DOLLAR_VOLUME,
    max_symbols: int | None = None,
) -> list[str]:
    """回傳通過流動性篩選的代號（依成交金額由大到小排序）。"""
    symbols = all_tradable_equities()
    # 抓最近數日的日線，算近期平均成交金額
    bars = alpaca_client.get_bars_multi(symbols, timeframe="1Day", lookback_days=10)

    scored: list[tuple[str, float]] = []
    for sym, df in bars.items():
        if df.empty or len(df) < 3:
            continue
        last_close = float(df["close"].iloc[-1])
        if last_close < min_price:
            continue
        avg_dollar_vol = float((df["close"] * df["volume"]).tail(5).mean())
        if avg_dollar_vol < min_dollar_volume:
            continue
        scored.append((sym, avg_dollar_vol))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [s for s, _ in scored]
    return result[:max_symbols] if max_symbols else result


if __name__ == "__main__":
    # python -m src.stocktracker.data.universe
    uni = liquid_universe()
    print(f"通過流動性篩選：{len(uni)} 檔")
    print("成交金額前 20 大：", uni[:20])
