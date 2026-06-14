"""
anti_crawl.py — 通用反爬工具模块
──────────────────────────────────────────────────────
提供：
  1. User-Agent 轮换池
  2. 带指数退避的自动重试
  3. 代理池支持（可选）
  4. 智能限速（自适应延迟）
  5. 请求会话工厂

所有爬虫统一调用此模块，避免各自重复实现。
──────────────────────────────────────────────────────
"""

import time
import random
import logging
import requests
from functools import wraps
from config import DELAY_MIN, DELAY_MAX

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# 1. User-Agent 轮换池
# ─────────────────────────────────────────────────────────

# 移动端 UA
MOBILE_UAS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.113 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/125.0.6422.80 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36",
]

# PC 端 UA
PC_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


def random_ua(mobile: bool = False) -> str:
    """随机返回一个 User-Agent"""
    pool = MOBILE_UAS if mobile else PC_UAS
    return random.choice(pool)


# ─────────────────────────────────────────────────────────
# 2. 代理池（可选，留空则直连）
# ─────────────────────────────────────────────────────────

# 在这里填入你的代理列表，格式如 "http://ip:port" 或 "http://user:pass@ip:port"
# 留空列表 = 不使用代理
PROXY_LIST: list[str] = [
    # "http://127.0.0.1:7890",
    # "http://user:pass@proxy.example.com:8080",
]


def get_proxy() -> dict | None:
    """随机返回一个代理字典，供 requests 使用。无代理返回 None。"""
    if not PROXY_LIST:
        return None
    proxy = random.choice(PROXY_LIST)
    return {"http": proxy, "https": proxy}


# ─────────────────────────────────────────────────────────
# 3. 智能延迟
# ─────────────────────────────────────────────────────────

def smart_sleep(base_min: float = DELAY_MIN, base_max: float = DELAY_MAX,
                multiplier: float = 1.0):
    """
    随机等待，支持乘数（被封后可加大 multiplier）。
    multiplier=1.0 正常；multiplier=2.0 遇到风险加倍等待。
    """
    delay = random.uniform(base_min * multiplier, base_max * multiplier)
    time.sleep(delay)


# ─────────────────────────────────────────────────────────
# 4. 带指数退避的请求重试
# ─────────────────────────────────────────────────────────

def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    max_retries: int = 3,
    base_delay: float = 2.0,
    retry_on_status: tuple = (403, 429, 500, 502, 503, 504),
    **kwargs,
) -> requests.Response | None:
    """
    发起请求，遇到失败自动重试（指数退避）。

    参数:
        session        requests.Session
        method         "get" / "post"
        url            请求地址
        max_retries    最大重试次数
        base_delay     首次重试基础延迟（秒）
        retry_on_status 哪些 HTTP 状态码触发重试
        **kwargs       传给 session.request() 的参数

    返回:
        Response 对象，或全部失败返回 None
    """
    # 设置代理（如果有）
    if "proxies" not in kwargs:
        proxy = get_proxy()
        if proxy:
            kwargs["proxies"] = proxy

    # 设置默认超时
    if "timeout" not in kwargs:
        kwargs["timeout"] = 15

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            resp = session.request(method, url, **kwargs)

            # 成功
            if resp.status_code < 400:
                return resp

            # 需要重试的状态码
            if resp.status_code in retry_on_status and attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    f"请求 {url} 返回 {resp.status_code}，"
                    f"第{attempt+1}次重试，等待 {delay:.1f}s"
                )
                time.sleep(delay)
                # 重试前换 UA
                session.headers["User-Agent"] = random_ua(
                    "mobile" in session.headers.get("User-Agent", "").lower()
                    or "iPhone" in session.headers.get("User-Agent", "")
                )
                continue

            # 不重试的错误状态码，直接返回
            return resp

        except (requests.ConnectionError, requests.Timeout, requests.ReadTimeout) as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    f"请求 {url} 网络异常: {e}，"
                    f"第{attempt+1}次重试，等待 {delay:.1f}s"
                )
                time.sleep(delay)
            else:
                logger.error(f"请求 {url} 最终失败: {e}")

        except Exception as e:
            logger.error(f"请求 {url} 未预期异常: {e}")
            last_error = e
            break

    return None


# ─────────────────────────────────────────────────────────
# 5. 会话工厂
# ─────────────────────────────────────────────────────────

def make_session(mobile: bool = False, referer: str = "", extra_headers: dict = None) -> requests.Session:
    """
    创建一个配置好反爬头的 requests.Session。

    参数:
        mobile          是否用移动端 UA
        referer         Referer 头
        extra_headers   额外自定义头
    """
    s = requests.Session()
    headers = {
        "User-Agent": random_ua(mobile),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }
    if referer:
        headers["Referer"] = referer
    if extra_headers:
        headers.update(extra_headers)
    s.headers.update(headers)
    return s


# ─────────────────────────────────────────────────────────
# 6. 检测反爬响应
# ─────────────────────────────────────────────────────────

def is_blocked(resp: requests.Response, check_login_redirect: bool = True) -> bool:
    """
    检测响应是否被反爬拦截。

    检测项：
      - 302/403 状态码
      - 跳转到登录页
      - 返回验证码页面
      - 返回空内容
    """
    if resp is None:
        return True

    # 状态码检查
    if resp.status_code in (403, 429):
        return True

    # 登录页重定向
    if check_login_redirect:
        url_lower = resp.url.lower()
        if any(kw in url_lower for kw in ("login", "passport", "verify", "captcha")):
            return True

    # 内容检查（验证码/人机验证关键词）
    text = resp.text[:2000].lower() if resp.text else ""
    block_indicators = ["验证码", "人机验证", "请完成安全验证", "access denied",
                        "请输入验证码", "滑动验证"]
    if any(ind in text for ind in block_indicators):
        return True

    return False


def print_block_advice(platform: str):
    """被封后打印建议"""
    print(f"\n  ⚠️  [{platform}] 疑似被反爬拦截，建议：")
    print(f"    1. 等待 10-30 分钟后重试")
    print(f"    2. 换 IP（切手机热点 / 使用代理）")
    print(f"    3. 在 anti_crawl.py 的 PROXY_LIST 中配置代理")
    print(f"    4. 减少抓取数量上限")
