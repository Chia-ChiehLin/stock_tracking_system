"""每日盤後總結 + 誠實計分板：模擬倉損益、持倉，並和「同期只買大盤」比較。

用法：python daily_summary.py
"""
from __future__ import annotations

from alpaca.trading.client import TradingClient

from config import settings
from src.stocktracker.data import alpaca_client
from src.stocktracker.notify import telegram


def _spy_return_since(created_at) -> float | None:
    """帳戶開始至今，若『同期只買 SPY 放著』的報酬（%）。"""
    try:
        days = 400
        try:
            from datetime import datetime, timezone
            days = max((datetime.now(timezone.utc) - created_at).days + 5, 10)
        except Exception:
            pass
        df = alpaca_client.get_bars(settings.MARKET_SYMBOL, "1Day",
                                    lookback_days=min(days, 700))
        if df.empty:
            return None
        return (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    except Exception:
        return None


def main() -> None:
    c = TradingClient(settings.ALPACA_API_KEY, settings.ALPACA_SECRET_KEY,
                      paper=True)
    a = c.get_account()
    equity = float(a.equity)
    last_equity = float(a.last_equity) if a.last_equity else equity
    day_chg = equity - last_equity
    day_pct = (day_chg / last_equity * 100) if last_equity else 0.0
    total_pct = (equity / 100_000 - 1) * 100

    positions = c.get_all_positions()
    lines = [f"  • {p.symbol} {p.qty} 股，未實現 "
             f"{(float(p.unrealized_plpc) * 100 if p.unrealized_plpc else 0):+.1f}%"
             for p in positions]
    pos_text = "\n".join(lines) if lines else "  （目前空手）"

    # 誠實計分板：你的系統 vs 同期只買大盤
    spy = _spy_return_since(getattr(a, "created_at", None))
    if spy is not None:
        diff = total_pct - spy
        verdict = "✅ 系統暫時領先" if diff >= 0 else "❌ 輸給單純買大盤"
        scoreboard = (f"\n\n📊 <b>誠實計分板（自開始至今）</b>\n"
                      f"你的系統：{total_pct:+.1f}%\n"
                      f"同期只買大盤(SPY)：{spy:+.1f}%\n"
                      f"差距：{diff:+.1f}%　{verdict}")
    else:
        scoreboard = ""

    emoji = "📈" if day_chg >= 0 else "📉"
    msg = (
        f"{emoji} <b>模擬倉每日總結</b>\n"
        f"淨值：${equity:,.0f}（起始 $100,000，累計 {total_pct:+.1f}%）\n"
        f"今日損益：{day_chg:+,.0f}（{day_pct:+.2f}%）\n"
        f"持倉 {len(positions)} 檔：\n{pos_text}"
        f"{scoreboard}"
    )
    print(msg)
    telegram.send_message(msg)


if __name__ == "__main__":
    main()
