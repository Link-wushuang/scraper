"""
携程景区评论爬虫
──────────────────────────────────────────────────────
使用携程移动端 JSON API，无需登录，是四个平台里最稳定的。
可获取：评论内容、发布时间、评分（1-5星）、有用数（点赞代理）

依赖：requests, pandas, tqdm
──────────────────────────────────────────────────────
"""

import re
import os
import sys
import time
import random
import asyncio
import subprocess
import requests
import pandas as pd
from tqdm import tqdm
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
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


def _extract_sight_id_from_url(url: str) -> int | None:
    if not url or not re.match(r"^https?://([^/]+\.)?ctrip\.com/", url):
        return None
    match = re.search(r"/sight/[^/]+/(\d+)\.html", url)
    return int(match.group(1)) if match else None


_EXTRACT_PAGE_REVIEWS_JS = """
() => {
    const clean = (text) => (text || '').replace(/\\s+/g, ' ').trim();
    const skipLine = (line) => {
        if (!line || line.length < 6) return true;
        if (/^(全部|最新|有图|好评|差评|中评|综合|排序|点评|写点评|加载|下一页|上一页)$/.test(line)) return true;
        if (/^\\d+(\\.\\d+)?分$/.test(line)) return true;
        return false;
    };
    const getDate = (text) => {
        const m = text.match(/(20\\d{2})[-/.年](\\d{1,2})[-/.月](\\d{1,2})/);
        if (!m) return '';
        return `${m[1]}-${String(Number(m[2])).padStart(2, '0')}-${String(Number(m[3])).padStart(2, '0')}`;
    };
    const getRating = (text, el) => {
        const textMatch = text.match(/([1-5](?:\\.0)?)\\s*分/);
        if (textMatch) return Number(textMatch[1]);
        const cls = String(el.className || '');
        const classMatch = cls.match(/star[^0-9]*([1-5]0?)/i);
        if (!classMatch) return 0;
        const raw = Number(classMatch[1]);
        return raw > 5 ? raw / 10 : raw;
    };
    const getLikes = (text) => {
        const m = text.match(/(?:有用|赞|点赞)\\D*(\\d+)/);
        return m ? Number(m[1]) : 0;
    };

    const selectors = [
        '[class*="commentItem"]',
        '[class*="comment-item"]',
        '[class*="CommentItem"]',
        '[class*="comment_single"]',
        '[class*="reviewItem"]',
        '[class*="review-item"]',
        'li[class*="comment"]',
        'div[class*="comment"]'
    ].join(',');
    const nodes = Array.from(document.querySelectorAll(selectors));
    const results = [];
    const seen = new Set();

    for (const el of nodes) {
        const raw = clean(el.innerText);
        if (raw.length < 20 || raw.length > 2500) continue;
        if (!/(\\d{4}[-/.年]\\d{1,2}[-/.月]\\d{1,2}|分|有用|赞)/.test(raw)) continue;

        const preferred = el.querySelector(
            '[class*="content"], [class*="Content"], [class*="detail"], ' +
            '[class*="Detail"], [class*="text"], [class*="Text"], p'
        );
        const sourceText = clean((preferred && preferred.innerText) || raw);
        const lines = sourceText.split(/\\n|。/).map(clean).filter(line => !skipLine(line));
        let content = lines.find(line => line.length >= 12 && !getDate(line)) || '';
        if (!content) {
            const fallback = raw.split(/\\n|。/).map(clean).filter(line => !skipLine(line));
            content = fallback.find(line => line.length >= 12 && !getDate(line)) || '';
        }
        content = clean(content).replace(/展开全部$/, '').trim();
        if (content.length < 6) continue;

        const key = content + '|' + getDate(raw);
        if (seen.has(key)) continue;
        seen.add(key);

        results.push({
            platform: '携程',
            content,
            date: getDate(raw),
            rating: getRating(raw, el),
            likes: getLikes(raw),
            images: (raw.match(/图片|照片|图/g) || []).length,
        });
    }
    return results;
}
"""


def _edge_exe() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return "msedge"


def _edge_user_data_dir() -> str:
    return os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")


def _normalize_ctrip_review(review: dict, sight_id: int | None) -> dict:
    review = dict(review)
    review["content"] = _clean_text(review.get("content", ""))
    review["date"] = review.get("date", "")
    review["rating"] = review.get("rating", 0) or 0
    review["likes"] = review.get("likes", 0) or 0
    review["images"] = review.get("images", 0) or 0
    review["sight_id"] = sight_id or 0
    return review


async def _scroll_to_review_section(page):
    """滚动到用户点评栏"""
    await page.evaluate("""
        () => {
            const el = document.querySelector(
                '[class*="commentModule"], [class*="CommentModule"], ' +
                '[class*="commentList"], [class*="CommentList"], ' +
                '[id*="commentModule"], [id*="commentList"], ' +
                '[class*="comment-module"]'
            );
            if (el) {
                el.scrollIntoView({ block: 'start' });
            } else {
                window.scrollTo(0, document.body.scrollHeight * 0.5);
            }
        }
    """)
    await page.wait_for_timeout(2000)
    for _ in range(3):
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(600)


_CLICK_NEXT_JS = """
    () => {
        const pager = document.querySelector('.ant-pagination, [class*="ant-pagination"]');
        if (!pager) return false;

        // 方式1：ant-pagination-next 按钮
        const nextLi = pager.querySelector(
            '.ant-pagination-next:not(.ant-pagination-disabled)'
        );
        if (nextLi) {
            const btn = nextLi.querySelector('button, a') || nextLi;
            btn.scrollIntoView({ block: 'center' });
            btn.click();
            return true;
        }

        // 方式2：当前激活页码的下一个兄弟
        const active = pager.querySelector('.ant-pagination-item-active');
        if (active && active.nextElementSibling) {
            const sib = active.nextElementSibling;
            if (/ant-pagination-item/.test(sib.className)) {
                const link = sib.querySelector('a') || sib;
                link.scrollIntoView({ block: 'center' });
                link.click();
                return true;
            }
        }
        return false;
    }
"""

_FIRST_COMMENT_JS = """
    () => {
        const el = document.querySelector('[class*="commentItem"]');
        return el ? el.innerText.slice(0, 80) : '';
    }
"""


async def _click_review_next_page(page) -> bool:
    """点击评论区 Ant Design 分页的下一页按钮，带重试"""
    before = await page.evaluate(_FIRST_COMMENT_JS)

    for attempt in range(3):
        try:
            clicked = await page.evaluate(_CLICK_NEXT_JS)
            if not clicked:
                if attempt < 2:
                    await page.wait_for_timeout(1000)
                    continue
                return False

            for _ in range(10):
                await page.wait_for_timeout(500)
                after = await page.evaluate(_FIRST_COMMENT_JS)
                if after != before:
                    return True
            return True
        except Exception:
            if attempt < 2:
                await page.wait_for_timeout(1000)
    return False


async def _async_fetch_reviews_from_page(url: str, max_count: int, use_profile: bool = True) -> list[dict]:
    sight_id = _extract_sight_id_from_url(url)
    async with async_playwright() as pw:
        context = None
        browser = None
        if use_profile:
            user_data = _edge_user_data_dir()
            if not os.path.isdir(user_data):
                print(f"  [Ctrip] Edge user data dir not found: {user_data}")
                return []
            subprocess.run(["taskkill", "/f", "/im", "msedge.exe"], capture_output=True)
            await asyncio.sleep(2)
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=user_data,
                channel="msedge",
                headless=False,
                args=["--profile-directory=Default"],
                viewport={"width": 1366, "height": 820},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await pw.chromium.launch(channel="msedge", headless=False)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 820},
                user_agent=random_ua(mobile=False),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = await context.new_page()

        print(f"  [Ctrip] open: {url}")
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        except PlaywrightTimeout:
            print("  [Ctrip] page load timeout; try extracting from current DOM")

        # 先滚动到用户点评栏
        await _scroll_to_review_section(page)

        reviews = []
        seen = set()
        empty_pages = 0
        page_num = 0
        with tqdm(total=max_count, desc=f"  Ctrip page={sight_id or '?'}", unit="item") as pbar:
            while len(reviews) < max_count and empty_pages < 3:
                page_num += 1
                page_reviews = await page.evaluate(_EXTRACT_PAGE_REVIEWS_JS)
                added = 0
                skipped_dup = 0
                skipped_short = 0
                skipped_date = 0

                for item in page_reviews:
                    review = _normalize_ctrip_review(item, sight_id)
                    if len(review["content"]) < 3:
                        skipped_short += 1
                        continue
                    if review["date"] and review["date"] < DATE_START:
                        skipped_date += 1
                        continue
                    key = (review["content"], review["date"], review["rating"])
                    if key in seen:
                        skipped_dup += 1
                        continue
                    seen.add(key)
                    reviews.append(review)
                    added += 1
                    pbar.update(1)
                    if len(reviews) >= max_count:
                        break

                if added == 0:
                    empty_pages += 1
                else:
                    empty_pages = 0

                if len(reviews) >= max_count or empty_pages >= 3:
                    break
                if not await _click_review_next_page(page):
                    print(f"\n  [Ctrip] pagination stopped at page {page_num}")
                    break

        if browser:
            await browser.close()
        else:
            await context.close()

    return reviews


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

def scrape_park(
    park_name: str,
    keywords: list[str],
    sight_id: int = None,
    ctrip_sight_id: int = None,
    ctrip_url: str = None,
    use_profile: bool | None = None,
) -> pd.DataFrame:
    """
    对某公园执行完整爬取流程：
      1. 若未提供 sight_id，先搜索获取
      2. 抓评论
      3. 返回 DataFrame
    """
    if use_profile is None:
        use_profile = "--profile" in sys.argv

    if ctrip_url and use_profile:
        raw = asyncio.run(_async_fetch_reviews_from_page(
            ctrip_url,
            MAX_REVIEWS_CTRIP,
            use_profile=True,
        ))
        if not raw:
            print(f"  [鎼虹▼] {park_name} 娌℃湁璇勮鏁版嵁")
            return pd.DataFrame()

        df = pd.DataFrame(raw)
        df["park"] = park_name
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        print(f"  [Ctrip] {park_name}: fetched {len(df)} reviews")
        return df

    session = _make_session_mobile()
    print(f"\n[携程] 开始爬取: {park_name}")

    if sight_id is None:
        sight_id = ctrip_sight_id or _extract_sight_id_from_url(ctrip_url or "") or find_sight_id(keywords)

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
