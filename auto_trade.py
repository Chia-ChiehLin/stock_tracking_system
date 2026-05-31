"""自動交易（模擬倉）：從全市場自動選股，依訊號強度排名後下單。

規則（只做多、保守）：
- 取得股票池（預設：全市場流動性過濾後 ~數百檔；可改回只看關注清單）。
- 批次抓 1 小時線，對每檔算最新訊號，收集「買進」訊號。
- 依信心分數由高到低排名，挑最強的幾檔（補滿可用持倉名額）。
- 只在「沒持有、沒掛單、未達最大持倉數」時進場，市價買進 + 括號單
  （自動掛停利/停損，由券商管理出場）。
- 每筆實際下單都推播 Telegram。

用法：
    python auto_trade.py            # 跑一次
    python auto_trade.py --dry-run  # 只試算不下單
"""
from __future__ import annotations

import argparse

from config import settings
from src.stocktracker.data import alpaca_client, universe
from src.stocktracker.notify import telegram
from src.stocktracker.signals import strategy
from src.stocktracker.trade import alpaca_trader


def _candidate_symbols() -> list[str]:
    """要掃描的股票池。"""
    if not settings.USE_FULL_UNIVERSE:
        return settings.WATCHLIST
    try:
        uni = universe.liquid_universe(
            min_price=settings.UNIVERSE_MIN_PRICE,
            min_dollar_volume=settings.UNIVERSE_MIN_DOLLAR_VOLUME,
        )
        if uni:
            return uni
    except Exception as exc:
        print(f"建立股票池失敗，改用關注清單：{exc}")
    return settings.WATCHLIST


def run(dry_run: bool = False) -> None:
    try:
        acc = alpaca_trader.get_account()
    except Exception as exc:
        print(f"無法連線模擬倉：{exc}")
        return

    print(f"模擬倉淨值 ${acc.equity:,.2f}｜可買力 ${acc.buying_power:,.2f}")
    if acc.equity <= 0:
        msg = "⚠️ 模擬倉資金為 $0，無法下單。請先到 Alpaca 重置帳戶資金。"
        print(msg); telegram.send_message(msg); return

    held = alpaca_trader.held_symbols()
    pending = alpaca_trader.open_order_symbols()
    busy = held | pending
    open_slots = settings.MAX_OPEN_POSITIONS - len(held)
    print(f"目前持有 {len(held)} 檔、掛單 {len(pending)} 檔、可用名額 {open_slots}")

    symbols = _candidate_symbols()
    print(f"掃描股票池：{len(symbols)} 檔")

    # 批次抓 1 小時線，逐檔算訊號，收集買進候選
    bars = alpaca_client.get_bars_multi(
        symbols, timeframe=settings.AUTO_TRADE_TIMEFRAME, lookback_days=60)
    candidates = []
    for sym, df in bars.items():
        if df.empty:
            continue
        try:
            sig = strategy.latest_signal(df, settings.STRATEGY_PARAMS)
        except Exception:
            continue
        if sig.action == "BUY" and sym not in busy:
            candidates.append((sym, sig))

    # 依信心分數排名，最強的優先
    candidates.sort(key=lambda x: x[1].confidence, reverse=True)
    print(f"買進候選 {len(candidates)} 檔，將挑前 {max(open_slots,0)} 檔下單")

    if open_slots <= 0:
        print("已達最大持倉數，這輪不進場。")
        return

    placed = 0
    for sym, sig in candidates:
        if placed >= open_slots:
            break
        qty = alpaca_trader.calc_qty(acc.equity, sig.price, settings.POSITION_PCT)
        if qty < 1:
            continue

        if dry_run:
            print(f"  [試算] 買 {sym} {qty} 股 @ {sig.price:.2f}（信心 {sig.confidence}）"
                  f" 停損 {sig.stop_loss:.2f} 停利 {sig.take_profit:.2f}")
            placed += 1
            continue

        try:
            alpaca_trader.open_long_bracket(sym, qty, sig.stop_loss, sig.take_profit)
        except Exception as order_exc:
            err = f"⚠️ {sym} 自動下單失敗：{order_exc}"
            print(f"  {err}"); telegram.send_message(err)
            continue

        placed += 1
        note = (f"🤖 自動買進 <b>{sym}</b> {qty} 股 @ ~${sig.price:.2f}\n"
                f"停損 ${sig.stop_loss:.2f}｜停利 ${sig.take_profit:.2f}\n"
                f"（模擬倉・信心 {sig.confidence}/100）")
        print(f"  已下單 {sym}：{qty} 股")
        telegram.send_message(note)

    if placed == 0:
        print("這輪沒有符合條件的買進。")


def main() -> None:
    parser = argparse.ArgumentParser(description="模擬倉自動交易")
    parser.add_argument("--dry-run", action="store_true", help="只試算不實際下單")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
