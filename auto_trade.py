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
from src.stocktracker.indicators import technical
from src.stocktracker.notify import telegram
from src.stocktracker.signals import strategy
from src.stocktracker.trade import alpaca_trader


def _trend_exit_pass(dry_run: bool) -> None:
    """對現有持倉做趨勢出場：價格跌破長期均線就賣出（讓獲利的單之前能一直抱）。"""
    positions = alpaca_trader.list_positions()
    if not positions:
        return
    symbols = [s for s, _ in positions]
    bars = alpaca_client.get_bars_multi(
        symbols, timeframe=settings.AUTO_TRADE_TIMEFRAME, lookback_days=60)
    for sym, qty in positions:
        df = bars.get(sym)
        if df is None or df.empty:
            continue
        trend = technical.ema(df["close"], settings.STRATEGY_PARAMS.get("trend_ema", 50))
        last_close = float(df["close"].iloc[-1])
        last_trend = float(trend.iloc[-1])
        if last_close < last_trend:   # 趨勢轉弱 → 出場
            if dry_run:
                print(f"  [試算] 趨勢轉弱，賣出 {sym} {qty} 股 @ {last_close:.2f}")
                continue
            try:
                alpaca_trader.close_position(sym)
                telegram.send_message(
                    f"📉 趨勢轉弱，自動賣出 <b>{sym}</b> {qty} 股 @ ~${last_close:.2f}")
            except Exception as exc:
                print(f"  {sym} 出場失敗：{exc}")


def _market_is_up() -> bool:
    """大盤趨勢過濾：大盤指標收盤是否站上長期均線（站上才允許新進場）。"""
    if not settings.MARKET_REGIME_FILTER:
        return True
    try:
        df = alpaca_client.get_bars(settings.MARKET_SYMBOL, "1Day", lookback_days=400)
        if len(df) < settings.MARKET_MA_DAYS:
            return True  # 資料不足就不擋
        ma = df["close"].rolling(settings.MARKET_MA_DAYS).mean().iloc[-1]
        return float(df["close"].iloc[-1]) >= float(ma)
    except Exception:
        return True


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

    # 先做趨勢出場（賣掉趨勢轉弱的持倉，騰出名額）
    _trend_exit_pass(dry_run)

    # 大盤趨勢過濾：大盤轉空就只出場、不進場（避開崩盤）
    if not _market_is_up():
        print("📉 大盤跌破長期均線，暫停所有新進場（只保留出場）。")
        telegram.send_message("📉 大盤轉空（跌破200日均線），系統暫停新進場以避險。")
        return

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
                  f" 初始停損 {sig.stop_loss:.2f}（讓獲利跑）")
            placed += 1
            continue

        try:
            alpaca_trader.open_long_oto_stop(sym, qty, sig.stop_loss)
        except Exception as order_exc:
            err = f"⚠️ {sym} 自動下單失敗：{order_exc}"
            print(f"  {err}"); telegram.send_message(err)
            continue

        placed += 1
        note = (f"🤖 自動買進 <b>{sym}</b> {qty} 股 @ ~${sig.price:.2f}\n"
                f"初始停損 ${sig.stop_loss:.2f}（之後讓獲利跑，跌破趨勢才賣）\n"
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
