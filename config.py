# =============================================================
# config.py — 全局配置
# 使用方式：把 PARKS 里的占位名改成你们实际的公园名即可
# =============================================================

# ─────────────────────────────────────────
# 1. 公园列表（每个公园给几个常见叫法作为关键词）
# ─────────────────────────────────────────
PARKS = {
    "公园A": {
        "name": "北海公园",
        "keywords": ["北海公园", "北海公园历史文化"],
        "city": "北京",
        "ctrip_sight_id": 232,
        "ctrip_url": "https://you.ctrip.com/sight/beijing1/232.html?renderPlatform=",
        "dianping_shop_id": "G2CauBeHQ9je4IAb",
        "meituan_poi_id": None,   # 运行 find_ids.py 或手动填写
    },
    "公园B": {
        "name": "天坛公园",
        "keywords": ["天坛公园", "天坛公园历史文化"],
        "city": "北京",
        "ctrip_sight_id": 233,
        "ctrip_url": "https://you.ctrip.com/sight/beijing1/233.html?renderPlatform=",
        "dianping_shop_id": "GaGMofn91UrCLmzu",
        "meituan_poi_id": None,
    },
    "公园C": {
        "name": "颐和园",
        "keywords": ["颐和园", "颐和园历史文化"],
        "city": "北京",
        "ctrip_sight_id": 231,
        "ctrip_url": "https://you.ctrip.com/sight/beijing1/231.html?renderPlatform=",
        "dianping_shop_id": "jXbmjaCBdAMesfbx",
        "meituan_poi_id": None,
    },
    "公园D": {
        "name": "朝阳公园",
        "keywords": ["朝阳公园", "朝阳公园历史文化"],
        "city": "北京",
        "ctrip_sight_id": 107621,
        "ctrip_url": "https://you.ctrip.com/sight/beijing1/107621.html?renderPlatform=",
        "dianping_shop_id": "jXbmjaCBdAMesfbx",
        "meituan_poi_id": None,
    },
    "公园E": {
        "name": "奥林匹克森林公园",
        "keywords": ["奥林匹克森林公园", "奥林匹克森林公园历史文化"],
        "city": "北京",
        "ctrip_sight_id": 69342270,
        "ctrip_url": "https://you.ctrip.com/sight/beijing1/69342270.html?renderPlatform=#ctm_ref=www_hp_bs_lst",
        "dianping_shop_id": "jXbmjaCBdAMesfbx",
        "meituan_poi_id": None,
    },
    "公园F": {
        "name": "树村公园",
        "keywords": ["树村公园", "树村公园历史文化"],
        "city": "北京",
        "ctrip_sight_id": 1483951,
        "ctrip_url": "https://you.ctrip.com/sight/beijing1/1483951.html?renderPlatform=",
        "dianping_shop_id": "jXbmjaCBdAMesfbx",
        "meituan_poi_id": None,
    },
    # ── 继续添加公园D、E、F（复制上面格式即可）──
    # "公园D": {
    #     "name": "XXX公园",
    #     "keywords": ["XXX"],
    #     "city": "北京",
    #     "ctrip_sight_id": None,
    #     "dianping_shop_id": None,
    #     "meituan_poi_id": None,
    # },
}

# 大众点评城市ID参考（常用）：
# 1=上海, 2=南京, 4=深圳, 7=广州, 11=北京, 17=成都, 23=武汉
# 完整列表：https://www.dianping.com/citylist

# ─────────────────────────────────────────
# 2. 时间范围
# ─────────────────────────────────────────
DATE_START = "2025-01-01"   # 抓取起始日期（近一年）
DATE_END   = "2026-06-01"   # 抓取截止日期

# ─────────────────────────────────────────
# 3. 各平台抓取上限
# ─────────────────────────────────────────
MAX_REVIEWS_CTRIP    = 200   # 携程最多抓多少条
MAX_REVIEWS_DIANPING = 200   # 大众点评最多抓多少条
MAX_REVIEWS_MEITUAN  = 200   # 美团最多抓多少条
MAX_NOTES_XHS        = 100   # 小红书最多抓多少条笔记

# ─────────────────────────────────────────
# 4. 小红书账号（Playwright登录用）
# ─────────────────────────────────────────
XHS_PHONE    = "12345678901"   # 你的手机号（留空则跳过XHS爬取）
XHS_PASSWORD = ""   # 密码（如用验证码登录可留空）

# ─────────────────────────────────────────
# 5. 请求延迟（秒）—— 不要改太小，容易被封
# ─────────────────────────────────────────
DELAY_MIN = 1.5   # 每次请求最短等待
DELAY_MAX = 3.5   # 每次请求最长等待

# ─────────────────────────────────────────
# 6. 输出路径
# ─────────────────────────────────────────
DATA_DIR   = "data"       # 原始数据存放目录
OUTPUT_DIR = "output"     # 结果（Excel/图片）存放目录
