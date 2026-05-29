"""全域設定：讀取 .env、定義關注清單與策略參數。"""
import os
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 盤中掃描的關注清單
WATCHLIST = ["AAPL", "TSLA", "NVDA", "MSFT", "AMD"]

# 當沖預設使用的分鐘線粒度
DEFAULT_TIMEFRAME = "5Min"

# 訊號策略參數
STRATEGY_PARAMS = {
    "rsi_period": 14,
    "rsi_oversold": 35,
    "rsi_overbought": 65,
    "ema_fast": 9,
    "ema_slow": 21,
    "trend_ema": 50,  # 趨勢過濾：只順著這條長期均線方向交易
    "atr_period": 14,
    # 風控：停損設在進場價 ± atr_stop_mult 倍 ATR；停利為停損距離 × risk_reward
    "atr_stop_mult": 1.5,
    "risk_reward": 2.0,
}
