"""Telegram 推播：把買賣訊號送到手機。"""
from __future__ import annotations

import requests

from config import settings


def send_message(text: str) -> bool:
    """送出純文字訊息，回傳是否成功。"""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        print("[telegram] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳過推播。")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"[telegram] 推播失敗: {exc}")
        return False


def format_signal(symbol: str, action: str, confidence: int, price: float,
                  reasons: list[str], stop_loss: float = 0.0,
                  take_profit: float = 0.0) -> str:
    emoji = {"BUY": "🟢 買進", "SELL": "🔴 賣出", "HOLD": "⚪ 觀望"}[action]
    reason_lines = "\n".join(f"  • {r}" for r in reasons)
    levels = ""
    if action in ("BUY", "SELL") and stop_loss and take_profit:
        levels = (f"停損: {stop_loss:.2f}\n"
                  f"停利: {take_profit:.2f}\n")
    return (
        f"<b>{symbol}</b>  {emoji}\n"
        f"信心分數: <b>{confidence}</b>/100\n"
        f"現價: {price:.2f}\n"
        f"{levels}"
        f"理由:\n{reason_lines}"
    )
