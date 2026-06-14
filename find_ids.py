"""
find_ids.py — 一次性工具：用浏览器自动查找各平台景区ID
──────────────────────────────────────────────────────
运行一次，把输出的ID填进 config.py，之后爬取直接跳过搜索，稳定很多。

使用：
    python find_ids.py

会弹出 Chromium 浏览器，自动搜索每个公园，把 ID 打印出来。
把结果复制到 config.py 里对应字段：
  ctrip_sight_id   / dianping_shop_id / meituan_poi_id
──────────────────────────────────────────────────────
"""

import re
import asyncio
from playwright.async_api import async_playwright
from config import PARKS


# ─────────────────────────────────────────────────────────
# 查携程景区 sightId
# ─────────────────────────────────────────────────────────

async def _find_ctrip_id(page, keywords: list[str]) -> int | None:
    for kw in keywords:
        try:
            url = f"https://you.ctrip.com/sight/search.html?keyword={kw}"
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)

            # 从 HTML 里找 /sight/{city}/{id}.html 格式的链接
            html = await page.content()
            matches = re.findall(r'/sight/[a-z]+\d*/(\d+)\.html', html)
            # 过滤掉明显不是景区ID的数字（太短）
            valid = [int(m) for m in matches if len(m) >= 4]
            if valid:
                return valid[0]
        except Exception as e:
            print(f"    携程搜索 '{kw}' 出错: {e}")
    return None


# ─────────────────────────────────────────────────────────
# 查大众点评景区 shopId
# ─────────────────────────────────────────────────────────

async def _find_dianping_id(page, keywords: list[str], city_id: str) -> str | None:
    for kw in keywords:
        try:
            # PC 站搜索
            url = f"https://www.dianping.com/search/keyword/{city_id}/0_{kw}"
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)

            html = await page.content()
            # 找 /shop/XXXXXXXX 格式（支持纯数字或字母数字混合 ID）
            matches = re.findall(r'/shop/([A-Za-z0-9]{6,})', html)
            if matches:
                return matches[0]
        except Exception as e:
            print(f"    点评搜索 '{kw}' 出错: {e}")
    return None


# ─────────────────────────────────────────────────────────
# 查美团景区 poiId
# ─────────────────────────────────────────────────────────

MEITUAN_CITY_IDS = {
    "北京": 1, "上海": 2, "广州": 16, "深圳": 7,
    "成都": 15, "武汉": 22, "西安": 37, "杭州": 10,
}

async def _find_meituan_id(page, keywords: list[str], city: str = "北京") -> str | None:
    city_id = MEITUAN_CITY_IDS.get(city, 1)
    for kw in keywords:
        try:
            url = f"https://travel.meituan.com/searchpage?keyword={kw}&cityId={city_id}"
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            html = await page.content()
            # 从 HTML 提取 poiId
            matches = re.findall(r'poiId["\s:=]+["\']?(\d{4,})', html)
            if not matches:
                matches = re.findall(r'/(?:poi|sight)/(\d{4,})', html)
            if matches:
                return matches[0]
        except Exception as e:
            print(f"    美团搜索 '{kw}' 出错: {e}")
    return None


# ─────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("景区 ID 查找工具")
    print("浏览器会自动打开，请勿关闭，等待自动搜索完成")
    print("=" * 60)

    results = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        for park_key, cfg in PARKS.items():
            print(f"\n>>> {park_key}（{cfg['name']}）")
            keywords = cfg["keywords"]
            city_id  = cfg.get("dianping_city_id", "11")
            city     = cfg.get("city", "北京")

            # 已有的 ID 直接跳过搜索
            existing_ct = cfg.get("ctrip_sight_id")
            existing_dp = cfg.get("dianping_shop_id")
            existing_mt = cfg.get("meituan_poi_id")

            if existing_ct:
                ct_id = existing_ct
                print(f"  携程  ctrip_sight_id:   {ct_id}  ← 已在 config 中")
            else:
                print(f"  携程  正在搜索...")
                ct_id = await _find_ctrip_id(page, keywords)
                print(f"  携程  ctrip_sight_id:   {ct_id or '未找到（可手动填写）'}")

            if existing_dp:
                dp_id = existing_dp
                print(f"  点评  dianping_shop_id: {dp_id}  ← 已在 config 中")
            else:
                print(f"  点评  正在搜索...")
                dp_id = await _find_dianping_id(page, keywords, city_id)
                print(f"  点评  dianping_shop_id: {dp_id or '未找到（可手动填写）'}")

            if existing_mt:
                mt_id = existing_mt
                print(f"  美团  meituan_poi_id:   {mt_id}  ← 已在 config 中")
            else:
                print(f"  美团  正在搜索...")
                mt_id = await _find_meituan_id(page, keywords, city)
                print(f"  美团  meituan_poi_id:   {mt_id or '未找到（可手动填写）'}")

            results[park_key] = {
                "ctrip_sight_id": ct_id,
                "dianping_shop_id": dp_id,
                "meituan_poi_id": mt_id,
            }
            await page.wait_for_timeout(1500)

        await browser.close()

    # ── 打印可直接粘贴到 config.py 的内容 ──
    print("\n" + "=" * 60)
    print("把下面的内容对应填进 config.py 每个公园的字典里：")
    print("=" * 60)
    for park_key, ids in results.items():
        print(f"\n  # {park_key}")
        if ids["ctrip_sight_id"]:
            print(f'  "ctrip_sight_id":   {ids["ctrip_sight_id"]},')
        else:
            print(f'  # "ctrip_sight_id":   ???  ← 未自动找到，请手动到 you.ctrip.com 查')
        if ids["dianping_shop_id"]:
            print(f'  "dianping_shop_id": "{ids["dianping_shop_id"]}",')
        else:
            print(f'  # "dianping_shop_id": "???"  ← 未自动找到，请手动到 dianping.com 查')
        if ids["meituan_poi_id"]:
            print(f'  "meituan_poi_id":   "{ids["meituan_poi_id"]}",')
        else:
            print(f'  # "meituan_poi_id":   "???"  ← 未自动找到，请手动到 meituan.com 查')

    print("\n填好 ID 后运行：python main.py --skip-xhs")
    print("（大众点评需登录，运行后会自动弹出浏览器提示）")


# ─────────────────────────────────────────────────────────
# 手动查找说明（找不到自动结果时）
# ─────────────────────────────────────────────────────────

def print_manual_guide():
    print("""
手动查找 ID 方法：

携程 sightId：
  1. 打开 https://you.ctrip.com
  2. 搜索公园名，点进景区页面
  3. URL 形如 https://you.ctrip.com/sight/beijing1/14938.html
  4. 最后那串数字（14938）就是 ctrip_sight_id

大众点评 dianping_shop_id：
  1. 打开 https://www.dianping.com
  2. 搜索公园名，点进景区页面
  3. URL 形如 https://www.dianping.com/shop/H6R5dYe5LFHuWrui
  4. shop/ 后面那串字符就是 dianping_shop_id
     （注意：点评用字符串ID，不是纯数字）
""")


if __name__ == "__main__":
    print_manual_guide()
    asyncio.run(main())
