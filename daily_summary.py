"""每日盤後總結：把模擬倉的資金、當日損益、持倉推播到 Telegram。

用法：python daily_summary.py
"""
from __future__ import annotations

from alpaca.trading.client import TradingClient

from config import settings
from src.stocktracker.notify import telegram


def main() -> None:
    c = TradingClient(settings.ALPACA_API_KEY, settings.ALPACA_SECRET_KEY,
                      paper=True)
    a = c.get_account()
    equity = float(a.equity)
    last_equity = float(a.last_equity) if a.last_equity else equity
    day_chg = equity - last_equity
    day_pct = (day_chg / last_equity * 100) if last_equity else 0.0
    total_pct = (equity / 100_000 - 1) * 100   # 相對起始 $100k

    positions = c.get_all_positions()
    lines = []
    for p in positions:
        pl_pct = float(p.unrealized_plpc) * 100 if p.unrealized_plpc else 0.0
        lines.append(f"  • {p.symbol} {p.qty} 股，未實現 {pl_pct:+.1f}%")
    pos_text = "\n".join(lines) if lines else "  （目前空手）"

    emoji = "📈" if day_chg >= 0 else "📉"
    msg = (
        f"{emoji} <b>模擬倉每日總結</b>\n"
        f"淨值：${equity:,.0f}（起始 $100,000，累計 {total_pct:+.1f}%）\n"
        f"今日損益：{day_chg:+,.0f}（{day_pct:+.2f}%）\n"
        f"持倉 {len(positions)} 檔：\n{pos_text}"
    )
    print(msg)
    telegram.send_message(msg)


if __name__ == "__main__":
    main()
