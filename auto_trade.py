"""自動交易（模擬倉）：依訊號自動在 Alpaca paper 帳戶下單。

規則（只做多、保守）：
- 掃描關注清單，對每檔用 1 小時線算最新訊號。
- 訊號為「買進」且「目前沒持有、也沒掛單」、且未超過最大持倉數 → 市價買進 + 括號單
  （同時掛好停利、停損，由券商自動出場）。
- 其他情況（賣出/觀望、或已持有）→ 不動作，交給括號單自動管理出場。
- 每筆實際下單都推播 Telegram 通知。

用法：
    python auto_trade.py            # 跑一次
    python auto_trade.py --dry-run  # 只試算不下單
"""
from __future__ import annotations

import argparse

from config import settings
from src.stocktracker.data import alpaca_client
from src.stocktracker.notify import telegram
from src.stocktracker.signals import strategy
from src.stocktracker.trade import alpaca_trader


def run(dry_run: bool = False) -> None:
    try:
        acc = alpaca_trader.get_account()
    except Exception as exc:
        print(f"無法連線模擬倉：{exc}")
        return

    print(f"模擬倉淨值 ${acc.equity:,.2f}｜可買力 ${acc.buying_power:,.2f}")
    if acc.equity <= 0:
        msg = "⚠️ 模擬倉資金為 $0，無法下單。請先到 Alpaca 重置帳戶資金。"
        print(msg)
        telegram.send_message(msg)
        return

    held = alpaca_trader.held_symbols()
    pending = alpaca_trader.open_order_symbols()
    busy = held | pending          # 已持有或掛單中的標的
    open_slots = settings.MAX_OPEN_POSITIONS - len(held)

    for symbol in settings.WATCHLIST:
        try:
            df = alpaca_client.get_bars(
                symbol, settings.AUTO_TRADE_TIMEFRAME, lookback_days=60)
            if df.empty:
                print(f"{symbol}: 無資料"); continue

            sig = strategy.latest_signal(df, settings.STRATEGY_PARAMS)
            print(f"{symbol}: {sig.action} (信心 {sig.confidence}) @ {sig.price:.2f}")

            if sig.action != "BUY":
                continue
            if symbol in busy:
                print(f"  ↳ 已持有或掛單中，略過"); continue
            if open_slots <= 0:
                print(f"  ↳ 已達最大持倉數 {settings.MAX_OPEN_POSITIONS}，略過"); continue

            qty = alpaca_trader.calc_qty(acc.equity, sig.price, settings.POSITION_PCT)
            if qty < 1:
                print(f"  ↳ 資金不足買 1 股，略過"); continue

            if dry_run:
                print(f"  ↳ [試算] 會買 {qty} 股，停損 {sig.stop_loss:.2f}、"
                      f"停利 {sig.take_profit:.2f}")
                continue

            try:
                alpaca_trader.open_long_bracket(
                    symbol, qty, sig.stop_loss, sig.take_profit)
            except Exception as order_exc:
                # 下單失敗也要讓使用者知道（例如市場休市、資金不足）
                err = (f"⚠️ {symbol} 自動下單失敗：{order_exc}")
                print(f"  ↳ {err}")
                telegram.send_message(err)
                continue

            open_slots -= 1
            busy.add(symbol)
            note = (f"🤖 自動買進 <b>{symbol}</b> {qty} 股 @ ~${sig.price:.2f}\n"
                    f"停損 ${sig.stop_loss:.2f}｜停利 ${sig.take_profit:.2f}\n"
                    f"（模擬倉・信心 {sig.confidence}/100）")
            print(f"  ↳ 已下單：{qty} 股")
            telegram.send_message(note)
        except Exception as exc:
            print(f"{symbol}: 處理錯誤 - {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="模擬倉自動交易")
    parser.add_argument("--dry-run", action="store_true", help="只試算不實際下單")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
