"""自動交易（模擬倉）：從全市場自動選股，依動量排名、做風控後下單。

規則（只做多、保守）：
- 只在「美股交易時段」動作（避免盤後送無效市價單空轉）。
- 出場：持倉跌破長期均線就賣（讓獲利的單之前能一直抱）；但若單檔相對均價暴變
  （疑似拆股/資料異常）則暫停自動賣出並告警。
- 補保護：為任何沒有在掛停損的持倉，自動補掛 GTC 停損（GTC 不會當日過期）。
- 大盤過濾：大盤跌破長期均線就只出場、不進場（避崩盤）。
- 進場：依動量排名挑強勢股，並做「同類股相關性上限 + 剛出場冷卻期 + 槓桿ETF封鎖」
  三道風控，補滿可用名額，市價買進 + GTC 初始停損。
- 每筆實際下單/出場都推播 Telegram。

用法：
    python auto_trade.py            # 跑一次
    python auto_trade.py --dry-run  # 只試算不下單
"""
from __future__ import annotations

import argparse

import pandas as pd

from config import settings
from src.stocktracker.data import alpaca_client, universe
from src.stocktracker.indicators import technical
from src.stocktracker.notify import telegram
from src.stocktracker.signals import strategy
from src.stocktracker.trade import alpaca_trader

P = settings.STRATEGY_PARAMS


def _lookback() -> int:
    """日線決策需要較長歷史（EMA50/動量），分鐘線用 60 天即可。"""
    return 250 if settings.AUTO_TRADE_TIMEFRAME in ("1Day", "1Hour") else 60


def _confirmed_trend_break(closes: pd.Series, trend_ema: pd.Series,
                           confirm_days: int) -> bool:
    """連續 confirm_days 天收盤都跌破長期均線，才視為趨勢真的走壞。

    單日跌破常是雜訊（回測：加一天確認可少被洗出場、總報酬與夏普皆升）。
    """
    if len(closes) < confirm_days or len(trend_ema) < confirm_days:
        return False
    below = closes.tail(confirm_days).values < trend_ema.tail(confirm_days).values
    return bool(below.all())


def _returns(df: pd.DataFrame, n: int = 120) -> pd.Series:
    return df["close"].pct_change().dropna().tail(n)


def _trend_exit_and_rearm(dry_run: bool, can_sell: bool = True) -> None:
    """出場 + 補掛停損。

    can_sell=False（盤後）時：只補掛 GTC 停損（可隨時掛），不送出市價賣單。
    """
    positions = alpaca_trader.list_positions()
    if not positions:
        return
    symbols = [p.symbol for p in positions]
    bars = alpaca_client.get_bars_multi(symbols, settings.AUTO_TRADE_TIMEFRAME, _lookback())
    protected = alpaca_trader.symbols_with_open_sell()

    for pos in positions:
        df = bars.get(pos.symbol)
        if df is None or df.empty:
            continue
        last_close = float(df["close"].iloc[-1])
        atr = float(technical.atr(df, P["atr_period"]).iloc[-1])
        trend_series = technical.ema(df["close"], P.get("trend_ema", 50))

        # 拆股/資料異常防呆：相對均價單步暴變 → 不自動賣，告警人工確認
        if pos.avg_entry_price > 0:
            move = abs(last_close / pos.avg_entry_price - 1)
            if move > settings.SPLIT_GUARD_PCT:
                msg = (f"⚠️ {pos.symbol} 價格相對均價變動 {move*100:.0f}%（疑似拆股/資料異常），"
                       f"已暫停自動賣出，請人工確認。")
                print(f"  {msg}")
                if not dry_run:
                    telegram.send_message(msg)
                continue

        # 趨勢轉弱 → 出場（需連續 N 天收盤跌破均線確認，避免單日雜訊洗出場；
        # 盤後不送市價賣單，留待開盤；但仍會補掛停損保護）
        if can_sell and _confirmed_trend_break(
                df["close"], trend_series, settings.EXIT_CONFIRM_DAYS):
            if dry_run:
                print(f"  [試算] 趨勢轉弱，賣出 {pos.symbol}（未實現 {pos.unrealized_pl_pct:+.1f}%）")
                continue
            try:
                alpaca_trader.close_position(pos.symbol)
                telegram.send_message(
                    f"📉 趨勢轉弱，自動賣出 <b>{pos.symbol}</b>"
                    f"（未實現 {pos.unrealized_pl_pct:+.1f}%）")
            except Exception as exc:
                print(f"  {pos.symbol} 出場失敗：{exc}")
            continue

        # 還在趨勢上但沒有保護 → 補掛 GTC 停損
        if settings.REARM_STOPS and pos.symbol not in protected and atr > 0:
            stop = round(last_close - P["atr_stop_mult"] * atr, 2)
            if stop > 0 and stop < last_close:
                if dry_run:
                    print(f"  [試算] {pos.symbol} 無保護，補掛停損 @ {stop}")
                    continue
                try:
                    alpaca_trader.place_stop_sell(pos.symbol, pos.qty, stop)
                    print(f"  {pos.symbol} 已補掛 GTC 停損 @ {stop}")
                except Exception as exc:
                    print(f"  {pos.symbol} 補掛停損失敗：{exc}")


def _market_is_up() -> bool:
    """大盤趨勢過濾：大盤收盤是否站上長期均線。"""
    if not settings.MARKET_REGIME_FILTER:
        return True
    try:
        df = alpaca_client.get_bars(settings.MARKET_SYMBOL, "1Day", lookback_days=400)
        if len(df) < settings.MARKET_MA_DAYS:
            return True
        ma = df["close"].rolling(settings.MARKET_MA_DAYS).mean().iloc[-1]
        return float(df["close"].iloc[-1]) >= float(ma)
    except Exception:
        return True


def _candidate_symbols() -> list[str]:
    if not settings.USE_FULL_UNIVERSE:
        return settings.WATCHLIST
    try:
        uni = universe.liquid_universe(
            min_price=settings.UNIVERSE_MIN_PRICE,
            min_dollar_volume=settings.UNIVERSE_MIN_DOLLAR_VOLUME)
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
        telegram.send_message("⚠️ 模擬倉資金為 $0，無法下單，請先重置帳戶資金。")
        return

    market_open = dry_run or alpaca_trader.market_is_open()

    # 決策時間窗：策略是日線邏輯，盤中的半根日 K 會亂跳（暫時跌破均線收盤又站回），
    # 造成回測裡不存在的多餘換手。買賣決策集中在收盤前的窗口執行（日 K 接近完整）；
    # 其餘班次只做「補掛停損」保護性維護。dry-run 不受限，方便隨時試算。
    in_window = True
    if settings.DECISION_WINDOW_MINUTES > 0 and not dry_run and market_open:
        mtc = alpaca_trader.minutes_to_close()
        in_window = mtc is not None and mtc <= settings.DECISION_WINDOW_MINUTES

    # 出場 + 補掛裸奔部位的停損（停損 GTC 盤後也可掛，先保護裸奔部位）
    _trend_exit_and_rearm(dry_run, can_sell=market_open and in_window)

    # 盤後：已補掛停損，但不做買賣，留待開盤
    if not market_open:
        print("🕒 非交易時段：已補掛停損保護，買賣留待開盤。")
        return

    # 盤中非決策窗口：保護做完就收工（不掃全市場、不進出場，省 API 也少雜訊交易）
    if not in_window:
        print(f"🕒 盤中維護模式：僅補掛停損。買賣決策集中在收盤前 "
              f"{settings.DECISION_WINDOW_MINUTES} 分鐘內的班次執行。")
        return

    # 大盤趨勢過濾：轉空時「減半曝險」（而非完全停手），兼顧避險與機會成本
    max_pos = settings.MAX_OPEN_POSITIONS
    pos_pct = settings.POSITION_PCT
    if not _market_is_up():
        max_pos = max(1, int(max_pos * settings.REGIME_REDUCE_FACTOR))
        pos_pct = pos_pct * settings.REGIME_REDUCE_FACTOR
        print(f"📉 大盤轉空，曝險減半：最多 {max_pos} 檔、單檔 {pos_pct:.0%}。")
        telegram.send_message("📉 大盤轉空（跌破200日均線），自動減半曝險避險。")

    held_positions = alpaca_trader.list_positions()
    held = {p.symbol for p in held_positions}
    pending = alpaca_trader.open_order_symbols()
    cooldown = alpaca_trader.recently_sold_symbols(settings.EXIT_COOLDOWN_HOURS)
    busy = held | pending | cooldown
    open_slots = max_pos - len(held)
    print(f"持有 {len(held)}｜掛單 {len(pending)}｜冷卻 {len(cooldown)}｜可用名額 {open_slots}")
    if open_slots <= 0:
        print("已達最大持倉數，這輪不進場。")
        return

    symbols = _candidate_symbols()
    print(f"掃描股票池：{len(symbols)} 檔")
    bars = alpaca_client.get_bars_multi(symbols, settings.AUTO_TRADE_TIMEFRAME, _lookback())

    # 收集買進候選（排除 busy 與槓桿ETF），算動量分數
    candidates = []
    for sym, df in bars.items():
        if df.empty or sym in busy or universe.is_blocked(sym):
            continue
        try:
            sig = strategy.latest_signal(df, P)
        except Exception:
            continue
        if sig.action != "BUY":
            continue
        mom = df["close"].pct_change(settings.MOMENTUM_WINDOW).iloc[-1]
        score = float(mom) if (settings.RANK_BY_MOMENTUM and mom == mom) else sig.confidence
        candidates.append({"sym": sym, "sig": sig, "score": score,
                           "ret": _returns(df)})

    # 依分數（動量）由高到低；次要鍵用代號確保穩定、與回測一致
    candidates.sort(key=lambda c: (c["score"], c["sym"]), reverse=True)
    print(f"買進候選 {len(candidates)} 檔，挑前 {open_slots} 檔（含相關性風控）")

    # 既有持倉的報酬序列（給相關性風控用）
    book_rets = []
    if held:
        hb = alpaca_client.get_bars_multi(list(held), settings.AUTO_TRADE_TIMEFRAME, _lookback())
        book_rets = [_returns(d) for d in hb.values() if not d.empty]

    placed = 0
    for c in candidates:
        if placed >= open_slots:
            break
        sym, sig = c["sym"], c["sig"]

        # 相關性風控：與書中任一標的相關性過高就跳過（避免集中同類股）
        too_correlated = False
        for br in book_rets:
            aligned = pd.concat([c["ret"], br], axis=1).dropna()
            if len(aligned) > 20:
                corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
                if corr is not None and corr > settings.MAX_CORRELATION:
                    too_correlated = True
                    break
        if too_correlated:
            print(f"  跳過 {sym}：與既有持倉相關性過高")
            continue

        qty = alpaca_trader.calc_qty(acc.equity, sig.price, pos_pct)
        if qty < 1:
            continue

        if dry_run:
            print(f"  [試算] 買 {sym} {qty} 股 @ {sig.price:.2f}"
                  f"（動量分 {c['score']:.3f}）停損 {sig.stop_loss:.2f}")
            placed += 1
            book_rets.append(c["ret"])
            continue

        try:
            alpaca_trader.open_long_oto_stop(sym, qty, sig.stop_loss)
        except Exception as order_exc:
            err = f"⚠️ {sym} 自動下單失敗：{order_exc}"
            print(f"  {err}"); telegram.send_message(err)
            continue

        placed += 1
        book_rets.append(c["ret"])
        telegram.send_message(
            f"🤖 自動買進 <b>{sym}</b> {qty} 股 @ ~${sig.price:.2f}\n"
            f"初始停損 ${sig.stop_loss:.2f}（GTC，讓獲利跑、跌破趨勢才賣）")
        print(f"  已下單 {sym}：{qty} 股")

    if placed == 0:
        print("這輪沒有符合條件的買進。")


def main() -> None:
    parser = argparse.ArgumentParser(description="模擬倉自動交易")
    parser.add_argument("--dry-run", action="store_true", help="只試算不實際下單")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
