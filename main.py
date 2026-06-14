"""
main.py — 主入口脚本
──────────────────────────────────────────────────────
运行流程：
  1. 读取 config.py 里的公园列表
  2. 依次用携程、大众点评、小红书爬虫抓取数据
  3. 合并数据 → 关键词分类（A5 准备）
  4. 计算 A4 / A5 分数
  5. 生成词云、柱状图、散点图
  6. 导出 Excel（原始数据 + 分数汇总）

使用方式：
  python main.py                    # 全量运行
  python main.py --only-analysis    # 跳过爬取，只分析已有 CSV
  python main.py --skip-xhs         # 跳过小红书（不需要登录）
  python main.py --park 公园A 公园B  # 只跑指定公园
──────────────────────────────────────────────────────
"""

import os
import sys
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 本地模块 ──
from config import PARKS, DATA_DIR, OUTPUT_DIR
import ctrip_scraper
import dianping_scraper
import meituan_scraper
import xiaohongshu_scraper

PLATFORMS = ("ctrip", "dianping", "meituan", "xhs")


# ─────────────────────────────────────────────────────────
# 初始化目录
# ─────────────────────────────────────────────────────────

def _init_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────
# 爬取单个公园（三平台合并）
# ─────────────────────────────────────────────────────────

def scrape_park(park_key: str, park_cfg: dict, skip_xhs: bool = False) -> pd.DataFrame:
    """爬取一个公园的全平台数据，返回合并 DataFrame"""
    name     = park_cfg["name"]
    keywords = park_cfg["keywords"]

    frames = []

    # ── 携程 ──
    try:
        ct_sight_id = park_cfg.get("ctrip_sight_id") or None
        df_ct = ctrip_scraper.scrape_park(
            park_key, keywords,
            sight_id=ct_sight_id,
            ctrip_url=park_cfg.get("ctrip_url") or None,
        )
        if not df_ct.empty:
            frames.append(df_ct)
            _save_raw(df_ct, park_key, "ctrip")
    except Exception as e:
        print(f"  [携程] {park_key} 爬取异常: {e}")

    # ── 大众点评 ──
    try:
        dp_shop_id = park_cfg.get("dianping_shop_id") or None
        dp_city    = park_cfg.get("dianping_city_id", "11")
        df_dp = dianping_scraper.scrape_park(park_key, keywords, city_id=dp_city, shop_id=dp_shop_id)
        if not df_dp.empty:
            frames.append(df_dp)
            _save_raw(df_dp, park_key, "dianping")
    except Exception as e:
        print(f"  [点评] {park_key} 爬取异常: {e}")

    # ── 美团 ──
    try:
        mt_poi_id = park_cfg.get("meituan_poi_id") or None
        df_mt = meituan_scraper.scrape_park(
            park_key, keywords,
            city=park_cfg.get("city", "北京"),
            meituan_poi_id=mt_poi_id,
        )
        if not df_mt.empty:
            frames.append(df_mt)
            _save_raw(df_mt, park_key, "meituan")
    except Exception as e:
        print(f"  [美团] {park_key} 爬取异常: {e}")

    # ── 小红书 ──
    if not skip_xhs:
        try:
            df_xhs = xiaohongshu_scraper.scrape_park(park_key, keywords)
            if not df_xhs.empty:
                frames.append(df_xhs)
                _save_raw(df_xhs, park_key, "xhs")
        except Exception as e:
            print(f"  [小红书] {park_key} 爬取异常: {e}")
    else:
        print(f"  [小红书] 已跳过 (--skip-xhs)")

    if not frames:
        print(f"  ⚠️  {park_key} 三个平台均无数据")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    print(f"  ✅ {park_key} 合并后共 {len(combined)} 条")
    return combined


def _save_raw(df: pd.DataFrame, park_key: str, platform: str):
    """保存原始数据 CSV"""
    path = os.path.join(DATA_DIR, f"{park_key}_{platform}.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  已保存原始数据: {path}")


# ─────────────────────────────────────────────────────────
# 读取已有 CSV（--only-analysis 模式）
# ─────────────────────────────────────────────────────────

def load_existing_data(park_key: str) -> pd.DataFrame:
    """从 data/ 目录读取该公园所有平台的原始 CSV 并合并"""
    data_path = Path(DATA_DIR)
    files = list(data_path.glob(f"{park_key}_*.csv"))
    if not files:
        print(f"  ⚠️  {park_key} 在 {DATA_DIR}/ 找不到任何 CSV，跳过")
        return pd.DataFrame()
    frames = [pd.read_csv(f, encoding="utf-8-sig") for f in files]
    combined = pd.concat(frames, ignore_index=True)
    if "date" in combined.columns:
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
    print(f"  [本地] {park_key} 读取 {len(files)} 个文件，共 {len(combined)} 条")
    return combined


# ─────────────────────────────────────────────────────────
# 导出 Excel
# ─────────────────────────────────────────────────────────

def _with_display_names(
    park_data: dict[str, pd.DataFrame],
    park_configs: dict[str, dict],
) -> dict[str, pd.DataFrame]:
    """Use config.py park names for charts, score tables, and Excel sheets."""
    return {
        park_configs.get(park_key, {}).get("name", park_key): df
        for park_key, df in park_data.items()
    }


def export_excel(park_data: dict[str, pd.DataFrame], scores: pd.DataFrame):
    """
    导出 Excel：
      - Sheet "汇总得分"   → A4/A5 分数及子指标
      - Sheet "公园A_原始" → 每个公园的原始+分类数据
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(OUTPUT_DIR, f"park_analysis_{timestamp}.xlsx")

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        # 汇总得分
        scores.to_excel(writer, sheet_name="汇总得分")

        # 各公园原始数据
        for park_key, df in park_data.items():
            if df.empty:
                continue
            sheet_name = park_key[:25]  # Excel sheet名最长31字符
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"\n✅ Excel 已导出: {path}")
    return path


# ─────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────

def _selected_platforms(platform: str) -> set[str]:
    return set(PLATFORMS) if platform == "all" else {platform}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Park crawler and analysis")
    parser.add_argument("--only-analysis", action="store_true",
                        help="skip crawling and analyze existing CSV files")
    parser.add_argument("--skip-xhs", action="store_true",
                        help="skip Xiaohongshu when running all platforms")
    parser.add_argument("--park", nargs="+", metavar="PARK_KEY",
                        help="only process specified parks")
    parser.add_argument("--platform", choices=("all", *PLATFORMS), default="all",
                        help="crawl one platform, or all platforms by default")
    parser.add_argument("--profile", action="store_true",
                        help="use Edge profile mode for xhs or ctrip")
    return parser


def _scrape_single_platform(park_key: str, park_cfg: dict, platform: str) -> pd.DataFrame:
    keywords = park_cfg["keywords"]

    if platform == "ctrip":
        df = ctrip_scraper.scrape_park(
            park_key, keywords,
            sight_id=park_cfg.get("ctrip_sight_id") or None,
            ctrip_url=park_cfg.get("ctrip_url") or None,
        )
    elif platform == "dianping":
        df = dianping_scraper.scrape_park(
            park_key, keywords,
            city_id=park_cfg.get("dianping_city_id", "11"),
            shop_id=park_cfg.get("dianping_shop_id") or None,
        )
    elif platform == "meituan":
        df = meituan_scraper.scrape_park(
            park_key, keywords,
            city=park_cfg.get("city", "鍖椾含"),
            meituan_poi_id=park_cfg.get("meituan_poi_id") or None,
        )
    elif platform == "xhs":
        df = xiaohongshu_scraper.scrape_park(park_key, keywords)
    else:
        raise ValueError(f"Unknown platform: {platform}")

    if not df.empty:
        _save_raw(df, park_key, platform)
    return df


def main():
    parser = argparse.ArgumentParser(description="公园文化传播爬虫与分析")
    parser.add_argument("--only-analysis", action="store_true",
                        help="跳过爬取，直接分析 data/ 目录里已有的 CSV")
    parser.add_argument("--skip-xhs", action="store_true",
                        help="跳过小红书（不需要登录，速度更快）")
    parser.add_argument("--park", nargs="+", metavar="PARK_KEY",
                        help="只处理指定公园（如 --park 公园A 公园B）")
    parser.add_argument("--platform", choices=("all", *PLATFORMS), default="all",
                        help="crawl one platform, or all platforms by default")
    parser.add_argument("--profile", action="store_true",
                        help="use Edge profile mode for xhs or ctrip")
    args = parser.parse_args()

    _init_dirs()

    # 确定要处理的公园
    target_parks = {
        k: v for k, v in PARKS.items()
        if args.park is None or k in args.park
    }
    if not target_parks:
        print("❌ 未找到指定公园，请检查 --park 参数或 config.py 里的 PARKS 配置")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"开始处理 {len(target_parks)} 个公园")
    print(f"{'='*60}")

    # ── 数据采集 ──
    park_data_raw: dict[str, pd.DataFrame] = {}
    for park_key, park_cfg in target_parks.items():
        print(f"\n>>> 公园: {park_key} ({park_cfg['name']})")
        if args.only_analysis:
            df = load_existing_data(park_key)
        else:
            if args.platform == "all":
                df = scrape_park(park_key, park_cfg, skip_xhs=args.skip_xhs)
            else:
                df = _scrape_single_platform(park_key, park_cfg, args.platform)
        park_data_raw[park_key] = df

    park_data_display = _with_display_names(park_data_raw, target_parks)

    # ── 关键词分类（A5 准备）──
    print(f"\n{'='*60}")
    print("开始内容分类（用于 A5）...")
    from classifier import classify_dataframe
    from calculator import calculate_scores, print_scores
    from charts import generate_all_wordclouds, plot_bar_comparison, plot_scatter

    park_data_classified: dict[str, pd.DataFrame] = {}
    for park_key, df in park_data_display.items():
        if df.empty:
            park_data_classified[park_key] = df
        else:
            park_data_classified[park_key] = classify_dataframe(df, text_col="content")

    # ── 计算 A4/A5 ──
    print(f"\n{'='*60}")
    print("计算 A4 / A5 分数...")
    scores = calculate_scores(park_data_classified)
    print_scores(scores)

    # ── 可视化（按平台分文件夹）──
    print(f"\n{'='*60}")
    print("生成可视化图表...")

    _PLAT_FOLDERS = {"携程": "ctrip", "大众点评": "dianping", "美团": "meituan", "小红书": "xhs"}
    for plat_label, plat_folder in _PLAT_FOLDERS.items():
        plat_data = {}
        for park_name, df in park_data_classified.items():
            if df.empty or "platform" not in df.columns:
                continue
            subset = df[df["platform"] == plat_label]
            if not subset.empty:
                plat_data[park_name] = subset
        if plat_data:
            plat_dir = os.path.join(OUTPUT_DIR, plat_folder)
            print(f"\n  [{plat_label}] 图表 -> {plat_dir}/")
            generate_all_wordclouds(plat_data, output_dir=plat_dir)
            plat_scores = calculate_scores(plat_data)
            plot_bar_comparison(plat_scores, output_dir=plat_dir)
            plot_scatter(plat_scores, output_dir=plat_dir)

    # 汇总图表（全平台合并）
    print(f"\n  [汇总] 图表 -> {OUTPUT_DIR}/")
    generate_all_wordclouds(park_data_classified, output_dir=OUTPUT_DIR)
    plot_bar_comparison(scores, output_dir=OUTPUT_DIR)
    plot_scatter(scores, output_dir=OUTPUT_DIR)

    # ── 导出 Excel ──
    print(f"\n{'='*60}")
    print("导出 Excel...")
    export_excel(park_data_classified, scores)

    print(f"\n🎉 全部完成！结果保存在 {OUTPUT_DIR}/ 目录")


if __name__ == "__main__":
    main()
