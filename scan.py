"""盤中掃描器：對關注清單跑訊號，出現 BUY/SELL 就推播 Telegram。

用法：
    python scan.py             # 掃一次
    python scan.py --loop 300  # 每 300 秒掃一次（盤中持續執行）

可搭配 cron 或背景執行。
"""
from __future__ import annotations

import argparse
import time

from config import settings
from src.stocktracker.data import alpaca_client
from src.stocktracker.notify import telegram
from src.stocktracker.signals import strategy


def scan_once(notify: bool = True, timeframe: str | None = None,
              lookback_days: int = 5) -> None:
    timeframe = timeframe or settings.DEFAULT_TIMEFRAME
    for symbol in settings.WATCHLIST:
        try:
            df = alpaca_client.get_bars(symbol, timeframe, lookback_days=lookback_days)
            if df.empty:
                print(f"{symbol}: 無資料")
                continue
            sig = strategy.latest_signal(df, settings.STRATEGY_PARAMS)
            print(f"{symbol}: {sig.action} (信心 {sig.confidence}) @ {sig.price:.2f}")
            if notify and sig.action in ("BUY", "SELL"):
                msg = telegram.format_signal(
                    symbol, sig.action, sig.confidence, sig.price, sig.reasons,
                    sig.stop_loss, sig.take_profit
                )
                telegram.send_message(msg)
        except Exception as exc:  # 單一標的失敗不應中斷整輪掃描
            print(f"{symbol}: 掃描錯誤 - {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="盤中訊號掃描器")
    parser.add_argument("--loop", type=int, default=0,
                        help="每 N 秒掃描一次；0 表示只掃一次")
    parser.add_argument("--timeframe", default=None,
                        help="K 棒粒度，如 5Min/1Hour/1Day；預設用 settings 的值")
    parser.add_argument("--no-notify", action="store_true", help="不推播，只印出")
    args = parser.parse_args()

    # 1Hour/1Day 需要較長回看天數才有足夠 K 棒算指標
    lookback = {"1Hour": 60, "1Day": 365}.get(args.timeframe, 5)

    if args.loop > 0:
        print(f"進入循環掃描，每 {args.loop} 秒一次（Ctrl+C 結束）...")
        while True:
            scan_once(notify=not args.no_notify, timeframe=args.timeframe,
                      lookback_days=lookback)
            time.sleep(args.loop)
    else:
        scan_once(notify=not args.no_notify, timeframe=args.timeframe,
                  lookback_days=lookback)


if __name__ == "__main__":
    main()
