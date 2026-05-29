# 美股當沖訊號系統

抓取美股分鐘線 → 計算技術指標 → 產生買/賣/觀望訊號（含信心分數與理由）→ 回測驗證 → Streamlit 介面 + Telegram 通知。

> ⚠️ **風險聲明**：本系統僅為決策輔助，非投資建議。當沖風險極高，散戶長期多數虧損。請務必先用 Alpaca 模擬倉（paper trading）長期驗證後，再考慮真實資金。回測表現好不代表未來會賺。

## 安裝

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 設定金鑰

```bash
cp .env.example .env
```

編輯 `.env`：
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`：到 https://alpaca.markets/ 免費註冊，在 Paper Trading 頁面取得（免費即時 IEX 資料）。
- `TELEGRAM_BOT_TOKEN`：跟 Telegram 的 @BotFather 申請 bot 取得。
- `TELEGRAM_CHAT_ID`：傳訊息給你的 bot 後，可用 @userinfobot 查自己的 chat id。

## 使用

**圖形介面（看走勢、指標、訊號、回測）**
```bash
streamlit run app.py
```

**盤中掃描 + Telegram 通知**
```bash
python scan.py              # 掃描關注清單一次
python scan.py --loop 300   # 每 5 分鐘掃一次（盤中持續執行）
python scan.py --no-notify  # 只印出不推播
```

關注清單與策略參數在 `config/settings.py` 調整。

## 架構

```
config/settings.py            全域設定、關注清單、策略參數
src/stocktracker/
  data/alpaca_client.py       Alpaca 抓分鐘/日線
  indicators/technical.py     EMA / RSI / MACD / ATR / VWAP（自行實作）
  signals/strategy.py         多指標投票 → 訊號 + 信心 + 理由
  backtest/engine.py          向量化回測（報酬/勝率/回撤/夏普）
  notify/telegram.py          Telegram 推播
app.py                        Streamlit 儀表板
scan.py                       盤中掃描器
```

## 訊號邏輯（規則式）

目前用 4 個指標投票：EMA 快慢線方向、價格相對 VWAP、RSI 超買超賣、MACD 柱狀體。
淨票數 ≥ +2 → BUY，≤ −2 → SELL，其餘 HOLD。每個訊號附信心分數與理由。

策略參數可在 `config/settings.py` 的 `STRATEGY_PARAMS` 調整，並用介面/回測驗證效果。

## 參數最佳化（防過擬合）

`src/stocktracker/backtest/optimize.py` 會網格搜尋多組參數，**只在前 70% 資料（樣本內）找最佳設定，再用後 30%（樣本外）驗證**。只有兩段都獲利才算穩健，否則視為過擬合。介面最下方有「自動最佳化」區塊可一鍵執行。

## 重要實測發現（2026-05-29）

對 AAPL / TSLA / NVDA 跑樣本內外驗證：
- **5 分 / 15 分（當沖）**：多數虧損，或樣本內賺、樣本外賠 → 沒有穩健優勢
- **1 小時 / 日線（波段）**：三檔皆樣本內外都獲利 → 通過驗證

結論：這套規則式策略的優勢在**較長時間框架**才顯現；純當沖（極短線）被雜訊與滑價吃掉。若追求穩定，建議用 1Hour/1Day 做波段。

## 下一步（尚未實作）

- 接 Alpaca 模擬倉自動下單
- 加入趨勢過濾，減少盤整時的無效交易
- （後期）機器學習模型當作額外一票，且需通過回測才採用
```
