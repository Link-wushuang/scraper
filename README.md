# 公园文化传播分析爬虫

从携程、大众点评、美团、小红书抓取公园评论/笔记数据，按关键词做文化传播指标分析，输出原始 CSV、Excel 报告和可视化图表。

## 环境准备

```bash
conda create --name scraper python=3.10
conda activate scraper
pip install -r requirements.txt
playwright install chromium
```

项目主要配置在 `config.py`：

- `PARKS`：公园名称、关键词、平台 ID。
- `DATE_START` / `DATE_END`：时间范围。
- `MAX_REVIEWS_*` / `MAX_NOTES_XHS`：各平台抓取上限。
- `DATA_DIR` / `OUTPUT_DIR`：输出目录。

## 推荐运行方式

现在建议按平台单独跑，尤其是小红书和大众点评这类需要打开浏览器页面的爬虫。不要默认一次跑全部平台，否则一个浏览器页面很难同时配合多个平台的交互式抓取。
记得先用Edge浏览器登录好你要爬的平台。

### 只跑携程

携程现在按 `config.py` 里的固定景点网址抓取。登录 Edge 后推荐用 profile 模式：

```bash
python main.py --platform ctrip --profile --park 公园A
```

已配置的携程网址：

- 公园A 北海公园：https://you.ctrip.com/sight/beijing1/232.html?renderPlatform=
- 公园B 天坛公园：https://you.ctrip.com/sight/beijing1/233.html?renderPlatform=
- 公园C 颐和园：https://you.ctrip.com/sight/beijing1/231.html?renderPlatform=
- 公园D 朝阳公园：https://you.ctrip.com/sight/beijing1/107621.html?renderPlatform=
- 公园E 奥林匹克森林公园：https://you.ctrip.com/sight/beijing1/69342270.html?renderPlatform=#ctm_ref=www_hp_bs_lst
- 公园F 树村公园：https://you.ctrip.com/sight/beijing1/1483951.html?renderPlatform=

这个模式会打开景点页，向下滚动到用户评论区，提取当前页评论，并尝试点击“下一页”继续抓取。

### 只跑小红书

你已经验证过的模式，可以从 `main.py` 入口这样跑：

```bash
python main.py --platform xhs --profile --park 公园A
```

等价于让 `main.py` 只调用小红书爬虫，并把 `--profile` 传给 `xiaohongshu_scraper.py`。小红书会使用 Edge 已有用户配置和登录状态。

也可以继续单独运行小红书脚本：

```bash
python xiaohongshu_scraper.py --profile
```

### 只跑大众点评（大众不行，需要app端）

```bash
python main.py --platform dianping --park 公园A
```

大众点评现在默认使用“当前页面点击模式”：

1. 程序会尝试连接 Edge 调试端口 `9222`。
2. 如果没有连接上，会启动带调试端口的 Edge。
3. 你在 Edge 里打开大众点评搜索页、店铺页或点评页。
4. 回到终端按回车，程序会复用当前页面，点击卡片/点评入口抓取评论。

如果想回到旧的 shop_id URL 抓取方式，需要在代码里调用：

```python
dianping_scraper.scrape_park(..., use_open_page=False)
```

### 只跑其他平台

```bash
python main.py --platform ctrip --park 公园A
完整（python main.py --platform ctrip --profile）
python main.py --platform meituan --park 公园A（还没做）
```

### 全平台运行

仍然保留全平台入口：

```bash
python main.py
```

但如果要抓小红书或大众点评，推荐改用单平台命令，手动配合浏览器页面更稳定。

## 常用参数

```bash
python main.py --help
```

当前支持：

- `--platform {all,ctrip,dianping,meituan,xhs}`：选择平台，默认 `all`。
- `--profile`：小红书 profile 模式，通常和 `--platform xhs` 一起使用。
- `--park 公园A 公园B`：只处理指定公园。
- `--only-analysis`：跳过爬取，只分析 `data/` 目录已有 CSV。
- `--skip-xhs`：全平台运行时跳过小红书。

## 输出

- `data/`：各平台原始 CSV，例如 `公园A_xhs.csv`。
- `output/`：Excel 报告、词云、柱状图、散点图。

## 项目结构

```text
config.py              # 公园列表、平台 ID、抓取参数
main.py                # 主入口，支持单平台和全平台运行
anti_crawl.py          # 请求头、延迟、重试等反爬辅助
ctrip_scraper.py       # 携程爬虫
dianping_scraper.py    # 大众点评爬虫，默认当前页面点击模式
meituan_scraper.py     # 美团爬虫
xiaohongshu_scraper.py # 小红书爬虫，支持 --profile / --connect
find_ids.py            # 辅助查找平台 POI ID
classifier.py          # 文本关键词分类
keywords.py            # 分类关键词词典
calculator.py          # A4/A5 分数计算
charts.py              # 词云、柱状图、散点图生成
```

## 注意事项

- 小红书 `--profile` 会使用 Edge 用户数据目录，运行前需要确保 Edge 登录状态可用。
- 大众点评当前页面模式需要你手动打开正确页面，再让程序接管点击。
- 如果只想更新某个平台的数据，优先使用 `--platform` 单独运行，避免无关平台耗时或触发登录/验证。
