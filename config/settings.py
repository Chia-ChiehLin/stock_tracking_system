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
# 改用日線決策：回測證實 1 小時線的訊號是「負資訊量」（比擲銅板還差），日線才站得住
AUTO_TRADE_TIMEFRAME = "1Day"
POSITION_PCT = 0.09             # 每檔投入帳戶淨值的比例（10 檔 × 9% ≈ 90% 部署）
MAX_OPEN_POSITIONS = 10         # 提高資金部署（回測：5→10 檔報酬與夏普同時改善）

# 自動選股範圍
USE_FULL_UNIVERSE = True         # True=從全市場（流動性過濾）自動選股；False=只看 WATCHLIST
UNIVERSE_MIN_PRICE = 5.0         # 全市場篩選：最低股價
UNIVERSE_MIN_DOLLAR_VOLUME = 30_000_000  # 全市場篩選：每日最低成交金額

# 大盤趨勢過濾（避崩盤）：大盤跌破長期均線時「減半曝險」（而非完全停手）
# 回測：硬性歸零會在強勢段付出大量機會成本；改成連續減半較佳
MARKET_REGIME_FILTER = True
MARKET_SYMBOL = "SPY"            # 用來判斷大盤多空的指標
MARKET_MA_DAYS = 200            # 大盤長期均線天數
REGIME_REDUCE_FACTOR = 0.5     # 大盤轉空時，持倉數與單檔比例各打的折扣

# 選股與風控
RANK_BY_MOMENTUM = True          # True=用動量排名挑股（回測勝率較高）；False=用信心分數
MOMENTUM_WINDOW = 20             # 動量回看根數
MAX_CORRELATION = 0.85           # 與既有持倉相關性超過此值就不買（避免集中同類股）
EXIT_COOLDOWN_HOURS = 24         # 剛賣出的標的冷卻期（小時），避免追高買回
SPLIT_GUARD_PCT = 0.50           # 單檔相對均價變動超過此比例視為拆股/資料異常，暫停自動賣出
REARM_STOPS = True               # 每輪為沒有保護的持倉自動補掛 GTC 停損

# 當沖預設使用的分鐘線粒度
DEFAULT_TIMEFRAME = "5Min"

# 訊號策略參數
STRATEGY_PARAMS = {
    "rsi_period": 14,
    "rsi_oversold": 35,
    "rsi_overbought": 65,
    # RSI 用法："filter"=只當過熱否決（RSI>rsi_overheat 不買，不再加分做多）；
    # "vote"=舊版把超賣當買進投票（回測證實會挑到最差進場點，已停用）
    "rsi_mode": "filter",
    "rsi_overheat": 70,
    "ema_fast": 9,
    "ema_slow": 21,
    "trend_ema": 50,  # 趨勢過濾：只順著這條長期均線方向交易
    "atr_period": 14,
    # 風控：停損放寬到 3.0×ATR（回測：太緊的 1.5× 把贏家太早洗出、勝率更低）
    "atr_stop_mult": 3.0,
    "risk_reward": 2.0,
}
