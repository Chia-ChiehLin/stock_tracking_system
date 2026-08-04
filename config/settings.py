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
MOMENTUM_WINDOW = 20             # 動量回看根數（測過 40/60/80/126：對窗口敏感、方向不穩，不動）
MAX_CORRELATION = 0.70           # 與既有持倉相關性超過此值就不買（收緊以避免又集中在半導體）
EXIT_COOLDOWN_HOURS = 24         # 剛賣出的標的冷卻期（小時），避免追高買回
SPLIT_GUARD_PCT = 0.50           # 單檔相對均價變動超過此比例視為拆股/資料異常，暫停自動賣出
REARM_STOPS = True               # 每輪為沒有保護的持倉自動補掛 GTC 停損

# 決策時間窗：策略是日線邏輯，盤中的半根日 K 訊號會亂跳、徒增換手與滑價成本。
# 買賣決策集中在「收盤前 N 分鐘」內執行（涵蓋至少兩班 30 分排程，容忍 Actions 延遲）；
# 其餘盤中排程只做「補掛停損」的保護性維護。0 = 關閉（隨時可決策，舊行為）。
DECISION_WINDOW_MINUTES = 75

# 趨勢出場確認：連續 N 天「收盤」跌破長期均線才賣。
# 回測（修正前視偏差後的引擎、固定停損配置）：N=2 全期與後半段大幅改善
# （單日雜訊不再洗出大贏家）、前半段約持平；代價是最大回撤加深約 2~10 個百分點
# （趨勢真走壞時多陪一天）。追求報酬故採 2；想保守可改回 1。
EXIT_CONFIRM_DAYS = 2

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
