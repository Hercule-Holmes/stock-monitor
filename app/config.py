"""全局配置"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "stock.db")
WEB_DIR = os.path.join(BASE_DIR, "web")

DB_TIMEOUT = 30            # sqlite busy timeout 秒
UPDATE_WORKERS = 8         # 数据下载并发数
PORT = 8600

# 自动更新时刻(仅池内股票), 周一~周五
SCHEDULE_SLOTS = [
    ("noon",  "中午更新",  (12, 30)),   # 12:30
    ("close", "收盘更新",  (15, 30)),   # 15:30
]

os.makedirs(DATA_DIR, exist_ok=True)
