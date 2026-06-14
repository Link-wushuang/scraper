"""
calculator.py — A4 / A5 分数计算
──────────────────────────────────────────────────────
A4（传播扩散度）：看量
  子指标：发帖量、互动总量（点赞+收藏+评论）、月均活跃度
  计算：各子指标极差标准化 → 加权平均 → A4 (0~100)

A5（文化传播关联度）：看内容质量
  子指标：文化关联内容占比（严格版：排除中性）
  计算：直接用占比 × 100 → A5 (0~100)

最终输出：一个 DataFrame，index = 公园名，列 = 各子指标 + A4 + A5
──────────────────────────────────────────────────────
"""

import pandas as pd
import numpy as np
from classifier import summarize_classification


# ─────────────────────────────────────────────────────────
# A4 子指标权重（可调整）
# ─────────────────────────────────────────────────────────
A4_WEIGHTS = {
    "post_count":       0.40,   # 发帖/评论总量
    "engagement_total": 0.40,   # 互动总量（点赞+收藏+评论+转发）
    "monthly_avg":      0.20,   # 月均发帖量
}


# ─────────────────────────────────────────────────────────
# 极差标准化
# ─────────────────────────────────────────────────────────

def _minmax(series: pd.Series) -> pd.Series:
    """
    极差标准化：(x - min) / (max - min) × 100
    若 max == min（所有公园数值相同），全部给 50 分（避免除以0）
    """
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series([50.0] * len(series), index=series.index)
    return (series - lo) / (hi - lo) * 100


# ─────────────────────────────────────────────────────────
# 从原始数据提取 A4 子指标
# ─────────────────────────────────────────────────────────

def extract_a4_metrics(df_park: pd.DataFrame, park_name: str) -> dict:
    """
    从某公园的原始数据 DataFrame 提取 A4 所需子指标。
    DataFrame 需含字段：likes / saves / comments / date

    返回 dict，key = 子指标名，value = 数值
    """
    if df_park.empty:
        return {
            "park": park_name,
            "post_count": 0,
            "total_likes": 0,
            "total_saves": 0,
            "total_comments": 0,
            "engagement_total": 0,
            "monthly_avg": 0.0,
        }

    post_count = len(df_park)

    # 互动量（各平台字段名可能不同，用 .get 做兼容）
    total_likes    = df_park.get("likes",    pd.Series(dtype=int)).fillna(0).sum()
    total_saves    = df_park.get("saves",    pd.Series(dtype=int)).fillna(0).sum()
    total_comments = df_park.get("comments", pd.Series(dtype=int)).fillna(0).sum()
    engagement_total = total_likes + total_saves + total_comments

    # 月均发帖量（用 date 列）
    if "date" in df_park.columns and df_park["date"].notna().any():
        df_dated = df_park.dropna(subset=["date"])
        date_range = (df_dated["date"].max() - df_dated["date"].min()).days
        months = max(date_range / 30, 1)
        monthly_avg = post_count / months
    else:
        monthly_avg = 0.0

    return {
        "park": park_name,
        "post_count": int(post_count),
        "total_likes": int(total_likes),
        "total_saves": int(total_saves),
        "total_comments": int(total_comments),
        "engagement_total": int(engagement_total),
        "monthly_avg": round(monthly_avg, 2),
    }


# ─────────────────────────────────────────────────────────
# 计算所有公园的 A4/A5 分数
# ─────────────────────────────────────────────────────────

def calculate_scores(
    park_data: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    park_data: {"公园A": df_A, "公园B": df_B, ...}
      df 是已完成分类（含 label 列）的原始数据 DataFrame

    返回：DataFrame，每行一个公园，列包含：
      park | post_count | engagement_total | monthly_avg
      | culture_count | non_culture_count | culture_ratio | culture_ratio_strict
      | a4_raw_post | a4_raw_engagement | a4_raw_monthly
      | a4_score | a5_score
    """
    rows = []
    for park_name, df in park_data.items():
        a4m = extract_a4_metrics(df, park_name)
        a5m = summarize_classification(df)
        row = {**a4m, **a5m}
        row["park"] = park_name
        rows.append(row)

    result = pd.DataFrame(rows).set_index("park")

    # ── A4：标准化各子指标再加权 ──
    result["a4_raw_post"]        = _minmax(result["post_count"])
    result["a4_raw_engagement"]  = _minmax(result["engagement_total"])
    result["a4_raw_monthly"]     = _minmax(result["monthly_avg"])

    result["a4_score"] = (
        result["a4_raw_post"]       * A4_WEIGHTS["post_count"]
        + result["a4_raw_engagement"] * A4_WEIGHTS["engagement_total"]
        + result["a4_raw_monthly"]    * A4_WEIGHTS["monthly_avg"]
    ).round(2)

    # ── A5：文化关联占比（严格版）× 100 ──
    result["a5_score"] = (result["culture_ratio_strict"] * 100).round(2)

    return result


# ─────────────────────────────────────────────────────────
# 打印结果表格
# ─────────────────────────────────────────────────────────

def print_scores(result: pd.DataFrame):
    cols = ["post_count", "engagement_total", "a4_score",
            "culture_count", "culture_ratio_strict", "a5_score"]
    present = [c for c in cols if c in result.columns]
    print("\n" + "="*60)
    print("A4 / A5 分数汇总")
    print("="*60)
    print(result[present].to_string())
    print("="*60)


# ─────────────────────────────────────────────────────────
# 测试
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from classifier import classify_dataframe

    # 造一些假数据测试
    data_a = pd.DataFrame({
        "content": ["历史遗址展陈丰富，有讲解员", "出片拍照好看", "历史文化价值很高",
                    "导览讲述了很多典故", "约会打卡必去", "历史背景介绍详细"],
        "likes": [10, 50, 20, 15, 80, 12],
        "saves": [5, 30, 8, 6, 40, 4],
        "comments": [3, 20, 5, 4, 30, 6],
        "date": pd.date_range("2024-03-01", periods=6, freq="ME"),
    })
    data_b = pd.DataFrame({
        "content": ["拍照出片颜值高", "咖啡好喝散步", "夜景很美约会", "打卡网红地", "好看漂亮"],
        "likes": [200, 150, 300, 250, 180],
        "saves": [100, 80, 150, 120, 90],
        "comments": [50, 40, 70, 60, 45],
        "date": pd.date_range("2024-01-01", periods=5, freq="ME"),
    })

    from classifier import classify_dataframe
    park_data = {
        "公园A（文化型）": classify_dataframe(data_a),
        "公园B（打卡型）": classify_dataframe(data_b),
    }
    result = calculate_scores(park_data)
    print_scores(result)
