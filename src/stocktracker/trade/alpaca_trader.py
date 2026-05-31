"""Alpaca 模擬倉自動下單：用括號單（bracket order）自動設停損/停利。

設計重點：
- 只做「做多（買進）」，不放空，對新手較安全、邏輯單純。
- 下「市價買進 + 括號單」：進場同時設好停利價與停損價，由券商自動執行出場。
- 只在「目前沒持有、也沒掛單」時才進場，避免每次掃描重複下單。
- 全部走 paper=True（模擬倉），不碰真錢。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from config import settings


def _client() -> TradingClient:
    if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
        raise RuntimeError("缺少 Alpaca 金鑰，無法連線模擬倉。")
    return TradingClient(settings.ALPACA_API_KEY, settings.ALPACA_SECRET_KEY,
                         paper=True)


@dataclass
class AccountInfo:
    equity: float
    cash: float
    buying_power: float


def get_account() -> AccountInfo:
    a = _client().get_account()
    return AccountInfo(equity=float(a.equity), cash=float(a.cash),
                       buying_power=float(a.buying_power))


def held_symbols() -> set[str]:
    """目前持有的標的。"""
    return {p.symbol for p in _client().get_all_positions()}


def open_order_symbols() -> set[str]:
    """目前還在掛、未成交的訂單標的（避免重複下單）。"""
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    return {o.symbol for o in _client().get_orders(filter=req)}


def calc_qty(equity: float, price: float, position_pct: float) -> int:
    """依帳戶淨值的比例算可買股數（整股、無條件捨去）。"""
    if price <= 0:
        return 0
    return math.floor((equity * position_pct) / price)


def open_long_bracket(symbol: str, qty: int, stop_loss: float,
                      take_profit: float):
    """市價買進 + 括號單（自動掛停利/停損）。"""
    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
        stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
    )
    return _client().submit_order(order_data=order)


if __name__ == "__main__":
    # 連線煙霧測試：python -m src.stocktracker.trade.alpaca_trader
    acc = get_account()
    print(f"模擬倉淨值 ${acc.equity:,.2f}｜現金 ${acc.cash:,.2f}｜"
          f"可買力 ${acc.buying_power:,.2f}")
    print("目前持有：", held_symbols() or "（無）")
    print("掛單中：", open_order_symbols() or "（無）")
