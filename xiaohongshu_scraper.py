"""
小红书笔记+评论爬虫（Playwright 浏览器自动化）
──────────────────────────────────────────────────────
流程：
  1. 搜索关键词，滚动加载帖子卡片
  2. 逐个点击卡片 → XHS SPA 打开笔记详情浮层（overlay）
  3. 在浮层中抓取正文 + 滚动加载评论
  4. 关闭浮层 → 回到搜索页 → 继续下一篇

关键设计：
  - 点击卡片而非 page.goto()，避免绕过 SPA 路由导致首页重定向
  - 搜索和详情共用同一标签页（XHS 以浮层展示笔记详情）
  - 7 层 fallback 评论选择器 + 展开按钮自动点击
  - Stealth JS + 随机视口 + 人类模拟滚动

依赖：playwright, pandas, tqdm
──────────────────────────────────────────────────────
"""

import os
import re
import sys
import json
import random
import asyncio
import subprocess
from datetime import datetime, timedelta
from urllib.parse import quote
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from config import DELAY_MIN, DELAY_MAX, MAX_NOTES_XHS, DATE_START, XHS_PHONE
from anti_crawl import random_ua

COOKIE_FILE = Path("xhs_cookies.json")
DEBUG_DIR = Path("debug_html")
XHS_BASE = "https://www.xiaohongshu.com"

_STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    window.chrome = { runtime: {} };
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(params);
}
"""


# ─────────────────────────────────────────────────────────
# Cookie 管理
# ─────────────────────────────────────────────────────────

def _save_cookies(cookies: list, path: Path = COOKIE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


def _load_cookies(path: Path = COOKIE_FILE) -> list | None:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _session_invalid(page_url: str) -> str | None:
    """检测页面是否处于无效状态。返回原因字符串，正常返回 None。"""
    url_lower = page_url.lower()
    if "login" in url_lower or "passport" in url_lower:
        return "跳转到登录页"
    for kw in ("verify", "captcha", "challenge", "blocked", "access-denied"):
        if kw in url_lower:
            return f"疑似风控（URL含 {kw}）"
    if page_url.rstrip("/") == XHS_BASE.rstrip("/"):
        return "被重定向到首页"
    return None


# ─────────────────────────────────────────────────────────
# 登录
# ─────────────────────────────────────────────────────────

async def _login(page):
    """打开首页，等待用户手动完成登录，自动检测并保存 Cookie。"""
    print("\n  [小红书] 即将打开浏览器，请手动完成登录")
    print("  登录完成后脚本将自动检测并继续...")

    await page.goto(XHS_BASE, timeout=30000, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    for _ in range(120):
        current = page.url.lower()
        if "login" in current or "passport" in current:
            await page.wait_for_timeout(1000)
            continue

        # 检查是否有登录弹窗/表单（比检测"登录"文字更精确，避免误判）
        has_login_form = await page.evaluate("""
            () => {
                // 检查是否有登录弹窗、登录表单、手机号输入框等
                const loginModal = document.querySelector(
                    '[class*="login-modal"], [class*="LoginModal"], ' +
                    '[class*="login-container"], [class*="LoginContainer"], ' +
                    '[class*="login-dialog"], [class*="qrcode-login"], ' +
                    'input[placeholder*="手机号"], input[type="tel"]'
                );
                return !!loginModal;
            }
        """)
        if has_login_form:
            await page.wait_for_timeout(1000)
            continue

        # 检查是否有用户头像（已登录的标志）
        is_logged_in = await page.evaluate("""
            () => {
                const avatar = document.querySelector(
                    '[class*="user-avatar"], [class*="UserAvatar"], ' +
                    '[class*="avatar"][class*="side"], img[class*="avatar"]'
                );
                return !!avatar;
            }
        """)
        if is_logged_in or "login" not in current:
            print(f"  [小红书] 检测到登录完成")
            await page.wait_for_timeout(2000)
            cookies = await page.context.cookies()
            _save_cookies(cookies)
            print(f"  [小红书] Cookie 已保存 ({len(cookies)} 个)")
            return

        await page.wait_for_timeout(1000)

    print("  [小红书] 等待登录超时，将尝试继续...")
    cookies = await page.context.cookies()
    _save_cookies(cookies)


async def _verify_session(page) -> bool:
    """通过访问首页检查 Cookie 是否有效。
    只做最基本的检测：未被重定向到登录页即视为有效。
    不做过于严格的头像检测，避免误判。"""
    try:
        await page.goto(XHS_BASE, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        reason = _session_invalid(page.url)
        if reason:
            print(f"  [小红书] session 验证失败: {reason}")
            return False
        # 只要没被重定向到登录页/风控页就算有效
        # 不检查头像元素（XHS 首页结构经常变，容易误判）
        print(f"  [小红书] Cookie 验证通过")
        return True
    except Exception as e:
        print(f"  [小红书] session 验证异常: {e}")
        return False


# ─────────────────────────────────────────────────────────
# 日期解析
# ─────────────────────────────────────────────────────────

_RELATIVE_DATE_RE = re.compile(
    r"(刚刚|(\d+)\s*分钟前|(\d+)\s*小时前|(\d+)\s*天前|昨天\s*([\d:]+)?|前天|今天\s*([\d:]+)?)"
)


def _parse_relative_date(text: str, now: datetime = None) -> str | None:
    """将小红书相对时间文本转换为 'YYYY-MM-DD' 格式。"""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    if now is None:
        now = datetime.now()
    if re.match(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        return text.replace("/", "-")[:10]
    m = _RELATIVE_DATE_RE.search(text)
    if not m:
        return None
    if m.group(1) == "刚刚":
        dt = now
    elif m.group(2):
        dt = now - timedelta(minutes=int(m.group(2)))
    elif m.group(3):
        dt = now - timedelta(hours=int(m.group(3)))
    elif m.group(4):
        dt = now - timedelta(days=int(m.group(4)))
    elif "昨天" in m.group(1):
        dt = now - timedelta(days=1)
    elif m.group(1) == "前天":
        dt = now - timedelta(days=2)
    elif "今天" in m.group(1):
        dt = now
    else:
        return None
    return dt.strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────
# 模拟人类滚动
# ─────────────────────────────────────────────────────────

async def _human_scroll(page, distance: int = None):
    if distance is None:
        distance = random.randint(600, 1200)
    if random.random() < 0.15:
        await page.evaluate("(d) => window.scrollBy(0, -d)", random.randint(50, 150))
        await page.wait_for_timeout(random.randint(200, 500))
    steps = random.randint(3, 6)
    for _ in range(steps):
        step_dist = distance // steps + random.randint(-30, 30)
        await page.evaluate("(d) => window.scrollBy(0, d)", step_dist)
        await page.wait_for_timeout(random.randint(80, 250))
    await page.wait_for_timeout(random.randint(800, 1500))


async def _save_debug_html(page, note_id: str, tag: str = ""):
    """保存当前页面 HTML 便于排查"""
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        label = f"{tag}_" if tag else ""
        filename = DEBUG_DIR / f"{label}{note_id}.html"
        html = await page.content()
        filename.write_text(html, encoding="utf-8")
        print(f"  [debug] HTML 已保存: {filename}")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# 正文提取 JS
# ─────────────────────────────────────────────────────────

_EXTRACT_BODY_JS = """
() => {
    const bodyEl = document.querySelector(
        '#detail-desc, .note-text, [class*="note-content"], ' +
        '[class*="desc"][class*="note"], .content .desc'
    );
    const body = bodyEl ? bodyEl.innerText.trim() : '';

    const titleEl = document.querySelector(
        '#detail-title, .title, [class*="note-title"]'
    );
    const title = titleEl ? titleEl.innerText.trim() : '';

    const dateEl = document.querySelector(
        '.date, [class*="date"], time, [class*="time"], .note-time, .bottom-container span'
    );
    let date = dateEl ? dateEl.innerText.trim() : '';

    const parseNum = (el) => {
        if (!el) return 0;
        const t = el.innerText.trim();
        if (!t || /^[\\u4e00-\\u9fa5]+$/.test(t)) return 0;
        const wm = t.match(/([\\d.]+)\\s*万/);
        if (wm) return Math.round(parseFloat(wm[1]) * 10000);
        return parseInt(t.replace(/[^0-9]/g, '')) || 0;
    };

    const likeEl = document.querySelector('[class*="like-wrapper"] span, [class*="like"] span.count, .like-active span');
    const saveEl = document.querySelector('[class*="collect-wrapper"] span, [class*="collect"] span.count');
    const cmtEl  = document.querySelector('[class*="chat-wrapper"] span, [class*="comment"] span.count');

    return {
        body: body || title,
        title,
        date,
        likes: parseNum(likeEl),
        saves: parseNum(saveEl),
        commentCount: parseNum(cmtEl),
    };
}
"""


# ─────────────────────────────────────────────────────────
# 评论提取 JS（7 层 fallback）
# ─────────────────────────────────────────────────────────

_EXTRACT_COMMENTS_JS = """
() => {
    const comments = [];
    const selectors = [
        '[class*="comment-item"]',
        '[class*="CommentItem"]',
        '.parent-comment',
        '.comment-inner',
        '.note-comment-item',
        'div[class*="comment"]',
        'li[class*="comment"]',
        '[data-v*="comment"]',
    ];

    let items = [];
    for (const sel of selectors) {
        items = document.querySelectorAll(sel);
        if (items.length > 0) break;
    }

    items.forEach(item => {
        let username = '';
        const userEl = item.querySelector(
            '[class*="user"], [class*="name"], [class*="nickname"], [class*="author"]'
        );
        if (userEl) username = userEl.innerText.trim();

        const textEl = item.querySelector(
            '[class*="content"], [class*="text"], [class*="desc"], ' +
            '[class*="note-text"], p, .comment-content, .comment-txt'
        );
        if (!textEl) return;

        let text = textEl.innerText.trim();
        if (!text || text.length < 2) return;
        if (/^[\\s\\p{P}]+$/u.test(text)) return;

        let time = '';
        const timeEl = item.querySelector(
            '[class*="time"], [class*="date"], [class*="Time"], .comment-time'
        );
        if (timeEl) time = timeEl.innerText.trim();

        let likes = 0;
        const likeEl = item.querySelector(
            '[class*="like"] span, [class*="Like"] span, .like-count, [class*="count"]'
        );
        if (likeEl) {
            const m = likeEl.innerText.match(/\\d+/);
            if (m) likes = parseInt(m[0]);
        }

        comments.push({ text, likes, username, time });
    });

    return comments;
}
"""


# ─────────────────────────────────────────────────────────
# 浮层内滚动 + 展开 + 计数
# ─────────────────────────────────────────────────────────

_SCROLL_OVERLAY_JS = """
() => {
    // 尝试找到笔记详情浮层内的可滚动面板（右侧评论区）
    const selectors = [
        '.note-detail-mask [class*="interaction"]',
        '.note-detail-mask [class*="content"]',
        '[class*="NoteDetail"] [class*="content"]',
        '[class*="note-detail"] [class*="scroll"]',
        '[class*="noteDetail"] [class*="right"]',
        '.note-detail-mask',
        '[class*="detail-modal"]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.scrollHeight > el.clientHeight + 10) {
            el.scrollBy(0, 500);
            return true;
        }
    }
    // 兜底：找任何包含 detail 的可滚动元素
    for (const el of document.querySelectorAll('div[class*="detail"], div[class*="note"]')) {
        if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 200) {
            el.scrollBy(0, 500);
            return true;
        }
    }
    window.scrollBy(0, 500);
    return false;
}
"""

_CLICK_EXPAND_JS = """
() => {
    let clicked = 0;
    document.querySelectorAll('*').forEach(el => {
        const t = (el.innerText || '').trim();
        if ((t === '展开' || t === '更多' || t === '更多回复' ||
             t === '查看全部' || t === '展开全部' ||
             t === '展开更多' || t === '... 展开') &&
            el.offsetHeight > 0 && el.offsetWidth > 0) {
            try { el.click(); clicked++; } catch(e) {}
        }
    });
    return clicked;
}
"""

_COUNT_COMMENTS_JS = """
() => document.querySelectorAll(
    '[class*="comment-item"], [class*="CommentItem"], ' +
    '.parent-comment, .comment-inner, ' +
    '.note-comment-item, div[class*="comment"]'
).length
"""


# ─────────────────────────────────────────────────────────
# 浮层关闭 / 搜索页恢复
# ─────────────────────────────────────────────────────────

async def _close_overlay(page):
    """关闭笔记详情浮层，回到搜索结果页"""
    # 方法1: Escape 键
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    if "/search_result" in page.url:
        return

    # 方法2: JS 点击关闭按钮（不用 Playwright click 避免导航等待超时）
    closed = await page.evaluate("""
        () => {
            const selectors = [
                '[class*="close-circle"]', '[class*="close-btn"]',
                'button[class*="close"]', '[class*="Close"]',
                '.note-detail-mask [class*="close"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.offsetHeight > 0) {
                    el.click();
                    return true;
                }
            }
            return false;
        }
    """)
    if closed:
        await page.wait_for_timeout(800)
        if "/search_result" in page.url:
            return

    # 方法3: 浏览器后退
    try:
        await page.go_back()
        await page.wait_for_timeout(800)
    except Exception:
        pass


async def _recover_to_search(page, search_url: str):
    """确保页面回到搜索结果页"""
    if "/search_result" in page.url:
        return
    try:
        await page.go_back()
        await page.wait_for_timeout(1000)
        if "/search_result" in page.url:
            return
    except Exception:
        pass
    # 兜底：直接导航回搜索页
    try:
        await page.goto(search_url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# 核心：搜索 → 点击卡片 → 浮层抓取 → 关闭 → 循环
# ─────────────────────────────────────────────────────────

# 找当前 DOM 中所有笔记卡片链接
_FIND_CARDS_JS = """
() => {
    const results = [];
    const seen = new Set();
    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/\\/(?:explore|discovery\\/item)\\/([a-f0-9]{18,})/i);
        if (m && !seen.has(m[1])) {
            seen.add(m[1]);
            const rect = a.getBoundingClientRect();
            results.push({
                id: m[1],
                href: href,
                x: rect.x + rect.width / 2,
                y: rect.y + rect.height / 2,
                w: rect.width,
                h: rect.height
            });
        }
    });
    return results;
}
"""


async def _search_click_scrape(page, keyword: str, max_count: int) -> list[dict]:
    """
    搜索关键词，滚动加载帖子卡片，逐个点击打开详情浮层，
    抓取正文+评论后关闭浮层，继续下一篇。

    核心思路：点击 <a> 元素触发 XHS SPA 路由，而非 page.goto()。
    """
    search_url = (
        f"{XHS_BASE}/search_result"
        f"?keyword={quote(keyword)}&source=web_explore_feed&type=51"
    )
    print(f"  [小红书] 搜索: {keyword}")
    try:
        await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
    except PlaywrightTimeout:
        print(f"  [小红书] 搜索页加载超时")
        return []

    await page.wait_for_timeout(random.randint(
        int(DELAY_MIN * 1000), int(DELAY_MAX * 1000)))

    processed = set()      # 已处理的 noteId
    all_rows = []
    no_new_rounds = 0      # 连续未发现新卡片的滚动轮数
    consecutive_fails = 0  # 连续抓取失败计数
    success_count = 0

    with tqdm(total=max_count, desc=f"  搜索+抓取/{keyword}", unit="篇") as pbar:
        while len(processed) < max_count and consecutive_fails < 10:

            # ── 扫描当前 DOM 中的帖子链接 ──
            cards = await page.evaluate(_FIND_CARDS_JS)
            new_cards = [c for c in cards
                         if c["id"] not in processed
                         and re.match(r"^[a-f0-9]{18,24}$", c["id"])]

            if not new_cards:
                no_new_rounds += 1
                if no_new_rounds >= 5:
                    # 保存调试 HTML 帮助分析
                    DEBUG_DIR.mkdir(exist_ok=True)
                    html = await page.content()
                    (DEBUG_DIR / f"search_{keyword}.html").write_text(
                        html, encoding="utf-8")
                    print(f"\n  [debug] 搜索页 HTML 已保存到 {DEBUG_DIR}/search_{keyword}.html")
                    break
                await _human_scroll(page)
                await page.wait_for_timeout(random.randint(
                    int(DELAY_MIN * 1000), int(DELAY_MAX * 1000)))
                continue

            # 第一轮调试：打印发现的卡片信息
            if not processed:
                print(f"\n  [debug] 发现 {len(new_cards)} 张卡片，"
                      f"示例 href: {new_cards[0].get('href','?')}")

            no_new_rounds = 0

            for card in new_cards:
                if len(processed) >= max_count or consecutive_fails >= 10:
                    break

                note_id = card["id"]
                card_href = card.get("href", "")
                processed.add(note_id)

                try:
                    # ── 滚动到卡片并刷新坐标 ──
                    # <a> 标签本身可能是 0x0 隐藏元素，需要往上找可见的父级容器
                    box = await page.evaluate("""
                        (info) => {
                            let link = document.querySelector('a[href="' + info.href + '"]');
                            if (!link) {
                                const all = document.querySelectorAll('a[href*="' + info.id + '"]');
                                if (all.length > 0) link = all[0];
                            }
                            if (!link) {
                                return { error: 'not_found' };
                            }
                            // 往上找到第一个有合理尺寸的可见父元素
                            let target = link;
                            let el = link;
                            for (let i = 0; i < 8; i++) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 50 && r.height > 50) {
                                    target = el;
                                    break;
                                }
                                if (!el.parentElement) break;
                                el = el.parentElement;
                            }
                            target.scrollIntoView({ block: 'center' });
                            const rect = target.getBoundingClientRect();
                            return {
                                x: rect.x + rect.width / 2,
                                y: rect.y + rect.height / 2,
                                w: rect.width, h: rect.height
                            };
                        }
                    """, {"href": card_href, "id": note_id})

                    if not box or box.get("error"):
                        print(f"\n  [debug] {note_id[:10]}... 卡片未找到")
                        if len(processed) <= 2:
                            await _save_debug_html(page, note_id, "card_miss")
                        continue

                    if box["w"] < 5 or box["h"] < 5:
                        print(f"\n  [debug] {note_id[:10]}... 尺寸太小 "
                              f"({box['w']:.0f}x{box['h']:.0f})")
                        continue

                    await page.wait_for_timeout(random.randint(500, 900))

                    url_before = page.url
                    await page.mouse.click(box["x"], box["y"])
                    await page.wait_for_timeout(random.randint(2500, 4000))

                    # ── 检测浮层是否打开 ──
                    cur_url = page.url
                    reason = _session_invalid(cur_url)
                    if reason:
                        print(f"\n  [小红书] {note_id[:10]}... {reason}")
                        consecutive_fails += 1
                        await _recover_to_search(page, search_url)
                        continue

                    # 判断浮层是否打开：URL 变化 或 页面出现详情元素
                    url_changed = cur_url != url_before
                    has_detail = await page.evaluate("""
                        () => !!document.querySelector(
                            '#detail-desc, .note-text, [class*="note-content"], ' +
                            '[class*="note-detail"], .note-detail-mask, ' +
                            '[class*="NoteDetail"], [id*="noteContainer"]'
                        )
                    """)
                    overlay_ok = url_changed or has_detail

                    if not overlay_ok:
                        # 调试：打印当前状态帮助定位
                        print(f"\n  [debug] {note_id[:10]}... 浮层未打开"
                              f" url_changed={url_changed} has_detail={has_detail}"
                              f" url={cur_url[:60]}")
                        await _save_debug_html(page, note_id, "no_overlay")
                        consecutive_fails += 1
                        continue

                    # ── 抓取正文 ──
                    detail = await page.evaluate(_EXTRACT_BODY_JS)
                    body = detail.get("body", "")
                    post_date = detail.get("date", "")

                    if body:
                        all_rows.append({
                            "platform": "小红书",
                            "content": body,
                            "date": post_date,
                            "rating": 0,
                            "likes": detail.get("likes", 0),
                            "saves": detail.get("saves", 0),
                            "comments": detail.get("commentCount", 0),
                            "source": "post",
                            "username": "",
                        })

                    # ── 滚动浮层加载评论 ──
                    last_cmt_count = 0
                    no_new_cmt = 0
                    for _ in range(20):
                        await page.evaluate(_SCROLL_OVERLAY_JS)
                        await page.wait_for_timeout(random.randint(500, 900))
                        await page.evaluate(_CLICK_EXPAND_JS)
                        await page.wait_for_timeout(random.randint(300, 600))

                        cur_cmt = await page.evaluate(_COUNT_COMMENTS_JS)
                        if cur_cmt > last_cmt_count:
                            last_cmt_count = cur_cmt
                            no_new_cmt = 0
                        else:
                            no_new_cmt += 1
                            if no_new_cmt >= 3:
                                break

                    # ── 提取评论 ──
                    comments = await page.evaluate(_EXTRACT_COMMENTS_JS)

                    if body and not comments:
                        await _save_debug_html(page, note_id, "no_comments")

                    for cmt in comments:
                        text = cmt.get("text", "").strip()
                        if text and len(text) >= 2:
                            all_rows.append({
                                "platform": "小红书",
                                "content": text,
                                "date": cmt.get("time", "") or post_date,
                                "rating": 0,
                                "likes": cmt.get("likes", 0),
                                "saves": 0,
                                "comments": 0,
                                "source": "comment",
                                "username": cmt.get("username", ""),
                            })

                    # ── 统计 ──
                    if body or comments:
                        consecutive_fails = 0
                        success_count += 1
                        pbar.update(1)
                        pbar.set_postfix({
                            "正文": sum(1 for r in all_rows if r["source"] == "post"),
                            "评论": sum(1 for r in all_rows if r["source"] == "comment"),
                        })
                    else:
                        consecutive_fails += 1

                    # ── 关闭浮层 ──
                    await _close_overlay(page)
                    await page.wait_for_timeout(random.randint(300, 600))

                    if "/search_result" not in page.url:
                        await _recover_to_search(page, search_url)

                    # 帖间随机延迟
                    await page.wait_for_timeout(random.randint(
                        int(DELAY_MIN * 1000), int(DELAY_MAX * 1000)))

                except PlaywrightTimeout:
                    print(f"\n  [小红书] {note_id[:10]}... 超时")
                    consecutive_fails += 1
                    await _close_overlay(page)
                    await _recover_to_search(page, search_url)
                except Exception as e:
                    print(f"\n  [小红书] {note_id[:10]}... 异常: {type(e).__name__}: {e}")
                    consecutive_fails += 1
                    try:
                        await _close_overlay(page)
                        await _recover_to_search(page, search_url)
                    except Exception:
                        pass

            # 处理完当前批次的卡片后，滚动加载更多
            await _human_scroll(page)

    if consecutive_fails >= 10:
        print(f"\n  [小红书] 连续失败过多，停止")
        print(f"  建议：删除 xhs_cookies.json 重新登录")

    skip_count = len(processed) - success_count
    if skip_count > 0:
        print(f"  [小红书] 抓取完成: 成功 {success_count}/{len(processed)}，跳过 {skip_count}")

    return all_rows


# ─────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────

async def _async_scrape_park(
    park_name: str,
    keywords: list[str],
    max_count: int = MAX_NOTES_XHS,
    use_cdp: bool = False,
) -> pd.DataFrame:

    async with async_playwright() as pw:
        own_browser = True   # 结束后是否关闭浏览器
        context = None
        browser = None

        if use_cdp:
            # ── 模式1: 连接到用户已打开的 Edge（CDP）──
            # 先强制杀掉所有 Edge 后台进程，再用调试端口启动
            print("  [小红书] 正在关闭所有 Edge 进程...")
            subprocess.run(["taskkill", "/f", "/im", "msedge.exe"],
                           capture_output=True)
            await asyncio.sleep(2)

            # 启动 Edge（带调试端口）
            edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            if not os.path.isfile(edge_exe):
                edge_exe = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
            subprocess.Popen([edge_exe, "--remote-debugging-port=9222"])
            print("  [小红书] 已启动 Edge（调试端口 9222），等待就绪...")
            await asyncio.sleep(3)

            connected = False
            for addr in ["http://127.0.0.1:9222", "http://localhost:9222"]:
                try:
                    browser = await pw.chromium.connect_over_cdp(addr)
                    print(f"  [小红书] 已连接到 Edge ({addr})")
                    connected = True
                    break
                except Exception:
                    continue

            if not connected:
                print(f"\n  [小红书] 仍无法连接，请手动在任务管理器结束所有 Edge 进程后重试")
                return pd.DataFrame()

            own_browser = False
            context = browser.contexts[0]
            page = await context.new_page()

        elif "--profile" in sys.argv:
            # ── 模式2: 用 Edge 已有的用户数据启动（继承全部 Cookie）──
            edge_user_data = os.path.expandvars(
                r"%LOCALAPPDATA%\Microsoft\Edge\User Data"
            )
            if not os.path.isdir(edge_user_data):
                print(f"  [小红书] 未找到 Edge 用户数据目录: {edge_user_data}")
                return pd.DataFrame()

            # 先杀掉所有 Edge 后台进程，否则用户数据目录被锁
            print("  [小红书] 正在关闭所有 Edge 进程...")
            subprocess.run(["taskkill", "/f", "/im", "msedge.exe"],
                           capture_output=True)
            await asyncio.sleep(2)

            print(f"  [小红书] 使用 Edge 用户配置文件启动（继承登录状态）")
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=edge_user_data,
                channel="msedge",
                headless=False,
                args=["--profile-directory=Default"],
                viewport={
                    "width": random.choice([1280, 1366, 1440, 1536]),
                    "height": random.choice([720, 768, 800, 900]),
                },
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            await context.add_init_script(_STEALTH_JS)
            page = context.pages[0] if context.pages else await context.new_page()

        else:
            # ── 模式3: 启动全新浏览器（默认）──
            browser = await pw.chromium.launch(channel="msedge", headless=False)
            context = await browser.new_context(
                viewport={
                    "width": random.choice([1280, 1366, 1440, 1536]),
                    "height": random.choice([720, 768, 800, 900]),
                },
                user_agent=random_ua(mobile=False),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            await context.add_init_script(_STEALTH_JS)

            # Cookie / 登录
            existing = _load_cookies()
            need_login = not existing

            if existing:
                await context.add_cookies(existing)
                print("  [小红书] 已加载 Cookie")

            page = await context.new_page()

            if need_login:
                await _login(page)
            else:
                print("  [小红书] 验证 Cookie 是否有效...")
                if not await _verify_session(page):
                    print("  [小红书] Cookie 可能失效，将尝试重新登录")
                    await _login(page)

        # ── 逐关键词搜索 + 点击抓取 ──
        all_rows = []
        per_kw = max(max_count // len(keywords), 20)
        for kw in keywords:
            rows = await _search_click_scrape(page, kw, per_kw)
            all_rows.extend(rows)
            if len(all_rows) >= max_count:
                break
            await page.wait_for_timeout(random.randint(
                int(DELAY_MIN * 1000), int(DELAY_MAX * 1000)))

        # 保存 Cookie（仅新浏览器模式）
        if own_browser and browser:
            updated = await context.cookies()
            _save_cookies(updated)

        await page.close()
        if own_browser:
            if browser:
                await browser.close()       # 模式3: 新浏览器
            else:
                await context.close()       # 模式2: persistent context
        # CDP 模式：只关工作标签页，不关用户的浏览器

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["park"] = park_name
    now = datetime.now()
    df["date"] = df["date"].apply(lambda x: _parse_relative_date(str(x), now) or x)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    n_posts = (df["source"] == "post").sum()
    n_comments = (df["source"] == "comment").sum()
    print(f"  [小红书] {park_name}: {n_posts} 篇正文 + {n_comments} 条评论 = {len(df)} 行")
    return df


def scrape_park(
    park_name: str,
    keywords: list[str],
    max_count: int = MAX_NOTES_XHS,
    enrich: bool = True,
    use_cdp: bool = False,
) -> pd.DataFrame:
    """同步入口。use_cdp=True 时连接已打开的 Edge 而非启动新浏览器。"""
    if not use_cdp and "--profile" not in sys.argv and not XHS_PHONE and not COOKIE_FILE.exists():
        print("  [小红书] 未配置 XHS_PHONE 且无 Cookie，将弹出浏览器请手动登录")
    return asyncio.run(_async_scrape_park(park_name, keywords, max_count, use_cdp=use_cdp))


# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    use_cdp = "--connect" in sys.argv
    use_profile = "--profile" in sys.argv

    if use_cdp:
        print("=" * 50)
        print("  连接模式：自动启动 Edge（调试端口）")
        print("  注意：会先关闭所有 Edge 窗口")
        print("=" * 50)
    elif use_profile:
        print("=" * 50)
        print("  配置文件模式：使用你 Edge 已有的登录状态")
        print("  注意：会先关闭所有 Edge 窗口")
        print("=" * 50)

    df = scrape_park(
        park_name="测试公园",
        keywords=["圆明园"],
        max_count=20,
        use_cdp=use_cdp,
    )
    if not df.empty:
        print(df[["source", "content", "likes"]].head(10))
        df.to_csv("test_xhs.csv", index=False, encoding="utf-8-sig")
        print("已保存到 test_xhs.csv")
