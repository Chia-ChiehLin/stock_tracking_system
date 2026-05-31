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

# 自動交易（模擬倉）設定
AUTO_TRADE_TIMEFRAME = "1Hour"   # 自動交易依據的 K 棒粒度（用驗證過的 1 小時）
POSITION_PCT = 0.10              # 每檔投入帳戶淨值的比例（0.10 = 10%）
MAX_OPEN_POSITIONS = 5          # 同時最多持有幾檔，控制曝險

# 自動選股範圍
USE_FULL_UNIVERSE = True         # True=從全市場（流動性過濾）自動選股；False=只看 WATCHLIST
UNIVERSE_MIN_PRICE = 5.0         # 全市場篩選：最低股價
UNIVERSE_MIN_DOLLAR_VOLUME = 30_000_000  # 全市場篩選：每日最低成交金額

# 大盤趨勢過濾（避崩盤）：大盤跌破長期均線時，暫停所有新進場
MARKET_REGIME_FILTER = True
MARKET_SYMBOL = "SPY"            # 用來判斷大盤多空的指標
MARKET_MA_DAYS = 200            # 大盤長期均線天數

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
