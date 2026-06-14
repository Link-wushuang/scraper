"""
classifier.py — 内容分类模块
──────────────────────────────────────────────────────
对每条帖子/评论做二分类：
  "culture"      → 文化关联内容（对 A5 有贡献）
  "non_culture"  → 非文化/表层传播
  "neutral"      → 中性（两类关键词均未命中）

分类规则（默认）：
  设 C = 文化词命中数，N = 非文化词命中数
  · C > N  → culture
  · N > C  → non_culture
  · C == N (含 0==0) → neutral

──────────────────────────────────────────────────────
"""

import pandas as pd
from keywords import CULTURE_WORDS, NON_CULTURE_WORDS, count_keywords


# ─────────────────────────────────────────────────────────
# 单条分类
# ─────────────────────────────────────────────────────────

def classify_text(text: str) -> dict:
    """
    对单条文本做分类。
    返回 dict，包含：
      label         : "culture" | "non_culture" | "neutral"
      culture_count : 文化词命中数
      non_culture_count : 非文化词命中数
      culture_hits  : 命中的文化词列表
      non_culture_hits : 命中的非文化词列表
    """
    c_count, c_hits = count_keywords(text, CULTURE_WORDS)
    n_count, n_hits = count_keywords(text, NON_CULTURE_WORDS)

    if c_count > n_count:
        label = "culture"
    elif n_count > c_count:
        label = "non_culture"
    else:
        label = "neutral"

    return {
        "label": label,
        "culture_count": c_count,
        "non_culture_count": n_count,
        "culture_hits": "|".join(c_hits),
        "non_culture_hits": "|".join(n_hits),
    }


# ─────────────────────────────────────────────────────────
# 批量分类（DataFrame）
# ─────────────────────────────────────────────────────────

def classify_dataframe(df: pd.DataFrame, text_col: str = "content") -> pd.DataFrame:
    """
    对 DataFrame 的 text_col 列批量做分类，
    返回附加了分类字段的新 DataFrame。

    新增列：label / culture_count / non_culture_count / culture_hits / non_culture_hits
    """
    if df.empty or text_col not in df.columns:
        return df

    results = df[text_col].fillna("").apply(classify_text)
    result_df = pd.DataFrame(list(results))
    return pd.concat([df.reset_index(drop=True), result_df], axis=1)


# ─────────────────────────────────────────────────────────
# 分类汇总统计（用于 A5）
# ─────────────────────────────────────────────────────────

def summarize_classification(df: pd.DataFrame) -> dict:
    """
    对已分类的 DataFrame 做汇总。
    返回 dict：
      total            总条数
      culture_count    文化关联条数
      non_culture_count 非文化条数
      neutral_count    中性条数
      culture_ratio    文化占比（culture / total）
      culture_ratio_strict  严格文化占比（culture / (culture + non_culture)）
    """
    if df.empty or "label" not in df.columns:
        return {k: 0 for k in ["total", "culture_count", "non_culture_count",
                                "neutral_count", "culture_ratio", "culture_ratio_strict"]}

    total = len(df)
    c = (df["label"] == "culture").sum()
    n = (df["label"] == "non_culture").sum()
    neu = (df["label"] == "neutral").sum()
    base_strict = c + n if (c + n) > 0 else 1

    return {
        "total": total,
        "culture_count": int(c),
        "non_culture_count": int(n),
        "neutral_count": int(neu),
        "culture_ratio": round(c / total, 4) if total > 0 else 0,
        "culture_ratio_strict": round(c / base_strict, 4),
    }


# ─────────────────────────────────────────────────────────
# 测试
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    samples = [
        "这里曾是明代的宫殿遗址，展陈很丰富，导览牌讲解了很多历史背景",
        "超级适合打卡！夜景绝美，约会必去，咖啡也好喝",
        "公园很大，停车方便",
        "考古发掘出土了很多文物，遗址保护得很好，还有专业讲解员",
        "闺蜜来这里拍照出片，氛围感拉满",
    ]
    for s in samples:
        r = classify_text(s)
        print(f"[{r['label']:12s}] C={r['culture_count']} N={r['non_culture_count']}  {s[:30]}...")
