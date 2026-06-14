"""
美团景区评论爬虫
──────────────────────────────────────────────────────
目标：美团旅游/景区 用户评论
反爬较宽松，无需登录，用 requests 直接访问 JSON API。

可获取：评论内容、发布时间、评分（1-5星）、点赞数
依赖：requests, pandas, tqdm
──────────────────────────────────────────────────────
"""

import re
import time
import random
import requests
import pandas as pd
from tqdm import tqdm
from config import DELAY_MIN, DELAY_MAX, MAX_REVIEWS_MEITUAN, DATE_START
from anti_crawl import (
    make_session, random_ua, request_with_retry,
    smart_sleep, is_blocked, print_block_advice,
)

# 美团旅游城市 ID
CITY_IDS = {
    "北京": 1,
    "上海": 2,
    "广州": 16,
    "深圳": 7,
    "成都": 15,
    "武汉": 22,
    "西安": 37,
    "杭州": 10,
    "南京": 13,
    "重庆": 23,
}


def _make_session():
    return make_session(
        mobile=False,
        referer="https://www.meituan.com/",
        extra_headers={
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.meituan.com",
        },
    )


# ─────────────────────────────────────────────────────────
# 第一步：搜索景区，拿到 poiId
# ─────────────────────────────────────────────────────────

def search_poi(keyword: str, city_id: int = 1, session=None) -> list[dict]:
    """
    搜索美团景区，返回 [{"poi_id": "xxx", "name": "xxx"}, ...]
    依次尝试两个接口，取第一个有效结果。
    """
    if session is None:
        session = _make_session()

    # ── 接口1：美团搜索 JSON API ──
    try:
        url = "https://www.meituan.com/s/api/suggest.json"
        params = {"q": keyword, "type": "sight", "ci": city_id}
        resp = request_with_retry(session, "get", url, params=params, timeout=10)
        if resp and resp.status_code == 200:
            data = resp.json()
            items = (
                data.get("data", {}).get("poiInfos")
                or data.get("data", {}).get("list")
                or []
            )
            results = []
            for item in items[:5]:
                pid = str(item.get("poiId") or item.get("id") or "")
                name = item.get("name") or item.get("title", "")
                if pid:
                    results.append({"poi_id": pid, "name": name})
            if results:
                return results
    except Exception:
        pass

    # ── 接口2：旅游搜索页 HTML，正则提取 poiId ──
    try:
        url2 = f"https://travel.meituan.com/searchpage?keyword={requests.utils.quote(keyword)}&cityId={city_id}"
        resp2 = request_with_retry(session, "get", url2, timeout=12)
        if resp2 and resp2.status_code == 200:
            html = resp2.text
            matches = re.findall(r'poiId["\s:=]+["\']?(\d{4,})', html)
            if not matches:
                matches = re.findall(r'/(?:poi|sight)/(\d{4,})', html)
            if matches:
                return [{"poi_id": matches[0], "name": keyword}]
    except Exception:
        pass

    return []


def find_poi_id(keywords: list[str], city: str = "北京", session=None) -> str | None:
    """遍历关键词，返回第一个找到的 poiId"""
    city_id = CITY_IDS.get(city, 1)
    if session is None:
        session = _make_session()
    for kw in keywords:
        results = search_poi(kw, city_id, session)
        smart_sleep()
        if results:
            pid = results[0]["poi_id"]
            print(f"  [美团] 找到景区 poiId={pid}（搜索词: {kw}）")
            return pid
    print(f"  [美团] 自动搜索未找到景区，关键词: {keywords}")
    print(f"  [美团] → 请运行 python find_ids.py 或手动填写 config 里的 meituan_poi_id")
    return None


# ─────────────────────────────────────────────────────────
# 第二步：抓景区评论（JSON API 翻页）
# ─────────────────────────────────────────────────────────

_COMMENT_APIS = [
    "https://www.meituan.com/scenic/api/commentApi/getCommentList",
    "https://travel.meituan.com/api/v1/poi/comment/list",
]


def _try_comment_api(session, poi_id, page, page_size):
    """依次尝试多个 API 端点，返回原始 JSON data 或 None"""
    for base_url in _COMMENT_APIS:
        params = {
            "poiId": poi_id,
            "pageNum": page,
            "pageIndex": page,
            "pageSize": page_size,
            "sortType": 1,
        }
        resp = request_with_retry(session, "get", base_url, params=params, timeout=12, max_retries=2)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                continue
            payload = data.get("data") or data
            comment_list = (
                payload.get("commentList")
                or payload.get("list")
                or payload.get("comments")
                or (payload if isinstance(payload, list) else [])
            )
            if comment_list is not None:
                return comment_list
    return None


def _parse_comment(item: dict) -> dict | None:
    """把单条 API 返回的 dict 转成统一格式"""
    content = (
        item.get("content")
        or item.get("comment")
        or item.get("text")
        or item.get("body", "")
    )
    if not content:
        return None

    pub_date = (
        item.get("publishTime")
        or item.get("createTime")
        or item.get("time")
        or item.get("date", "")
    )
    pub_date = str(pub_date)[:10]

    rating_raw = item.get("star") or item.get("rating") or item.get("score") or 0
    try:
        rating = int(rating_raw)
        if rating > 5:
            rating = rating // 10
    except (ValueError, TypeError):
        rating = 0

    likes = (
        item.get("usefulCount")
        or item.get("likeCount")
        or item.get("likes")
        or 0
    )

    return {
        "platform": "美团",
        "content": str(content).strip(),
        "date": pub_date,
        "rating": rating,
        "likes": likes,
    }


def fetch_reviews(poi_id: str, max_count: int = MAX_REVIEWS_MEITUAN,
                  session=None) -> list[dict]:
    """翻页抓取景区评论"""
    if session is None:
        session = _make_session()

    reviews = []
    page = 1
    page_size = 20
    empty_streak = 0

    with tqdm(total=max_count, desc=f"  美团 poi={poi_id}", unit="条") as pbar:
        while len(reviews) < max_count:
            # 每隔几页换 UA
            if page % 5 == 0:
                session.headers["User-Agent"] = random_ua(mobile=False)

            comment_list = _try_comment_api(session, poi_id, page, page_size)

            if comment_list is None:
                print(f"\n  [美团] API 请求失败（第{page}页），请检查 _COMMENT_APIS 端点")
                print_block_advice("美团")
                break

            if not comment_list:
                empty_streak += 1
                if empty_streak >= 2:
                    break
                page += 1
                smart_sleep()
                continue

            empty_streak = 0
            for item in comment_list:
                if len(reviews) >= max_count:
                    break

                parsed = _parse_comment(item)
                if not parsed:
                    continue

                # 日期过滤
                if parsed["date"] and parsed["date"] < DATE_START:
                    return reviews

                reviews.append(parsed)
                pbar.update(1)

            page += 1
            smart_sleep()

    return reviews


# ─────────────────────────────────────────────────────────
# 一站式入口
# ─────────────────────────────────────────────────────────

def scrape_park(
    park_name: str,
    keywords: list[str],
    city: str = "北京",
    meituan_poi_id: str = None,
    max_count: int = MAX_REVIEWS_MEITUAN,
) -> pd.DataFrame:
    """
    对某公园执行完整爬取：搜索 POI → 抓评论 → 返回 DataFrame
    """
    session = _make_session()
    print(f"\n[美团] 开始爬取: {park_name}")

    poi_id = meituan_poi_id
    if poi_id is None:
        poi_id = find_poi_id(keywords, city, session)

    if poi_id is None:
        print(f"  [美团] {park_name} 找不到景区，跳过")
        return pd.DataFrame()

    print(f"  [美团] 景区 poiId = {poi_id}")
    raw = fetch_reviews(poi_id, max_count=max_count, session=session)

    if not raw:
        print(f"  [美团] {park_name} 无评论数据")
        print(f"  [美团] 提示：若 API 返回空，可能端点变了，检查 _COMMENT_APIS 或换用 Playwright")
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    df["park"] = park_name
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    print(f"  [美团] {park_name} 共获取 {len(df)} 条评论")
    return df


# ─────────────────────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    df = scrape_park(
        park_name="测试-圆明园",
        keywords=["圆明园"],
        city="北京",
    )
    if not df.empty:
        print(df[["date", "rating", "content"]].head(5))
        df.to_csv("test_meituan.csv", index=False, encoding="utf-8-sig")
        print("已保存到 test_meituan.csv")
    else:
        print("未获取到数据，需手动确认 API 端点或填写 meituan_poi_id")
