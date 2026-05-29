#!/bin/bash
# 點兩下開啟圖形介面（Streamlit 儀表板）
cd "$(dirname "$0")"
source .venv/bin/activate
echo "正在開啟美股訊號系統介面…瀏覽器會自動打開。"
echo "（要關閉：回到這個視窗按 Ctrl + C，或直接關掉視窗）"
streamlit run app.py
