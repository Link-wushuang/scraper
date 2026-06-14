"""
携程景区评论爬虫
──────────────────────────────────────────────────────
使用携程移动端 JSON API，无需登录，是四个平台里最稳定的。
可获取：评论内容、发布时间、评分（1-5星）、有用数（点赞代理）

依赖：requests, pandas, tqdm
──────────────────────────────────────────────────────
"""

import re
import time
import random
import requests
import pandas as pd
from tqdm import tqdm
from datetime import datetime
from config import DELAY_MIN, DELAY_MAX, MAX_REVIEWS_CTRIP, DATE_START
from anti_crawl import (
    make_session, random_ua, request_with_retry,
    smart_sleep, is_blocked, print_block_advice,
)


# ─────────────────────────────────────────────────────────
# 内部工具函数
# ─────────────────────────────────────────────────────────

def _make_session_mobile():
    """移动端 session（用于评论API）"""
    return make_session(
        mobile=True,
        referer="https://m.ctrip.com/",
        extra_headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
        },
    )


def _make_session_pc():
    """PC 端 session（用于页面搜索）"""
    return make_session(
        mobile=False,
        referer="https://you.ctrip.com/",
    )


# ─────────────────────────────────────────────────────────
# 第一步：搜索景区，拿到 sightId
# ─────────────────────────────────────────────────────────

def search_sight(keyword: str) -> list[dict]:
    """
    在 you.ctrip.com 搜索景区，解析页面 HTML 提取 sightId。
    返回格式：[{"sightId": 123, "sightName": "xx公园"}, ...]
    """
    pc = _make_session_pc()
    url = f"https://you.ctrip.com/sight/search.html?keyword={requests.utils.quote(keyword)}"
    try:
        resp = request_with_retry(pc, "get", url)
        if resp is None or resp.status_code != 200:
            print(f"  [携程] 搜索 '{keyword}' 请求失败")
            return []

        if is_blocked(resp):
            print_block_advice("携程")
            return []

        html = resp.text
        # 景区页面链接格式：/sight/{city}/{id}.html
        matches = re.findall(r'/sight/[a-z]+\d*/(\d{4,})\.html', html)
        results = []
        seen = set()
        for sid in matches:
            if sid not in seen:
                seen.add(sid)
                results.append({"sightId": int(sid), "sightName": keyword})
        return results
    except Exception as e:
        print(f"  [携程] 搜索 '{keyword}' 失败: {e}")
        return []


def find_sight_id(keywords: list[str]) -> int | None:
    """
    遍历关键词列表，返回第一个搜到的景区 sightId。
    """
    for kw in keywords:
        results = search_sight(kw)
        smart_sleep()
        if results:
            chosen = results[0]
            print(f"  [携程] 找到景区 ID={chosen['sightId']}（搜索词: {kw}）")
            return chosen["sightId"]
    print(f"  [携程] 自动搜索未找到景区，关键词: {keywords}")
    print(f"  [携程] → 请运行 python find_ids.py 或手动填写 config 里的 ctrip_sight_id")
    return None


# ─────────────────────────────────────────────────────────
# 第二步：抓景区评论
# ─────────────────────────────────────────────────────────

def fetch_reviews(sight_id: int, max_count: int = MAX_REVIEWS_CTRIP, session=None) -> list[dict]:
    """
    抓指定景区的评论。
    返回原始评论列表，每条包含 content/date/rating/likes 等字段。
    """
    if session is None:
        session = _make_session_mobile()

    # 多个备选端点，依次尝试直到有数据
    _APIS = [
        "https://m.ctrip.com/restapi/soa2/13444/json/getCommentCollapseList",
        "https://m.ctrip.com/restapi/soa2/13444/json/getCommentList",
        "https://m.ctrip.com/restapi/soa2/11189/json/getSightCommentList",
    ]
    # 加上景区专属 Referer，避免被当作裸 API 请求
    session.headers["Referer"] = f"https://m.ctrip.com/webapp/scenic/sight/{sight_id}.html"

    reviews = []
    page = 1
    page_size = 10
    max_pages = (max_count // page_size) + 1
    api_url = _APIS[0]
    consecutive_errors = 0

    with tqdm(total=max_count, desc=f"  携程 sightId={sight_id}", unit="条") as pbar:
        while page <= max_pages:
            payload = {
                "sightId": sight_id,
                "pageIndex": page,
                "pageSize": page_size,
                "sortType": 3,   # 3=最新, 1=默认, 2=有用
                "applicationType": 1,
            }

            # 每隔几页换一次 UA，降低被识别风险
            if page % 5 == 0:
                session.headers["User-Agent"] = random_ua(mobile=True)

            resp = request_with_retry(session, "post", api_url, json=payload)
            if resp is None:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    print(f"\n  [携程] 连续 {consecutive_errors} 次请求失败，停止")
                    break
                page += 1
                smart_sleep(multiplier=2.0)
                continue

            try:
                data = resp.json()
            except Exception:
                print(f"\n  [携程] 第{page}页 JSON 解析失败")
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    break
                page += 1
                smart_sleep(multiplier=1.5)
                continue

            consecutive_errors = 0  # 成功拿到 JSON，重置计数

            # 兼容多种响应结构（用 or {} 防止值为 None 时链式调用崩溃）
            comment_list = (
                data.get("commentList")
                or (data.get("data") or {}).get("commentList")
                or (data.get("result") or {}).get("commentList")
                or []
            )
            if not comment_list:
                if page == 1:
                    # 打印调试信息，帮助判断 API 返回了什么
                    print(f"\n  [携程调试] HTTP {resp.status_code}, 响应 keys: {list(data.keys())}")
                    ack = data.get("ResponseStatus", {}).get("Ack", "N/A")
                    print(f"  [携程调试] ResponseStatus.Ack = {ack}")
                    # 尝试下一个端点
                    idx = _APIS.index(api_url)
                    if idx + 1 < len(_APIS):
                        api_url = _APIS[idx + 1]
                        print(f"  [携程] 换用备选端点: {api_url}")
                        continue
                break

            for item in comment_list:
                pub_date = item.get("publishedDate", "")
                # 过滤日期：只保留 DATE_START 之后的内容
                if pub_date and pub_date < DATE_START:
                    return reviews

                reviews.append({
                    "platform": "携程",
                    "content": _clean_text(item.get("content", "")),
                    "date": pub_date,
                    "rating": item.get("rating", 0),
                    "likes": item.get("usefulCount", 0),
                    "images": len(item.get("imageList", [])),
                    "sight_id": sight_id,
                })
                pbar.update(1)

                if len(reviews) >= max_count:
                    return reviews

            page += 1
            smart_sleep()

    return reviews


# ─────────────────────────────────────────────────────────
# 第三步：一站式爬取（搜索 + 评论）
# ─────────────────────────────────────────────────────────

def scrape_park(park_name: str, keywords: list[str], sight_id: int = None, ctrip_sight_id: int = None) -> pd.DataFrame:
    """
    对某公园执行完整爬取流程：
      1. 若未提供 sight_id，先搜索获取
      2. 抓评论
      3. 返回 DataFrame
    """
    session = _make_session_mobile()
    print(f"\n[携程] 开始爬取: {park_name}")

    if sight_id is None:
        sight_id = find_sight_id(keywords)

    if sight_id is None:
        print(f"  [携程] {park_name} 找不到景区，跳过")
        return pd.DataFrame()

    raw = fetch_reviews(sight_id, session=session)
    if not raw:
        print(f"  [携程] {park_name} 没有评论数据")
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    df["park"] = park_name
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    print(f"  [携程] {park_name} 共获取 {len(df)} 条评论")
    return df


# ─────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """去除多余空白"""
    if not text:
        return ""
    return " ".join(text.split())


# ─────────────────────────────────────────────────────────
# 单独测试入口
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    df = scrape_park(
        park_name="测试公园",
        keywords=["明故宫遗址公园", "明故宫"],
    )
    if not df.empty:
        print(df[["date", "rating", "content"]].head(5))
        df.to_csv("test_ctrip.csv", index=False, encoding="utf-8-sig")
        print("已保存到 test_ctrip.csv")
