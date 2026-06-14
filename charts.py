"""
charts.py — 可视化模块
──────────────────────────────────────────────────────
提供三类图表：
  1. 词云（每个公园一张）
  2. A4/A5 双柱状对比图
  3. A4-A5 散点图（核心输出图）

依赖：matplotlib, wordcloud, jieba, pandas
──────────────────────────────────────────────────────
"""

import os
import re
import warnings
import jieba
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from wordcloud import WordCloud
from pathlib import Path

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────
# 中文字体（matplotlib）
# 自动寻找系统中可用的中文字体
# ─────────────────────────────────────────────────────────

def _get_chinese_font() -> str:
    """返回可用中文字体路径（找不到返回空字符串）"""
    candidates = [
        # Windows
        "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",   # 黑体
        "C:/Windows/Fonts/simsun.ttc",   # 宋体
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode MS.ttf",
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    # 最后尝试从系统字体管理器找
    for f in fm.findSystemFonts():
        if any(k in f.lower() for k in ["msyh", "simhei", "simsun", "pingfang",
                                          "noto", "cjk", "wqy", "yahei"]):
            return f
    return ""


_FONT_PATH = _get_chinese_font()

def _set_mpl_font():
    """设置 matplotlib 中文字体"""
    if _FONT_PATH:
        prop = fm.FontProperties(fname=_FONT_PATH)
        plt.rcParams["font.family"] = prop.get_name()
    else:
        # 降级：用 sans-serif 并忽略中文乱码
        plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False


# ─────────────────────────────────────────────────────────
# 1. 词云
# ─────────────────────────────────────────────────────────

# 停用词（生成词云时忽略）
_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "都",
    "而", "及", "与", "但", "很", "还", "也", "不", "这",
    "个", "上", "到", "去", "他", "她", "们", "一", "那",
    "啊", "吧", "哦", "嗯", "哈", "呢", "啦", "哇", "诶",
    "真的", "觉得", "感觉", "因为", "所以", "如果", "可以",
    "公园", "景区", "景点",  # 排除公园本身名词，避免词云被它占满
}


def generate_wordcloud(
    df: pd.DataFrame,
    park_name: str,
    output_dir: str = "output",
    text_col: str = "content",
    max_words: int = 80,
) -> str:
    """
    为某公园生成词云图。
    返回保存路径。
    """
    if df.empty or text_col not in df.columns:
        print(f"  [词云] {park_name} 无数据，跳过")
        return ""

    all_text = " ".join(df[text_col].fillna("").astype(str))
    # jieba 分词
    words = jieba.cut(all_text)
    filtered = [w for w in words if len(w) >= 2 and w not in _STOPWORDS
                and not re.fullmatch(r"[\d\s\W]+", w)]
    text_for_cloud = " ".join(filtered)

    if not text_for_cloud.strip():
        print(f"  [词云] {park_name} 分词后无有效词，跳过")
        return ""

    wc_kwargs = dict(
        background_color="white",
        max_words=max_words,
        width=800,
        height=500,
        collocations=False,
        stopwords=_STOPWORDS,
    )
    if _FONT_PATH:
        wc_kwargs["font_path"] = _FONT_PATH

    wc = WordCloud(**wc_kwargs).generate(text_for_cloud)

    os.makedirs(output_dir, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", park_name)
    path = os.path.join(output_dir, f"wordcloud_{safe_name}.png")
    wc.to_file(path)
    print(f"  [词云] 已保存: {path}")
    return path


def generate_all_wordclouds(
    park_data: dict[str, pd.DataFrame],
    output_dir: str = "output",
):
    """为所有公园批量生成词云"""
    for name, df in park_data.items():
        generate_wordcloud(df, name, output_dir)


# ─────────────────────────────────────────────────────────
# 2. A4/A5 双柱状对比图
# ─────────────────────────────────────────────────────────

def plot_bar_comparison(
    scores: pd.DataFrame,
    output_dir: str = "output",
    filename: str = "a4_a5_bar.png",
):
    """
    画 A4/A5 双柱图，每个公园并排两根柱子。
    scores: calculate_scores 返回的 DataFrame（index=公园名）
    """
    _set_mpl_font()
    os.makedirs(output_dir, exist_ok=True)

    parks = scores.index.tolist()
    a4 = scores["a4_score"].tolist()
    a5 = scores["a5_score"].tolist()
    x = range(len(parks))
    width = 0.35

    fig, ax = plt.subplots(figsize=(max(8, len(parks) * 1.5), 5))
    bars1 = ax.bar([i - width/2 for i in x], a4, width, label="A4 传播扩散度",
                   color="#4472C4", alpha=0.85)
    bars2 = ax.bar([i + width/2 for i in x], a5, width, label="A5 文化传播关联度",
                   color="#ED7D31", alpha=0.85)

    # 标注数值
    for bar in bars1 + bars2:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.5,
                f"{h:.1f}", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(list(x))
    ax.set_xticklabels(parks, fontsize=10)
    ax.set_ylim(0, 115)
    ax.set_ylabel("得分 (0~100)")
    ax.set_title("各公园 A4 / A5 指标对比")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()

    path = os.path.join(output_dir, filename)
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  [图表] 柱状图已保存: {path}")
    return path


# ─────────────────────────────────────────────────────────
# 3. A4-A5 散点图（象限图）
# ─────────────────────────────────────────────────────────

def plot_scatter(
    scores: pd.DataFrame,
    output_dir: str = "output",
    filename: str = "a4_a5_scatter.png",
):
    """
    画 A4（x轴）× A5（y轴）散点图，每个点标注公园名。
    用中线（50分）划成4个象限，便于解读。
    """
    _set_mpl_font()
    os.makedirs(output_dir, exist_ok=True)

    parks = scores.index.tolist()
    a4 = scores["a4_score"].values
    a5 = scores["a5_score"].values

    fig, ax = plt.subplots(figsize=(7, 6))

    # 散点
    ax.scatter(a4, a5, s=120, color="#5B9BD5", zorder=3)

    # 标注公园名
    for i, name in enumerate(parks):
        ax.annotate(
            name,
            (a4[i], a5[i]),
            textcoords="offset points",
            xytext=(8, 5),
            fontsize=9,
        )

    # 象限分割线
    ax.axvline(50, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)
    ax.axhline(50, color="gray", linestyle="--", linewidth=0.8, alpha=0.7)

    # 象限标签
    ax.text(5,  95, "低传播·高文化", fontsize=8, color="gray", alpha=0.7)
    ax.text(75, 95, "高传播·高文化", fontsize=8, color="#70AD47", alpha=0.9,
            fontweight="bold")
    ax.text(5,  3,  "低传播·低文化", fontsize=8, color="gray", alpha=0.7)
    ax.text(75, 3,  "高传播·低文化", fontsize=8, color="gray", alpha=0.7)

    ax.set_xlim(0, 105)
    ax.set_ylim(0, 105)
    ax.set_xlabel("A4 传播扩散度", fontsize=11)
    ax.set_ylabel("A5 文化传播关联度", fontsize=11)
    ax.set_title("公园文化传播象限分析", fontsize=13)
    ax.grid(True, linestyle="--", alpha=0.3)
    plt.tight_layout()

    path = os.path.join(output_dir, filename)
    plt.savefig(path, dpi=150)
    plt.close()
    print(f"  [图表] 散点图已保存: {path}")
    return path


# ─────────────────────────────────────────────────────────
# 测试
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    # 造假 scores 测试绘图
    scores = pd.DataFrame({
        "a4_score": [80, 30, 60, 90, 20, 50],
        "a5_score": [70, 85, 40, 30, 50, 65],
    }, index=["公园A", "公园B", "公园C", "公园D", "公园E", "公园F"])

    plot_bar_comparison(scores, output_dir="output")
    plot_scatter(scores, output_dir="output")
