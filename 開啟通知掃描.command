#!/bin/bash
# 點兩下啟動盤中自動掃描，出現買賣訊號就推播到 Telegram
cd "$(dirname "$0")"
source .venv/bin/activate
echo "正在啟動盤中掃描，每 5 分鐘掃一次關注清單…"
echo "出現買進/賣出訊號會推播到你的 Telegram。"
echo "（要停止：回到這個視窗按 Ctrl + C，或直接關掉視窗）"
python scan.py --loop 300
