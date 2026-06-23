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
    StopOrderRequest,
    TakeProfitRequest,
)

from config import settings


def market_is_open() -> bool:
    """美股是否在交易時段（避免盤後送出無效市價單造成重試空轉）。"""
    try:
        return bool(_client().get_clock().is_open)
    except Exception:
        return True  # 查不到就不阻擋


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


def open_long_oto_stop(symbol: str, qty: int, stop_loss: float):
    """市價買進 + 只掛初始停損（GTC，不會當日過期；不設停利上限，讓獲利繼續跑）。

    出場改由排程的「趨勢出場」邏輯處理（跌破長期均線才賣）。
    """
    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,      # GTC：停損子單不會當日失效
        order_class=OrderClass.OTO,
        stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
    )
    return _client().submit_order(order_data=order)


def place_stop_sell(symbol: str, qty: int, stop_price: float):
    """為現有持倉補掛一張 GTC 停損賣單（救回沒有保護的裸奔部位）。"""
    order = StopOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
        stop_price=round(stop_price, 2),
    )
    return _client().submit_order(order_data=order)


@dataclass
class Position:
    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float
    unrealized_pl_pct: float


def list_positions() -> list[Position]:
    """回傳持倉明細（用 Alpaca 已分割還原的 qty/均價，不用自己記的舊值）。"""
    out = []
    for p in _client().get_all_positions():
        try:
            out.append(Position(
                symbol=p.symbol,
                qty=int(float(p.qty)),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                unrealized_pl_pct=float(p.unrealized_plpc) * 100,
            ))
        except (ValueError, TypeError):
            continue
    return out


def symbols_with_open_sell() -> set[str]:
    """目前有「在掛賣單（停損）」的標的，用來判斷哪些持倉有保護。"""
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    return {o.symbol for o in _client().get_orders(filter=req)
            if str(o.side).endswith("SELL")}


def recently_sold_symbols(within_hours: int = 24) -> set[str]:
    """近 N 小時內有賣出成交的標的（用於『剛出場冷卻期』，避免追高買回）。"""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=200)
    out = set()
    for o in _client().get_orders(filter=req):
        if (str(o.side).endswith("SELL") and o.filled_at
                and o.filled_at >= cutoff):
            out.add(o.symbol)
    return out


def cancel_symbol_orders(symbol: str) -> None:
    """取消某標的所有未成交掛單（出場前先清掉殘留的停損單）。"""
    c = _client()
    for o in c.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)):
        if o.symbol == symbol:
            try:
                c.cancel_order_by_id(o.id)
            except Exception:
                pass


def close_position(symbol: str):
    """平倉某標的全部股數（先取消殘留掛單，再由 Alpaca 全平，不用自記股數）。"""
    cancel_symbol_orders(symbol)
    return _client().close_position(symbol)


if __name__ == "__main__":
    # 連線煙霧測試：python -m src.stocktracker.trade.alpaca_trader
    acc = get_account()
    print(f"模擬倉淨值 ${acc.equity:,.2f}｜現金 ${acc.cash:,.2f}｜"
          f"可買力 ${acc.buying_power:,.2f}")
    print("目前持有：", held_symbols() or "（無）")
    print("掛單中：", open_order_symbols() or "（無）")
