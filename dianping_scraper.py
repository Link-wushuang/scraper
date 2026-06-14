"""
大众点评景区评论爬虫（Playwright 方案）
──────────────────────────────────────────────────────
⚠️  大众点评反爬极强：JS 渲染 + CSS 字体混淆 + 频率检测
   requests 无法获取评论内容，必须用 Playwright 渲染真实页面。

运行流程：
  1. 首次运行弹出浏览器让用户手动登录，保存 Cookie
  2. 之后自动加载 Cookie，Playwright 渲染评论页面
  3. 分页提取评论（从渲染后 DOM 中获取）

注意：大众点评对部分文字使用 CSS 字体加密，提取的文本可能
     有少量乱码字符，但大部分内容可读，不影响关键词分类。

依赖：playwright, pandas, tqdm
──────────────────────────────────────────────────────
"""

import re
import os
import json
import random
import asyncio
import requests
import subprocess
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from config import DELAY_MIN, DELAY_MAX, MAX_REVIEWS_DIANPING, DATE_START
from anti_crawl import random_ua

COOKIE_FILE = Path("dianping_cookies.json")
EDGE_CDP_ENDPOINTS = ("http://127.0.0.1:9222", "http://localhost:9222")

# Stealth JS：隐藏 Playwright 自动化特征
_STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });
    window.chrome = { runtime: {}, loadTimes: () => {} };
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(params);
    // 移除 PhantomJS 痕迹
    delete window.callPhantom;
}
"""

# 从渲染页面提取评论的 JS（兼容 PC 和移动站）
_EXTRACT_REVIEWS_JS = """
() => {
    const results = [];

    // PC 站 + 移动站选择器
    const containers = document.querySelectorAll(
        '.reviews-items > ul > li, ' +
        'div.review-list-item, ' +
        'li.comment-item, ' +
        '[class*="review-item"], ' +
        '.main-review, ' +
        '.list-item, ' +
        '.comment-list > li, ' +
        '.review-list > div'
    );

    containers.forEach(el => {
        // ── 评论文本 ──
        const textEl = el.querySelector(
            '.review-words, .review-truncated-words, ' +
            '[class*="review-words"], .desc, .content, ' +
            '.review-words-hide, .comment-txt, .comment-content, ' +
            '[class*="comment-text"], [class*="review-content"]'
        );
        let text = '';
        if (textEl) {
            const fullEl = textEl.querySelector('.review-words-hide');
            text = (fullEl || textEl).innerText.trim();
        }
        if (!text || text.length < 3) return;

        // ── 评分 ──
        let rating = 0;
        const starEl = el.querySelector(
            '[class*="star"], [class*="rank-rst"], [class*="sml-rank"], ' +
            '[class*="star-icon"], [class*="rating"]'
        );
        if (starEl) {
            const cls = starEl.className;
            const m = cls.match(/star\\D?(\\d+)/i)
                   || cls.match(/rank-rst(\\d+)/i)
                   || cls.match(/level-(\\d+)/i);
            if (m) {
                const v = parseInt(m[1]);
                rating = v > 5 ? Math.floor(v / 10) : v;
            }
        }

        // ── 日期 ──
        const timeEl = el.querySelector(
            '.time, [class*="time"], time, .misc-info, ' +
            '.date, [class*="date"], .review-time'
        );
        let date = '';
        if (timeEl) {
            const raw = timeEl.innerText.trim();
            const dm = raw.match(/(\\d{4}[\\-/]\\d{1,2}[\\-/]\\d{1,2})/);
            date = dm ? dm[1] : raw;
        }

        // ── 点赞数 ──
        let likes = 0;
        const likeEl = el.querySelector('[class*="useful"], [class*="like"], .reply');
        if (likeEl) {
            const m = likeEl.innerText.match(/\\d+/);
            if (m) likes = parseInt(m[0]);
        }

        results.push({
            platform: '大众点评',
            content: text,
            date: date,
            rating: rating,
            likes: likes,
        });
    });

    return results;
}
"""

_FIND_SHOP_CARDS_JS = """
() => {
    const results = [];
    const seen = new Set();
    const links = Array.from(document.querySelectorAll('a[href]'));

    for (const link of links) {
        const rawHref = link.getAttribute('href') || '';
        const href = link.href || rawHref;
        const match = href.match(/\\/(?:shop|shopshare)\\/([A-Za-z0-9]{6,})/);
        if (!match) continue;
        if (seen.has(match[1])) continue;

        let target = link;
        let cur = link;
        for (let i = 0; i < 8 && cur; i++) {
            const rect = cur.getBoundingClientRect();
            const text = (cur.innerText || '').trim();
            if (rect.width > 80 && rect.height > 50 && text.length > 0) {
                target = cur;
                break;
            }
            cur = cur.parentElement;
        }

        const rect = target.getBoundingClientRect();
        if (rect.width < 5 || rect.height < 5) continue;

        seen.add(match[1]);
        results.push({
            shop_id: match[1],
            href: rawHref,
            text: (target.innerText || link.innerText || '').trim().slice(0, 120),
        });
    }

    return results;
}
"""


def _extract_shop_id_from_url(url: str) -> str | None:
    """Return a Dianping shop id from shop/shopshare URLs."""
    if not url:
        return None

    if url.startswith("/"):
        url = "https://www.dianping.com" + url

    if not re.match(r"^https?://([^/]+\.)?dianping\.com/", url):
        return None

    match = re.search(r"/(?:shop|shopshare)/([A-Za-z0-9]{6,})", url)
    return match.group(1) if match else None


def _review_key(review: dict) -> tuple[str, str, int]:
    return (
        str(review.get("content", "")).strip(),
        str(review.get("date", "")).strip(),
        int(review.get("rating") or 0),
    )


def _add_unique_review(rows: list[dict], seen: set, review: dict) -> bool:
    content = str(review.get("content", "")).strip()
    if len(content) < 3:
        return False

    review = dict(review)
    review["content"] = content
    key = _review_key(review)
    if key in seen:
        return False

    seen.add(key)
    rows.append(review)
    return True


# ─────────────────────────────────────────────────────────
# Cookie 管理 & 登录
# ─────────────────────────────────────────────────────────

async def _do_login(context):
    """在 Playwright context 中完成登录"""
    page = await context.new_page()
    print("\n" + "=" * 60)
    print("[点评] 首次运行需要登录大众点评")
    print("即将打开浏览器，请在里面完成登录（扫码或手机号均可）")
    print("登录完成后回到这个窗口，按回车继续")
    print("=" * 60)
    await page.goto("https://account.dianping.com/login", timeout=30000)
    input("\n  [OK] 浏览器已打开，请完成登录后，回到此窗口按回车...")
    cookies = await context.cookies()
    COOKIE_FILE.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  [点评] Cookie 已保存到 {COOKIE_FILE}")
    await page.close()


# ─────────────────────────────────────────────────────────
# 搜索景区 shop_id（仍用 requests，够用）
# ─────────────────────────────────────────────────────────

def _search_shop_id(keywords: list[str], city_id: str = "11") -> str | None:
    """用正则从搜索页 HTML 提取 shop_id"""
    from anti_crawl import make_session, request_with_retry, smart_sleep

    for kw in keywords:
        session = make_session(mobile=True, referer="https://www.dianping.com/")
        url = f"https://www.dianping.com/search/keyword/{city_id}/0_{requests.utils.quote(kw)}"
        resp = request_with_retry(session, "get", url, max_retries=2)
        if resp and resp.status_code == 200:
            matches = re.findall(r'/shop/([A-Za-z0-9]{6,})', resp.text)
            if matches:
                print(f"  [点评] 找到景区 ID={matches[0]}（搜索词: {kw}）")
                return matches[0]
        smart_sleep()
    print(f"  [点评] 自动搜索未找到景区，关键词: {keywords}")
    print(f"  [点评] → 请手动填写 config 里的 dianping_shop_id")
    return None


# ─────────────────────────────────────────────────────────
# 日期标准化
# ─────────────────────────────────────────────────────────

def _normalize_date(raw: str) -> str:
    """将各种日期格式统一为 YYYY-MM-DD，失败返回空字符串"""
    if not raw:
        return ""
    raw = raw.strip()
    for prefix in ("更新于", "发布于", "点评于"):
        raw = raw.replace(prefix, "").strip()

    # 直接正则提取
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%m-%d", "%m月%d日"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _check_blocked(url: str, body_text: str = "") -> str | None:
    """检测是否被大众点评反爬拦截。返回拦截原因，正常返回 None。"""
    url_lower = url.lower()
    # App 导流页面（强反爬）
    if "app-m-user-growth" in url_lower or "h5.dianping.com/app" in url_lower:
        return "被重定向到 App 导流页（h5.dianping.com），大众点评已识别为爬虫"
    # 登录/注册页
    if "login" in url_lower or "passport" in url_lower:
        return "跳转到登录页，Cookie 失效"
    # 验证码
    if "verify" in url_lower or "captcha" in url_lower:
        return "触发验证码"
    # 内容检测
    if body_text:
        if any(kw in body_text for kw in ["验证码", "人机验证", "请完成安全验证", "滑动验证"]):
            return "页面包含验证码"
    return None


# ─────────────────────────────────────────────────────────
# Playwright 抓取评论（核心）
# ─────────────────────────────────────────────────────────

def _edge_exe() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return "msedge"


async def _connect_cdp_browser(pw):
    for endpoint in EDGE_CDP_ENDPOINTS:
        try:
            browser = await pw.chromium.connect_over_cdp(endpoint)
            print(f"  [Dianping] connected to Edge CDP: {endpoint}")
            return browser
        except Exception:
            continue
    return None


def _all_pages(browser) -> list:
    pages = []
    for context in browser.contexts:
        pages.extend(context.pages)
    return pages


async def _pick_dianping_page(browser):
    for page in reversed(_all_pages(browser)):
        if "dianping.com" in page.url:
            await page.bring_to_front()
            return page
    return None


async def _connect_open_dianping_page(pw):
    browser = await _connect_cdp_browser(pw)

    if browser is None:
        print("  [Dianping] no Edge CDP browser found on port 9222.")
        print("  [Dianping] starting Edge with remote debugging; open Dianping manually there.")
        try:
            subprocess.Popen([_edge_exe(), "--remote-debugging-port=9222"])
        except Exception as exc:
            print(f"  [Dianping] failed to start Edge: {exc}")
        input("  [Dianping] open the Dianping search/shop/review page, then press Enter...")
        browser = await _connect_cdp_browser(pw)

    if browser is None:
        print("  [Dianping] cannot connect to Edge CDP. Start Edge with --remote-debugging-port=9222 and retry.")
        return None, None

    page = await _pick_dianping_page(browser)
    if page is None:
        context = browser.contexts[0]
        page = context.pages[-1] if context.pages else await context.new_page()
        await page.bring_to_front()
        input("  [Dianping] no Dianping tab found. Open one in this Edge window, then press Enter...")
        page = await _pick_dianping_page(browser)

    if page is None:
        print("  [Dianping] still no Dianping tab found; skip.")
        return browser, None

    print(f"  [Dianping] using current page: {page.url}")
    return browser, page


async def _expand_review_text(page):
    try:
        expand_btns = await page.query_selector_all(
            'a.fold, [class*="unfold"], [class*="expand"], ' +
            '.review-words a, .show-more, [class*="more"], ' +
            '[class*="view-all"], .comment-expand'
        )
        for btn in expand_btns[:10]:
            try:
                await btn.click(timeout=1000)
                await page.wait_for_timeout(250)
            except Exception:
                pass
    except Exception:
        pass


async def _collect_current_reviews(page, all_reviews: list[dict], seen: set, max_count: int, pbar) -> str:
    await page.wait_for_timeout(random.randint(1200, 2200))
    body_text = ""
    try:
        body_text = await page.inner_text("body", timeout=3000)
    except Exception:
        pass

    blocked = _check_blocked(page.url, body_text)
    if blocked:
        print(f"\n  [Dianping] {blocked}")
        return "blocked"

    await _expand_review_text(page)
    reviews = await page.evaluate(_EXTRACT_REVIEWS_JS)
    added = 0

    for review in reviews:
        if len(all_reviews) >= max_count:
            return "done"

        review["date"] = _normalize_date(review.get("date", ""))
        if review["date"] and review["date"] < DATE_START:
            return "date_stop"

        if _add_unique_review(all_reviews, seen, review):
            added += 1
            pbar.update(1)

    return "added" if added else "empty"


async def _click_review_entry(page) -> bool:
    try:
        clicked = await page.evaluate("""
            () => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                const byHref = links.find(a => /review_all/.test(a.href || a.getAttribute('href') || ''));
                const byText = links.find(a => /全部点评|网友点评|点评|评价/.test((a.innerText || '').trim()));
                const target = byHref || byText;
                if (!target) return false;
                target.removeAttribute('target');
                target.scrollIntoView({ block: 'center' });
                target.click();
                return true;
            }
        """)
        if clicked:
            await page.wait_for_timeout(random.randint(1800, 3000))
            return True
    except Exception:
        pass
    return False


async def _click_next_review_page(page) -> bool:
    try:
        clicked = await page.evaluate("""
            () => {
                const nodes = Array.from(document.querySelectorAll('a[href], button'));
                const target = nodes.find(el => {
                    const text = (el.innerText || el.textContent || '').trim();
                    const cls = String(el.className || '');
                    const href = el.href || el.getAttribute('href') || '';
                    const disabled = el.disabled || /disabled/.test(cls) || el.getAttribute('aria-disabled') === 'true';
                    if (disabled) return false;
                    return text === '下一页'
                        || text === '>'
                        || /next/i.test(cls)
                        || (/review_all\\/p\\d+/.test(href) && /下一页|>/.test(text));
                });
                if (!target) return false;
                if (target.tagName === 'A') target.removeAttribute('target');
                target.scrollIntoView({ block: 'center' });
                target.click();
                return true;
            }
        """)
        if clicked:
            await page.wait_for_timeout(random.randint(2000, 3500))
            return True
    except Exception:
        pass
    return False


async def _click_shop_card(page, card: dict) -> bool:
    box = await page.evaluate("""
        (card) => {
            let link = null;
            const links = Array.from(document.querySelectorAll('a[href]'));
            if (card.href) {
                link = links.find(a => (a.getAttribute('href') || '') === card.href || a.href === card.href);
            }
            if (!link && card.shop_id) {
                link = links.find(a => (a.href || a.getAttribute('href') || '').includes(card.shop_id));
            }
            if (!link) return { error: 'not_found' };
            link.removeAttribute('target');

            let target = link;
            let cur = link;
            for (let i = 0; i < 8 && cur; i++) {
                const rect = cur.getBoundingClientRect();
                if (rect.width > 80 && rect.height > 50) {
                    target = cur;
                    break;
                }
                cur = cur.parentElement;
            }

            target.scrollIntoView({ block: 'center' });
            const rect = target.getBoundingClientRect();
            return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, w: rect.width, h: rect.height };
        }
    """, card)

    if not box or box.get("error") or box["w"] < 5 or box["h"] < 5:
        return False

    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(random.randint(2200, 3800))
    return True


async def _return_to_url_by_history(page, url: str):
    for _ in range(4):
        if page.url == url:
            return
        try:
            await page.go_back(timeout=8000, wait_until="domcontentloaded")
            await page.wait_for_timeout(800)
        except Exception:
            break


async def _scrape_open_page_reviews(page, max_count: int) -> list[dict]:
    all_reviews = []
    seen_reviews = set()
    seen_shops = set()
    empty_rounds = 0

    with tqdm(total=max_count, desc="  Dianping open page", unit="item") as pbar:
        while len(all_reviews) < max_count and empty_rounds < 5:
            status = await _collect_current_reviews(page, all_reviews, seen_reviews, max_count, pbar)
            if status in {"done", "date_stop", "blocked"}:
                break
            if status == "added":
                empty_rounds = 0
                if len(all_reviews) >= max_count:
                    break
                if await _click_next_review_page(page):
                    continue
                break

            if _extract_shop_id_from_url(page.url) and await _click_review_entry(page):
                empty_rounds = 0
                continue

            source_url = page.url
            cards = await page.evaluate(_FIND_SHOP_CARDS_JS)
            cards = [c for c in cards if c.get("shop_id") not in seen_shops]

            if not cards:
                empty_rounds += 1
                await page.evaluate("(d) => window.scrollBy(0, d)", random.randint(500, 900))
                await page.wait_for_timeout(random.randint(900, 1600))
                continue

            empty_rounds = 0
            for card in cards:
                if len(all_reviews) >= max_count:
                    break
                shop_id = card.get("shop_id") or _extract_shop_id_from_url(card.get("href", ""))
                if not shop_id or shop_id in seen_shops:
                    continue
                seen_shops.add(shop_id)

                if not await _click_shop_card(page, card):
                    continue

                await _click_review_entry(page)
                status = await _collect_current_reviews(page, all_reviews, seen_reviews, max_count, pbar)
                if status in {"done", "date_stop", "blocked"}:
                    return all_reviews

                await _return_to_url_by_history(page, source_url)
                await page.wait_for_timeout(random.randint(800, 1400))

    return all_reviews


async def _async_fetch_reviews_from_open_page(max_count: int) -> list[dict]:
    """Attach to the user's current Dianping page and crawl by clicking visible cards."""
    async with async_playwright() as pw:
        _, page = await _connect_open_dianping_page(pw)
        if page is None:
            return []
        return await _scrape_open_page_reviews(page, max_count)


async def _async_fetch_reviews(shop_id: str, max_count: int) -> list[dict]:
    """用 Playwright 渲染评论页面并提取内容"""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="msedge", headless=False)
        context = await browser.new_context(
            viewport={"width": random.choice([375, 390, 414]),
                       "height": random.choice([667, 812, 896])},
            user_agent=random_ua(mobile=True),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(_STEALTH_JS)

        # 加载或获取 Cookie
        if COOKIE_FILE.exists():
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            await context.add_cookies(cookies)
            print(f"  [点评] 已加载 Cookie")
        else:
            await _do_login(context)

        page = await context.new_page()

        # 访问主站验证登录状态
        await page.goto("https://www.dianping.com/", timeout=20000,
                         wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        blocked = _check_blocked(page.url)
        if blocked:
            print(f"  [点评] {blocked}")
            if COOKIE_FILE.exists():
                print(f"  [点评] Cookie 可能失效，删除 {COOKIE_FILE} 后重试")
            await browser.close()
            return []

        all_reviews = []
        page_num = 1
        empty_streak = 0

        pbar = tqdm(total=max_count, desc=f"  点评 shop={shop_id}", unit="条")

        while len(all_reviews) < max_count:
            # 用 PC 站 URL（m.dianping.com 没有 /review_all 路径）
            url = f"https://www.dianping.com/shop/{shop_id}/review_all/p{page_num}"
            try:
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                print(f"\n  [点评] 第{page_num}页加载超时")
                empty_streak += 1
                if empty_streak >= 3:
                    break
                page_num += 1
                continue

            await page.wait_for_timeout(random.randint(2000, 4000))

            # 检测反爬拦截
            body_text = ""
            try:
                body_text = await page.inner_text("body")
            except Exception:
                pass
            blocked = _check_blocked(page.url, body_text)
            if blocked:
                print(f"\n  [点评] {blocked}，停止抓取")
                if "Cookie" in blocked or "登录" in blocked:
                    print(f"  [点评] 提示：删除 {COOKIE_FILE} 重新运行以重新登录")
                else:
                    print(f"  [点评] 建议：等待 30 分钟后重试，或切换 IP")
                break

            # 尝试点击"展开全文"按钮（PC + 移动站）
            try:
                expand_btns = await page.query_selector_all(
                    'a.fold, [class*="unfold"], [class*="expand"], ' +
                    '.review-words a, .show-more, [class*="more"], ' +
                    '[class*="view-all"], .comment-expand'
                )
                for btn in expand_btns[:10]:
                    try:
                        await btn.click()
                        await page.wait_for_timeout(300)
                    except Exception:
                        pass
            except Exception:
                pass

            # 提取评论
            reviews = await page.evaluate(_EXTRACT_REVIEWS_JS)

            if not reviews:
                empty_streak += 1
                if empty_streak >= 3:
                    print(f"\n  [点评] 连续 {empty_streak} 页无评论，停止")
                    break
            else:
                empty_streak = 0
                for r in reviews:
                    if len(all_reviews) >= max_count:
                        break
                    # 标准化日期
                    r["date"] = _normalize_date(r.get("date", ""))
                    # 日期过滤
                    if r["date"] and r["date"] < DATE_START:
                        pbar.close()
                        # 保存更新后的 Cookie
                        updated = await context.cookies()
                        COOKIE_FILE.write_text(
                            json.dumps(updated, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        await browser.close()
                        return all_reviews
                    all_reviews.append(r)
                    pbar.update(1)

            page_num += 1
            # 随机延迟，模拟人类浏览
            await page.wait_for_timeout(random.randint(3000, 6000))

        pbar.close()

        # 保存更新后的 Cookie
        updated = await context.cookies()
        COOKIE_FILE.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        await browser.close()

    return all_reviews


# ─────────────────────────────────────────────────────────
# 一站式入口（同步包装）
# ─────────────────────────────────────────────────────────

def scrape_park(
    park_name: str,
    keywords: list[str],
    city_id: str = "2",
    shop_id: str = None,
    use_open_page: bool = True,
) -> pd.DataFrame:
    """
    对某公园执行完整爬取：搜索 → Playwright 抓评论 → 返回 DataFrame

    参数:
        park_name  公园标识名
        keywords   搜索关键词列表
        city_id    大众点评城市ID（"11"=北京, "1"=上海, "2"=南京 等）
        shop_id    已知可直接传入，跳过搜索
    """
    print(f"\n[大众点评] 开始爬取: {park_name}")

    if use_open_page:
        raw = asyncio.run(_async_fetch_reviews_from_open_page(MAX_REVIEWS_DIANPING))
    else:
        if shop_id is None:
            shop_id = _search_shop_id(keywords, city_id)

        if shop_id is None:
            print(f"  [点评] {park_name} 找不到景区，跳过")
            return pd.DataFrame()

        raw = asyncio.run(_async_fetch_reviews(shop_id, MAX_REVIEWS_DIANPING))

    if not raw:
        print(f"  [点评] {park_name} 没有评论数据")
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    df["park"] = park_name
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    print(f"  [点评] {park_name} 共获取 {len(df)} 条评论")
    return df


# ─────────────────────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    df = scrape_park(
        park_name="测试公园",
        keywords=["圆明园"],
        city_id="11",
        shop_id="G2CauBeHQ9je4IAb",
    )
    if not df.empty:
        print(df[["date", "rating", "content"]].head(5))
        df.to_csv("test_dianping.csv", index=False, encoding="utf-8-sig")
        print("已保存到 test_dianping.csv")
