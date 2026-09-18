# 报告改为统一排序卡片流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 GitHub Pages / Web UI 热点报告从"按关键词分组 + 按平台分组的新增热点"改为一个按权重全局排序、以卡片形式展示、支持"换一批"分批浏览的统一新闻流。

**Architecture:** 新建 `trendradar/html_report.py` 承接 `main.py` 里原本内联的 HTML/CSS/JS 拼接逻辑（`generate_html_report` / `render_html_content`），在其中新增"摊平 + 全局重排 + 卡片网格 + 分批"的渲染逻辑；`calculate_news_weight` 从 `main.py` 迁移到 `trendradar/utils.py` 并改为显式接收 `weight_config` 参数，供 `main.py: count_word_frequency`（不变的分组统计逻辑）和新模块共用；删除确认无引用的死代码 `templates/report.html`、`templates/stats.html`、`templates/new_titles.html`，并同步修掉 Docker 构建里对这个空目录的 `COPY`。`count_word_frequency`、`prepare_report_data` 及所有推送通知路径完全不改动。

**Tech Stack:** Python 3.10+, pytest, mypy, FastAPI + Jinja2（仅 web_server 表单，不涉及本次核心渲染逻辑）, 原生 HTML/CSS/JS（无前端框架，静态页面内嵌脚本）。

---

## 背景速览（执行者必读）

- 设计文档：`docs/superpowers/specs/2026-09-18-unified-news-card-report-design.md`（已提交，已修正过一次 `MAX_NEWS_PER_KEYWORD` 语义误判，务必以最新版本为准）。
- **真正生成报告 HTML 的函数是 `main.py` 里的 `generate_html_report`（第 938 行起）和 `render_html_content`（第 985 行起）**，纯 Python f-string 拼接，不经过任何 Jinja 模板。`templates/report.html`、`templates/stats.html`、`templates/new_titles.html` 三个文件没有被任何代码加载，是死代码，会在 Task 6 删除。
- `calculate_news_weight`（main.py 第 326-359 行）目前签名是 `calculate_news_weight(title_data: Dict, rank_threshold: int = CONFIG["RANK_THRESHOLD"]) -> float`，内部直接读全局 `CONFIG["WEIGHT_CONFIG"]`。唯一调用点在 `main.py` 第 711 行 `count_word_frequency` 内部，调用时已经显式传了 `rank_threshold`（没有依赖默认值）。迁移后新签名：`calculate_news_weight(title_data: Dict, weight_config: Dict, rank_threshold: int) -> float`，两个参数都必填，不再依赖任何全局变量。
- `prepare_report_data`（`trendradar/notifier/__init__.py` 第 220 行起）同时被 HTML 报告生成和推送通知复用，本次完全不修改它的实现和返回结构；返回的 `report_data["stats"]` 里每条新闻的字段是：`title`, `source_name`, `time_display`, `count`, `ranks`, `rank_threshold`, `url`, `mobile_url`（注意是 `mobile_url` 不是 `mobileUrl`）, `is_new`。
- `docker/Dockerfile` 第 56 行有 `COPY templates/ ./templates/`；删掉三个文件后 `templates/` 目录在 git 里不再存在（git 不跟踪空目录），这行 `COPY` 在 Docker 构建时会因为源路径不存在而失败，必须在 Task 6 一并删除。
- `tests/test_main.py` 目前用 `sys.path.insert(0, "/tmp/TrendRadar_clone")` 加 `import main` 的方式导入被测模块；在这台机器上执行 `pytest tests/test_main.py` 会因为 `import main` 意外解析到同级目录另一个不相关项目（`../../yijiushuoguo-AI/main.py`，缺 `openai` 包）而报 `ModuleNotFoundError: No module named 'openai'`。这是**改动前就存在**的环境问题，与本计划无关；每个任务验证测试时，全量测试请用 `pytest --ignore=tests/test_main.py`，如需单独验证 `test_main.py` 里的改动，直接读代码走查即可，不强求本地能跑通该文件。

---

## Task 1: 迁移 `calculate_news_weight` 到 `trendradar/utils.py`

**Files:**
- Modify: `trendradar/utils.py`（新增函数，插入到第 269 行 `load_frequency_words` 结束和第 272 行 `format_rank_display` 开始之间）
- Modify: `main.py:326-359`（删除本地定义）
- Modify: `main.py:14`（导入语句）
- Modify: `main.py:711`（调用点传参）
- Modify: `tests/test_utils.py`（新增测试类）
- Modify: `tests/test_main.py:84-113`（删除旧测试类 `TestCalculateNewsWeight`）

- [ ] **Step 1: 在 `tests/test_utils.py` 写失败的测试**

打开 `tests/test_utils.py`，把顶部 import 块（第 11-20 行）里的 `format_title_for_platform,` 后面加一行 `calculate_news_weight,`：

```python
from trendradar.utils import (
    clean_title,
    html_escape,
    matches_word_groups,
    format_rank_display,
    format_title_for_platform,
    calculate_news_weight,
    load_frequency_words,
    ensure_directory_exists,
    get_output_path,
)
```

在文件末尾追加：

```python


class TestCalculateNewsWeight:
    def _weight_config(self):
        return {"RANK_WEIGHT": 0.6, "FREQUENCY_WEIGHT": 0.3, "HOTNESS_WEIGHT": 0.1}

    def test_basic_rank_weight(self):
        data = {"ranks": [1], "count": 1}
        weight = calculate_news_weight(data, self._weight_config(), rank_threshold=5)
        assert weight > 0

    def test_multiple_ranks_average(self):
        data = {"ranks": [1, 2], "count": 2}
        weight1 = calculate_news_weight(data, self._weight_config(), rank_threshold=5)
        data2 = {"ranks": [5, 6], "count": 2}
        weight2 = calculate_news_weight(data2, self._weight_config(), rank_threshold=5)
        assert weight1 > weight2

    def test_frequency_weight(self):
        data = {"ranks": [1], "count": 5}
        weight = calculate_news_weight(data, self._weight_config(), rank_threshold=5)
        assert weight > 0

    def test_hotness_weight(self):
        data = {"ranks": [1, 2, 3], "count": 3}
        weight_high = calculate_news_weight(data, self._weight_config(), rank_threshold=5)
        data2 = {"ranks": [8, 9, 10], "count": 3}
        weight_low = calculate_news_weight(data2, self._weight_config(), rank_threshold=5)
        assert weight_high > weight_low

    def test_empty_ranks(self):
        data = {"ranks": [], "count": 1}
        weight = calculate_news_weight(data, self._weight_config(), rank_threshold=5)
        assert weight == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_utils.py::TestCalculateNewsWeight -v --no-cov`
Expected: FAIL，报 `ImportError: cannot import name 'calculate_news_weight' from 'trendradar.utils'`

- [ ] **Step 3: 在 `trendradar/utils.py` 实现函数**

在 `trendradar/utils.py` 第 269 行（`return processed_groups, filter_words, global_filters` 之后，空两行）和第 272 行 `def format_rank_display` 之间插入：

```python
def calculate_news_weight(
    title_data: Dict[str, Any], weight_config: Dict[str, float], rank_threshold: int
) -> float:
    """计算新闻权重，用于排序"""
    ranks = title_data.get("ranks", [])
    if not ranks:
        return 0.0

    count = title_data.get("count", len(ranks))

    # 排名权重：Σ(11 - min(rank, 10)) / 出现次数
    rank_scores = []
    for rank in ranks:
        score = 11 - min(rank, 10)
        rank_scores.append(score)

    rank_weight = sum(rank_scores) / len(ranks) if ranks else 0

    # 频次权重：min(出现次数, 10) × 10
    frequency_weight = min(count, 10) * 10

    # 热度加成：高排名次数 / 总出现次数 × 100
    high_rank_count = sum(1 for rank in ranks if rank <= rank_threshold)
    hotness_ratio = high_rank_count / len(ranks) if ranks else 0
    hotness_weight = hotness_ratio * 100

    total_weight = (
        rank_weight * weight_config["RANK_WEIGHT"]
        + frequency_weight * weight_config["FREQUENCY_WEIGHT"]
        + hotness_weight * weight_config["HOTNESS_WEIGHT"]
    )

    return total_weight


```

（这段代码是从 `main.py` 原样搬过来的，只改了函数签名：`rank_threshold` 去掉了绑定全局 `CONFIG` 的默认值，新增 `weight_config` 必填参数，内部把 `CONFIG["WEIGHT_CONFIG"]` 换成参数 `weight_config`。）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_utils.py::TestCalculateNewsWeight -v --no-cov`
Expected: PASS，5 个测试全过

- [ ] **Step 5: 从 `main.py` 删除旧定义，改为导入**

在 `main.py` 第 14 行：

```python
from trendradar.utils import load_frequency_words, matches_word_groups
```

改为：

```python
from trendradar.utils import load_frequency_words, matches_word_groups, calculate_news_weight
```

删除 `main.py` 第 325-360 行（含注释行 `# === 统计和分析 ===` 到函数结尾的空行，即从 `# === 统计和分析 ===` 到 `return total_weight` 后的空行为止，共计 `calculate_news_weight` 整个函数定义），确认删除后 `def format_time_display` 紧接在 `from trendradar.utils import ...` 生效的位置之前，不留多余空行造成的语法问题（保留一个空行分隔即可，参照文件里其他函数间距）。

- [ ] **Step 6: 更新 `count_word_frequency` 的调用点**

Step 5 删除 `calculate_news_weight` 之后，下面这段代码的行号会往前移，不要按行号定位，直接在 `main.py` 里搜索这段内容（在 `count_word_frequency` 函数内）：

```python
        # 按权重排序
        sorted_titles = sorted(
            all_titles,
            key=lambda x: (
                -calculate_news_weight(x, rank_threshold),
                min(x["ranks"]) if x["ranks"] else 999,
                -x["count"],
            ),
        )
```

改为：

```python
        # 按权重排序
        sorted_titles = sorted(
            all_titles,
            key=lambda x: (
                -calculate_news_weight(x, CONFIG["WEIGHT_CONFIG"], rank_threshold),
                min(x["ranks"]) if x["ranks"] else 999,
                -x["count"],
            ),
        )
```

- [ ] **Step 7: 从 `tests/test_main.py` 删除旧测试类**

删除 `tests/test_main.py` 第 84-113 行的整个 `class TestCalculateNewsWeight:` 块（从 `class TestCalculateNewsWeight:` 到 `assert weight == 0.0` 后、`class TestFormatTimeDisplay:` 前的空行）。删除后确认 `class TestParseFileTitles:` 块和 `class TestFormatTimeDisplay:` 块之间只隔一个空行（参照文件其余类间距）。

- [ ] **Step 8: 运行完整测试套件确认无回归**

Run: `uv run pytest --ignore=tests/test_main.py -q`
Expected: 全部通过（数量应与改动前一致，只是 `TestCalculateNewsWeight` 从 `test_main.py` 的统计里消失、在 `test_utils.py` 里出现）

再单独确认 `count_word_frequency` 相关测试仍然通过（如果存在，通常在 `test_main.py` 里，因环境问题无法本地跑，改为走查 Step 6 的 diff 确认逻辑等价：只是把 `rank_threshold` 单参数调用换成了显式传 `CONFIG["WEIGHT_CONFIG"]` 的三参数调用，排序 key 的相对大小关系不变）。

- [ ] **Step 9: mypy 检查**

Run: `uv run mypy trendradar/utils.py main.py`
Expected: `Success: no issues found in 2 source files`

- [ ] **Step 10: Commit**

```bash
git add trendradar/utils.py main.py tests/test_utils.py tests/test_main.py
git commit -m "refactor: move calculate_news_weight to trendradar/utils.py

Drop its implicit dependency on main.py's global CONFIG by making
weight_config an explicit parameter, so the upcoming html_report
module can reuse it without a circular import."
```

---

## Task 2: 新增 `cards_per_batch` 配置

**Files:**
- Modify: `config/config.yaml`
- Modify: `trendradar/config.py`
- Modify: `tests/test_config_load.py`

- [ ] **Step 1: 在 `tests/test_config_load.py` 写失败的测试**

在 `tests/test_config_load.py` 的 `TestLoadConfig` 类里，`test_env_override_report_mode` 方法后面（第 98 行之后）插入两个新测试：

```python

    def test_cards_per_batch_default(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            config = load_config()
            assert config["CARDS_PER_BATCH"] == 12

    def test_env_override_cards_per_batch(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("CARDS_PER_BATCH", "20")
            config = load_config()
            assert config["CARDS_PER_BATCH"] == 20
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config_load.py::TestLoadConfig::test_cards_per_batch_default tests/test_config_load.py::TestLoadConfig::test_env_override_cards_per_batch -v --no-cov`
Expected: FAIL，`KeyError: 'CARDS_PER_BATCH'`

- [ ] **Step 3: 在 `config/config.yaml` 加配置项**

在 `config/config.yaml` 的 `report:` 段里，`max_news_per_keyword: 0` 后面加一行：

```yaml
report:
  mode: daily
  rank_threshold: 5
  sort_by_position_first: false
  max_news_per_keyword: 0
  cards_per_batch: 12
  reverse_content_order: false
```

- [ ] **Step 4: 在 `trendradar/config.py` 读取配置项**

在 `trendradar/config.py` 里 `"MAX_NEWS_PER_KEYWORD"` 那一段（第 176-179 行）后面加：

```python
        "CARDS_PER_BATCH": int(
            os.environ.get("CARDS_PER_BATCH", "").strip() or "0"
        )
        or config_data["report"].get("cards_per_batch", 12),
```

（与刚加完的 `MAX_WORKERS` 是完全一样的模式：环境变量优先，否则读 yaml，否则默认值。）

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_config_load.py -v --no-cov`
Expected: 全部通过，包括新增的两个

- [ ] **Step 6: mypy 检查**

Run: `uv run mypy trendradar/config.py`
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 7: Commit**

```bash
git add config/config.yaml trendradar/config.py tests/test_config_load.py
git commit -m "feat: add report.cards_per_batch config for news card pagination"
```

---

## Task 3: 新建 `trendradar/html_report.py`，实现摊平排序函数

**Files:**
- Create: `trendradar/html_report.py`
- Create: `tests/test_html_report.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_html_report.py`：

```python
# coding=utf-8

from typing import Any, Dict, List

import pytest

from trendradar.html_report import _flatten_and_sort_news


def _weight_config() -> Dict[str, float]:
    return {"RANK_WEIGHT": 0.6, "FREQUENCY_WEIGHT": 0.3, "HOTNESS_WEIGHT": 0.1}


def _title(title: str, ranks: List[int], count: int = 1, is_new: bool = False) -> Dict[str, Any]:
    return {
        "title": title,
        "source_name": "测试源",
        "time_display": "",
        "count": count,
        "ranks": ranks,
        "rank_threshold": 5,
        "url": "",
        "mobile_url": "",
        "is_new": is_new,
    }


class TestFlattenAndSortNews:
    def test_merges_all_groups_into_one_list(self):
        stats = [
            {"word": "关键词A", "count": 1, "titles": [_title("标题1", [1])]},
            {"word": "关键词B", "count": 1, "titles": [_title("标题2", [2])]},
        ]
        result = _flatten_and_sort_news(stats, _weight_config(), rank_threshold=5)
        assert len(result) == 2
        assert {item["title"] for item in result} == {"标题1", "标题2"}

    def test_sorts_by_weight_descending(self):
        stats = [
            {
                "word": "全部新闻",
                "count": 2,
                "titles": [
                    _title("低排名", [9], count=1),
                    _title("高排名", [1], count=1),
                ],
            }
        ]
        result = _flatten_and_sort_news(stats, _weight_config(), rank_threshold=5)
        assert [item["title"] for item in result] == ["高排名", "低排名"]

    def test_empty_stats_returns_empty_list(self):
        result = _flatten_and_sort_news([], _weight_config(), rank_threshold=5)
        assert result == []

    def test_group_with_no_titles_is_skipped(self):
        stats = [
            {"word": "空组", "count": 0, "titles": []},
            {"word": "有内容", "count": 1, "titles": [_title("标题1", [1])]},
        ]
        result = _flatten_and_sort_news(stats, _weight_config(), rank_threshold=5)
        assert len(result) == 1
        assert result[0]["title"] == "标题1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_html_report.py -v --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'trendradar.html_report'`

- [ ] **Step 3: 实现 `trendradar/html_report.py`（第一部分：摊平排序）**

创建 `trendradar/html_report.py`：

```python
# coding=utf-8

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trendradar import utils
from trendradar.notifier import prepare_report_data
from trendradar.utils import calculate_news_weight


def _flatten_and_sort_news(
    stats: List[Dict[str, Any]],
    weight_config: Dict[str, float],
    rank_threshold: int,
) -> List[Dict[str, Any]]:
    """把按关键词分组的 stats 摊平成一个按权重全局排序的新闻列表

    不对总条数做任何截断：每个关键词组内的数量上限（MAX_NEWS_PER_KEYWORD /
    frequency_words.txt 里的 @N 语法）已经在 count_word_frequency 里按组生效过了，
    这里只负责合并展示顺序，不重复截断。
    """
    all_titles: List[Dict[str, Any]] = []
    for stat in stats:
        all_titles.extend(stat["titles"])

    sorted_titles = sorted(
        all_titles,
        key=lambda x: (
            -calculate_news_weight(x, weight_config, rank_threshold),
            min(x["ranks"]) if x["ranks"] else 999,
            -x["count"],
        ),
    )

    return sorted_titles
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_html_report.py -v --no-cov`
Expected: PASS，4 个测试全过

- [ ] **Step 5: mypy 检查**

Run: `uv run mypy trendradar/html_report.py`
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 6: Commit**

```bash
git add trendradar/html_report.py tests/test_html_report.py
git commit -m "feat: add trendradar/html_report.py with news flattening logic"
```

---

## Task 4: 实现卡片网格渲染 + 分批 + 完整报告生成

**Files:**
- Modify: `trendradar/html_report.py`
- Modify: `tests/test_html_report.py`

- [ ] **Step 1: 写失败的测试（卡片渲染 + 分批）**

在 `tests/test_html_report.py` 顶部 import 那一行改为：

```python
from trendradar.html_report import _flatten_and_sort_news, _render_news_cards_html
```

在文件末尾追加：

```python


class TestRenderNewsCardsHtml:
    def test_empty_list_returns_no_cards_and_zero_batches(self):
        cards_html, total_batches = _render_news_cards_html([], cards_per_batch=12)
        assert cards_html == ""
        assert total_batches == 0

    def test_single_batch_when_under_batch_size(self):
        news = [_title(f"标题{i}", [1]) for i in range(5)]
        cards_html, total_batches = _render_news_cards_html(news, cards_per_batch=12)
        assert total_batches == 1
        assert cards_html.count('class="news-card') == 5
        # 只有一批时，所有卡片都不应该被 JS 初始隐藏
        assert 'style="display:none"' not in cards_html

    def test_multiple_batches_hides_non_first_batch(self):
        news = [_title(f"标题{i}", [1]) for i in range(15)]
        cards_html, total_batches = _render_news_cards_html(news, cards_per_batch=12)
        assert total_batches == 2
        assert cards_html.count('data-batch="0"') == 12
        assert cards_html.count('data-batch="1"') == 3
        assert cards_html.count('style="display:none"') == 3

    def test_zero_batch_size_treated_as_single_batch(self):
        news = [_title(f"标题{i}", [1]) for i in range(15)]
        cards_html, total_batches = _render_news_cards_html(news, cards_per_batch=0)
        assert total_batches == 1
        assert 'style="display:none"' not in cards_html

    def test_new_badge_class_applied(self):
        news = [_title("新标题", [1], is_new=True)]
        cards_html, _ = _render_news_cards_html(news, cards_per_batch=12)
        assert 'class="news-card new"' in cards_html

    def test_escapes_title_html(self):
        news = [_title("<script>alert(1)</script>", [1])]
        cards_html, _ = _render_news_cards_html(news, cards_per_batch=12)
        assert "<script>alert(1)</script>" not in cards_html
        assert "&lt;script&gt;" in cards_html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_html_report.py::TestRenderNewsCardsHtml -v --no-cov`
Expected: FAIL，`ImportError: cannot import name '_render_news_cards_html'`

- [ ] **Step 3: 在 `trendradar/html_report.py` 实现 `_render_news_cards_html`**

在 `trendradar/html_report.py` 的 `_flatten_and_sort_news` 函数后面追加：

```python


def _render_news_cards_html(
    news_list: List[Dict[str, Any]], cards_per_batch: int
) -> Tuple[str, int]:
    """渲染新闻卡片网格 HTML，返回 (cards_html, total_batches)

    cards_per_batch <= 0 时视为不分批（全部算作第 0 批，total_batches = 1）。
    """
    if not news_list:
        return "", 0

    batch_size = cards_per_batch if cards_per_batch > 0 else len(news_list)
    total_batches = (len(news_list) + batch_size - 1) // batch_size

    cards_html = ""
    for idx, title_data in enumerate(news_list):
        batch_index = idx // batch_size
        position_in_batch = idx % batch_size + 1
        is_new = title_data.get("is_new", False)
        card_class = "news-card new" if is_new else "news-card"
        hidden_attr = "" if batch_index == 0 else ' style="display:none"'

        cards_html += f"""
                <div class="{card_class}" data-batch="{batch_index}"{hidden_attr}>
                    <div class="news-number">{position_in_batch}</div>
                    <div class="news-content">
                        <div class="news-header">
                            <span class="source-name">{utils.html_escape(title_data["source_name"])}</span>"""

        ranks = title_data.get("ranks", [])
        if ranks:
            min_rank = min(ranks)
            max_rank = max(ranks)
            rank_threshold = title_data.get("rank_threshold", 10)
            if min_rank <= 3:
                rank_class = "top"
            elif min_rank <= rank_threshold:
                rank_class = "high"
            else:
                rank_class = ""
            rank_text = str(min_rank) if min_rank == max_rank else f"{min_rank}-{max_rank}"
            cards_html += f'<span class="rank-num {rank_class}">{rank_text}</span>'

        time_display = title_data.get("time_display", "")
        if time_display:
            simplified_time = (
                time_display.replace(" ~ ", "~").replace("[", "").replace("]", "")
            )
            cards_html += f'<span class="time-info">{utils.html_escape(simplified_time)}</span>'

        count_info = title_data.get("count", 1)
        if count_info > 1:
            cards_html += f'<span class="count-info">{count_info}次</span>'

        cards_html += """
                        </div>
                        <div class="news-title">"""

        escaped_title = utils.html_escape(title_data["title"])
        link_url = title_data.get("mobile_url") or title_data.get("url", "")
        if link_url:
            escaped_url = utils.html_escape(link_url)
            cards_html += f'<a href="{escaped_url}" target="_blank" class="news-link">{escaped_title}</a>'
        else:
            cards_html += escaped_title

        cards_html += """
                        </div>
                    </div>
                </div>"""

    return cards_html, total_batches
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_html_report.py -v --no-cov`
Expected: PASS，全部通过（`TestFlattenAndSortNews` 4 个 + `TestRenderNewsCardsHtml` 6 个）

- [ ] **Step 5: 写失败的测试（`render_html_content` 整体结构）**

在 `tests/test_html_report.py` 顶部 import 改为：

```python
from trendradar.html_report import (
    _flatten_and_sort_news,
    _render_news_cards_html,
    render_html_content,
)
```

文件末尾追加：

```python


def _report_data(stats=None, failed_ids=None):
    return {
        "stats": stats or [],
        "new_titles": [],
        "failed_ids": failed_ids or [],
        "total_new_count": 0,
    }


class TestRenderHtmlContent:
    def test_no_word_group_markup(self):
        news = [_title("标题1", [1])]
        html = render_html_content(
            _report_data(), news, total_titles=1, cards_per_batch=12
        )
        assert "word-group" not in html
        assert "new-section" not in html
        assert "news-grid" in html
        assert "news-card" in html

    def test_no_segmented_save_button(self):
        news = [_title("标题1", [1])]
        html = render_html_content(
            _report_data(), news, total_titles=1, cards_per_batch=12
        )
        assert "saveAsMultipleImages" not in html
        assert "分段保存" not in html
        assert "saveAsImage" in html
        assert "保存为图片" in html

    def test_batch_controls_hidden_when_single_batch(self):
        news = [_title(f"标题{i}", [1]) for i in range(5)]
        html = render_html_content(
            _report_data(), news, total_titles=5, cards_per_batch=12
        )
        assert "换一批" not in html

    def test_batch_controls_shown_when_multiple_batches(self):
        news = [_title(f"标题{i}", [1]) for i in range(15)]
        html = render_html_content(
            _report_data(), news, total_titles=15, cards_per_batch=12
        )
        assert "换一批" in html
        assert "共 2 批" in html

    def test_failed_ids_section_rendered(self):
        html = render_html_content(
            _report_data(failed_ids=["zhihu", "weibo"]),
            [],
            total_titles=0,
            cards_per_batch=12,
        )
        assert "请求失败的平台" in html
        assert "zhihu" in html
        assert "weibo" in html

    def test_hot_news_count_uses_flattened_length(self):
        news = [_title(f"标题{i}", [1]) for i in range(3)]
        html = render_html_content(
            _report_data(), news, total_titles=10, cards_per_batch=12
        )
        assert "3 条" in html
        assert "10 条" in html
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/test_html_report.py::TestRenderHtmlContent -v --no-cov`
Expected: FAIL，`ImportError: cannot import name 'render_html_content'`

- [ ] **Step 7: 在 `trendradar/html_report.py` 实现 `render_html_content`**

在 `trendradar/html_report.py` 的 `_render_news_cards_html` 函数后面追加：

```python


def render_html_content(
    report_data: Dict[str, Any],
    news_list: List[Dict[str, Any]],
    total_titles: int,
    is_daily_summary: bool = False,
    mode: str = "daily",
    update_info: Optional[Dict[str, Any]] = None,
    cards_per_batch: int = 12,
) -> str:
    """渲染HTML内容：统一排序的新闻卡片流 + 分批展示"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>热点新闻分析</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js" integrity="sha512-BNaRQnYJYiPSqHHDb58B0yaPfCu+Wgds8Gp/gU33kqBtgNS4tSPHuGibyoeqMV/TJlSKda6FXzoEyYGjTe+vXA==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
        <style>
            * { box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
                margin: 0;
                padding: 16px;
                background: #fafafa;
                color: #333;
                line-height: 1.5;
            }

            .container {
                max-width: 880px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 2px 16px rgba(0,0,0,0.06);
            }

            .header {
                background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
                color: white;
                padding: 32px 24px;
                text-align: center;
                position: relative;
            }

            .save-buttons {
                position: absolute;
                top: 16px;
                right: 16px;
                display: flex;
                gap: 8px;
            }

            .save-btn {
                background: rgba(255, 255, 255, 0.2);
                border: 1px solid rgba(255, 255, 255, 0.3);
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 500;
                transition: all 0.2s ease;
                backdrop-filter: blur(10px);
                white-space: nowrap;
            }

            .save-btn:hover {
                background: rgba(255, 255, 255, 0.3);
                border-color: rgba(255, 255, 255, 0.5);
                transform: translateY(-1px);
            }

            .save-btn:active { transform: translateY(0); }
            .save-btn:disabled { opacity: 0.6; cursor: not-allowed; }

            .header-title {
                font-size: 22px;
                font-weight: 700;
                margin: 0 0 20px 0;
            }

            .header-info {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 16px;
                font-size: 14px;
                opacity: 0.95;
            }

            .info-item { text-align: center; }
            .info-label { display: block; font-size: 12px; opacity: 0.8; margin-bottom: 4px; }
            .info-value { font-weight: 600; font-size: 16px; }

            .content { padding: 24px; }

            .news-grid {
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 16px;
            }

            .news-card {
                position: relative;
                display: flex;
                gap: 12px;
                align-items: flex-start;
                padding: 14px 16px;
                border: 1px solid #f0f0f0;
                border-radius: 10px;
                background: #fff;
                transition: box-shadow 0.2s ease, transform 0.2s ease;
            }

            .news-card:hover {
                box-shadow: 0 4px 16px rgba(0,0,0,0.08);
                transform: translateY(-1px);
            }

            .news-card.new::after {
                content: "NEW";
                position: absolute;
                top: 10px;
                right: 10px;
                background: #fbbf24;
                color: #92400e;
                font-size: 9px;
                font-weight: 700;
                padding: 3px 6px;
                border-radius: 4px;
                letter-spacing: 0.5px;
            }

            .news-number {
                color: #999;
                font-size: 13px;
                font-weight: 600;
                min-width: 20px;
                text-align: center;
                flex-shrink: 0;
                background: #f8f9fa;
                border-radius: 50%;
                width: 24px;
                height: 24px;
                display: flex;
                align-items: center;
                justify-content: center;
                margin-top: 2px;
            }

            .news-content {
                flex: 1;
                min-width: 0;
                padding-right: 40px;
            }

            .news-card.new .news-content { padding-right: 50px; }

            .news-header {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-bottom: 8px;
                flex-wrap: wrap;
            }

            .source-name { color: #666; font-size: 12px; font-weight: 500; }

            .rank-num {
                color: #fff;
                background: #6b7280;
                font-size: 10px;
                font-weight: 700;
                padding: 2px 6px;
                border-radius: 10px;
                min-width: 18px;
                text-align: center;
            }
            .rank-num.top { background: #dc2626; }
            .rank-num.high { background: #ea580c; }

            .time-info { color: #999; font-size: 11px; }
            .count-info { color: #059669; font-size: 11px; font-weight: 500; }

            .news-title { font-size: 15px; line-height: 1.4; color: #1a1a1a; margin: 0; }

            .news-link { color: #2563eb; text-decoration: none; }
            .news-link:hover { text-decoration: underline; }
            .news-link:visited { color: #7c3aed; }

            .batch-controls {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 12px;
                margin-top: 24px;
                padding-top: 20px;
                border-top: 1px solid #f0f0f0;
            }

            .batch-btn {
                background: #4f46e5;
                color: white;
                border: none;
                padding: 10px 24px;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.2s ease;
            }
            .batch-btn:hover { background: #4338ca; }

            .batch-indicator { color: #666; font-size: 13px; }

            .error-section {
                background: #fef2f2;
                border: 1px solid #fecaca;
                border-radius: 8px;
                padding: 16px;
                margin-bottom: 24px;
            }
            .error-title { color: #dc2626; font-size: 14px; font-weight: 600; margin: 0 0 8px 0; }
            .error-list { list-style: none; padding: 0; margin: 0; }
            .error-item {
                color: #991b1b;
                font-size: 13px;
                padding: 2px 0;
                font-family: 'SF Mono', Consolas, monospace;
            }

            .footer {
                margin-top: 32px;
                padding: 20px 24px;
                background: #f8f9fa;
                border-top: 1px solid #e5e7eb;
                text-align: center;
            }
            .footer-content { font-size: 13px; color: #6b7280; line-height: 1.6; }
            .footer-link {
                color: #4f46e5;
                text-decoration: none;
                font-weight: 500;
                transition: color 0.2s ease;
            }
            .footer-link:hover { color: #7c3aed; text-decoration: underline; }
            .project-name { font-weight: 600; color: #374151; }

            @media (max-width: 480px) {
                body { padding: 12px; }
                .header { padding: 24px 20px; }
                .content { padding: 20px; }
                .footer { padding: 16px 20px; }
                .header-info { grid-template-columns: 1fr; gap: 12px; }
                .news-grid { grid-template-columns: 1fr; gap: 12px; }
                .news-card { padding: 12px 14px; gap: 8px; }
                .news-header { gap: 6px; }
                .news-content { padding-right: 45px; }
                .news-number { width: 20px; height: 20px; font-size: 12px; }
                .save-buttons {
                    position: static;
                    margin-bottom: 16px;
                    display: flex;
                    gap: 8px;
                    justify-content: center;
                    flex-direction: column;
                    width: 100%;
                }
                .save-btn { width: 100%; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="save-buttons">
                    <button class="save-btn" onclick="saveAsImage()">保存为图片</button>
                </div>
                <div class="header-title">热点新闻分析</div>
                <div class="header-info">
                    <div class="info-item">
                        <span class="info-label">报告类型</span>
                        <span class="info-value">"""

    if is_daily_summary:
        if mode == "current":
            html += "当前榜单"
        elif mode == "incremental":
            html += "增量模式"
        else:
            html += "当日汇总"
    else:
        html += "实时分析"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">新闻总数</span>
                        <span class="info-value">"""

    html += f"{total_titles} 条"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">热点新闻</span>
                        <span class="info-value">"""

    html += f"{len(news_list)} 条"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">生成时间</span>
                        <span class="info-value">"""

    now = utils.get_beijing_time()
    html += now.strftime("%m-%d %H:%M")

    html += """</span>
                    </div>
                </div>
            </div>

            <div class="content">"""

    if report_data["failed_ids"]:
        html += """
                <div class="error-section">
                    <div class="error-title">⚠️ 请求失败的平台</div>
                    <ul class="error-list">"""
        for id_value in report_data["failed_ids"]:
            html += f'<li class="error-item">{utils.html_escape(id_value)}</li>'
        html += """
                    </ul>
                </div>"""

    cards_html, total_batches = _render_news_cards_html(news_list, cards_per_batch)

    if cards_html:
        html += f"""
                <div class="news-grid">{cards_html}
                </div>"""

    if total_batches > 1:
        html += f"""
                <div class="batch-controls">
                    <button class="batch-btn" onclick="showNextBatch()">换一批</button>
                    <span class="batch-indicator">第 <span id="currentBatchNum">1</span> / 共 {total_batches} 批</span>
                </div>"""

    html += """
            </div>

            <div class="footer">
                <div class="footer-content">
                    由 <span class="project-name">TrendRadar</span> 生成 ·
                    <a href="https://github.com/sansan0/TrendRadar" target="_blank" class="footer-link">
                        GitHub 开源项目
                    </a>"""

    if update_info:
        html += f"""
                    <br>
                    <span style="color: #ea580c; font-weight: 500;">
                        发现新版本 {update_info['remote_version']}，当前版本 {update_info['current_version']}
                    </span>"""

    html += """
                </div>
            </div>
        </div>

        <script>
            async function saveAsImage() {
                const button = event.target;
                const originalText = button.textContent;

                try {
                    button.textContent = '生成中...';
                    button.disabled = true;
                    window.scrollTo(0, 0);

                    await new Promise(resolve => setTimeout(resolve, 200));

                    const buttons = document.querySelector('.save-buttons');
                    buttons.style.visibility = 'hidden';

                    await new Promise(resolve => setTimeout(resolve, 100));

                    const container = document.querySelector('.container');

                    const canvas = await html2canvas(container, {
                        backgroundColor: '#ffffff',
                        scale: 1.5,
                        useCORS: true,
                        allowTaint: false,
                        imageTimeout: 10000,
                        removeContainer: false,
                        foreignObjectRendering: false,
                        logging: false,
                        width: container.offsetWidth,
                        height: container.offsetHeight,
                        x: 0,
                        y: 0,
                        scrollX: 0,
                        scrollY: 0,
                        windowWidth: window.innerWidth,
                        windowHeight: window.innerHeight
                    });

                    buttons.style.visibility = 'visible';

                    const link = document.createElement('a');
                    const now = new Date();
                    const filename = `TrendRadar_热点新闻分析_${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}_${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}.png`;

                    link.download = filename;
                    link.href = canvas.toDataURL('image/png', 1.0);

                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);

                    button.textContent = '保存成功!';
                    setTimeout(() => {
                        button.textContent = originalText;
                        button.disabled = false;
                    }, 2000);

                } catch (error) {
                    const buttons = document.querySelector('.save-buttons');
                    buttons.style.visibility = 'visible';
                    button.textContent = '保存失败';
                    setTimeout(() => {
                        button.textContent = originalText;
                        button.disabled = false;
                    }, 2000);
                }
            }

            let currentBatch = 0;
            const totalBatches = """ + str(total_batches) + """;

            function showNextBatch() {
                if (totalBatches <= 1) { return; }
                currentBatch = (currentBatch + 1) % totalBatches;
                document.querySelectorAll('.news-card').forEach(function(card) {
                    const batchIndex = parseInt(card.getAttribute('data-batch'), 10);
                    card.style.display = batchIndex === currentBatch ? '' : 'none';
                });
                const indicator = document.getElementById('currentBatchNum');
                if (indicator) {
                    indicator.textContent = currentBatch + 1;
                }
                document.querySelector('.content').scrollIntoView({ behavior: 'smooth', block: 'start' });
            }

            document.addEventListener('DOMContentLoaded', function() {
                window.scrollTo(0, 0);
            });
        </script>
    </body>
    </html>
    """

    return html
```

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/test_html_report.py -v --no-cov`
Expected: PASS，全部通过（`TestFlattenAndSortNews` 4 + `TestRenderNewsCardsHtml` 6 + `TestRenderHtmlContent` 6 = 16 个）

- [ ] **Step 9: 写失败的测试（`generate_html_report` 文件写入）**

在 `tests/test_html_report.py` 顶部 import 改为：

```python
from trendradar.html_report import (
    _flatten_and_sort_news,
    _render_news_cards_html,
    render_html_content,
    generate_html_report,
)
```

文件末尾追加：

```python


class TestGenerateHtmlReport:
    def _config(self):
        return {
            "RANK_THRESHOLD": 5,
            "WEIGHT_CONFIG": {"RANK_WEIGHT": 0.6, "FREQUENCY_WEIGHT": 0.3, "HOTNESS_WEIGHT": 0.1},
        }

    def test_writes_html_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stats = [
            {
                "word": "全部新闻",
                "count": 1,
                "titles": [_title("标题1", [1])],
            }
        ]
        file_path = generate_html_report(
            self._config(), stats, total_titles=1, mode="daily", is_daily_summary=False
        )
        assert Path(file_path).exists()
        content = Path(file_path).read_text(encoding="utf-8")
        assert "标题1" in content
        assert "news-grid" in content

    def test_daily_summary_writes_root_index(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stats = [
            {
                "word": "全部新闻",
                "count": 1,
                "titles": [_title("标题1", [1])],
            }
        ]
        generate_html_report(
            self._config(), stats, total_titles=1, mode="daily", is_daily_summary=True
        )
        assert (tmp_path / "index.html").exists()
        assert (tmp_path / "output" / "index.html").exists()
```

在文件顶部 import 块里加上 `from pathlib import Path`（如果还没有的话，检查文件顶部现有 import，没有就在 `from typing import Any, Dict, List` 之前加一行 `from pathlib import Path`）。

- [ ] **Step 10: 运行测试确认失败**

Run: `uv run pytest tests/test_html_report.py::TestGenerateHtmlReport -v --no-cov`
Expected: FAIL，`ImportError: cannot import name 'generate_html_report'`

- [ ] **Step 11: 在 `trendradar/html_report.py` 实现 `generate_html_report`**

在 `trendradar/html_report.py` 文件末尾追加：

```python


def generate_html_report(
    config: Dict[str, Any],
    stats: List[Dict[str, Any]],
    total_titles: int,
    failed_ids: Optional[List[str]] = None,
    new_titles: Optional[Dict[str, Any]] = None,
    id_to_name: Optional[Dict[str, str]] = None,
    mode: str = "daily",
    is_daily_summary: bool = False,
    update_info: Optional[Dict[str, Any]] = None,
) -> str:
    """生成HTML报告"""
    if is_daily_summary:
        if mode == "current":
            filename = "当前榜单汇总.html"
        elif mode == "incremental":
            filename = "当日增量.html"
        else:
            filename = "当日汇总.html"
    else:
        filename = f"{utils.format_time_filename()}.html"

    file_path = utils.get_output_path("html", filename)

    report_data = prepare_report_data(config, stats, failed_ids, new_titles, id_to_name, mode)

    news_list = _flatten_and_sort_news(
        report_data["stats"], config["WEIGHT_CONFIG"], config["RANK_THRESHOLD"]
    )

    html_content = render_html_content(
        report_data,
        news_list,
        total_titles,
        is_daily_summary,
        mode,
        update_info,
        cards_per_batch=config.get("CARDS_PER_BATCH", 12),
    )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    if is_daily_summary:
        # 生成到根目录（供 GitHub Pages 访问）
        root_index_path = Path("index.html")
        with open(root_index_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # 同时生成到 output 目录（供 Docker Volume 挂载访问）
        output_index_path = Path("output") / "index.html"
        utils.ensure_directory_exists("output")
        with open(output_index_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    return file_path
```

- [ ] **Step 12: 运行测试确认通过**

Run: `uv run pytest tests/test_html_report.py -v --no-cov`
Expected: PASS，全部 18 个测试通过

- [ ] **Step 13: mypy 检查**

Run: `uv run mypy trendradar/html_report.py`
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 14: Commit**

```bash
git add trendradar/html_report.py tests/test_html_report.py
git commit -m "feat: render unified sorted news card grid with batch pagination"
```

---

## Task 5: 把 `main.py` 接到新模块，删除旧的内联渲染代码

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 加导入**

在 `main.py` 第 12 行 `from trendradar.notifier import send_to_notifications, prepare_report_data` 后面加一行：

```python
from trendradar.html_report import generate_html_report
```

- [ ] **Step 2: 删除旧的 `generate_html_report` 和 `render_html_content`**

注意：经过 Task 1 的编辑，`calculate_news_weight` 已经从 main.py 里删掉了，所以这两个函数现在的行号比设计/规划阶段读到的 938-2001 要小（少了约 36 行）。不要按绝对行号删除，用内容定位：

Run: `grep -n "^def generate_html_report\|^def render_html_content\|^def format_title_for_platform" main.py`

这会打印三行，形如：

```
752:def format_title_for_platform(
902:def generate_html_report(
949:def render_html_content(
```

（具体数字以实际输出为准。）删除的范围是：**从 `def generate_html_report(` 所在行开始，到 `render_html_content` 函数体结束为止**。`render_html_content` 函数体的结尾特征是：最后一行代码是 `return html`，其后是函数结尾的空行，再往下应该紧接着 `_run_analysis_pipeline` 所在的 `class` 定义或下一个顶层 `def`——执行时用 `grep -n "^def \|^class " main.py`，在输出里找到 `render_html_content` 那一行之后的下一个顶层定义，删除区间就是 `[generate_html_report 所在行, 下一个顶层定义所在行 - 1]`，中间只保留一个空行分隔（不要留两个连续空行）。用 Read 工具读一遍这个区间的首尾几行，确认首行确实是 `def generate_html_report(`、确认删除区间最后几行是 `render_html_content` 的 `return html` 加收尾空行，再执行删除。

- [ ] **Step 3: 更新调用点**

`main.py` 里 `_run_analysis_pipeline` 方法内（原第 2440-2450 行附近）：

```python
        # HTML生成
        html_file = generate_html_report(
            stats,
            total_titles,
            failed_ids=failed_ids,
            new_titles=new_titles,
            id_to_name=id_to_name,
            mode=mode,
            is_daily_summary=is_daily_summary,
            update_info=self.update_info if CONFIG["SHOW_VERSION_UPDATE"] else None,
        )
```

改为：

```python
        # HTML生成
        html_file = generate_html_report(
            CONFIG,
            stats,
            total_titles,
            failed_ids=failed_ids,
            new_titles=new_titles,
            id_to_name=id_to_name,
            mode=mode,
            is_daily_summary=is_daily_summary,
            update_info=self.update_info if CONFIG["SHOW_VERSION_UPDATE"] else None,
        )
```

- [ ] **Step 4: 确认没有遗留引用**

Run: `grep -n "def generate_html_report\|def render_html_content\|saveAsMultipleImages\|word-group\|new-section" main.py`
Expected: 无输出（`main.py` 里不应再有这些字符串——它们现在只应该出现在 `trendradar/html_report.py` 里，而且 `saveAsMultipleImages`/`word-group`/`new-section` 在新模块里也不应出现，因为已经被去掉了）

再确认：

Run: `grep -n "generate_html_report" main.py`
Expected: 只有两行——`from trendradar.html_report import generate_html_report` 和调用处的 `html_file = generate_html_report(`

- [ ] **Step 5: 运行完整测试套件**

Run: `uv run pytest --ignore=tests/test_main.py -q`
Expected: 全部通过

- [ ] **Step 6: mypy 检查**

Run: `uv run mypy trendradar/ main.py`
Expected: `Success: no issues found in N source files`（N 是所有被检查的文件数，不应该报任何 error）

- [ ] **Step 7: 手动跑一次报告生成，肉眼检查输出**

Run: `uv run python -c "
from trendradar.html_report import generate_html_report
config = {
    'RANK_THRESHOLD': 5,
    'WEIGHT_CONFIG': {'RANK_WEIGHT': 0.6, 'FREQUENCY_WEIGHT': 0.3, 'HOTNESS_WEIGHT': 0.1},
    'CARDS_PER_BATCH': 12,
}
stats = [{
    'word': '全部新闻',
    'count': 15,
    'titles': [
        {'title': f'测试新闻{i}', 'source_name': '知乎', 'time_display': '10:00', 'count': 1,
         'ranks': [i % 10 + 1], 'rank_threshold': 5, 'url': f'http://example.com/{i}',
         'mobileUrl': '', 'is_new': i < 3}
        for i in range(15)
    ],
}]
path = generate_html_report(config, stats, total_titles=15, mode='daily', is_daily_summary=False)
print('生成文件:', path)
"`

Expected: 打印出生成的文件路径，无报错。然后用浏览器打开该路径（`output/<今天日期>/html/xxx.html`），检查：卡片网格 2 列展示、能看到"换一批"按钮和"共 2 批"提示、点击"换一批"能切换到第二批 3 条、"保存为图片"按钮存在且没有"分段保存"按钮、其中 3 条带 NEW 角标。窗口收窄到 480px 以下时卡片变成单列。

- [ ] **Step 8: Commit**

```bash
git add main.py
git commit -m "refactor: use trendradar/html_report.py for report generation, drop inline generator"
```

---

## Task 6: 删除死代码模板文件，修掉 Docker 构建

**Files:**
- Delete: `templates/report.html`
- Delete: `templates/stats.html`
- Delete: `templates/new_titles.html`
- Modify: `docker/Dockerfile:56`

- [ ] **Step 1: 确认三个文件确实无引用**

Run: `grep -rn "templates/report.html\|templates/stats.html\|templates/new_titles.html" --include="*.py" --include="Dockerfile*" --include="*.yml" --include="*.yaml" . | grep -v ".venv\|__pycache__"`
Expected: 无输出（Task 开始前已经在设计阶段确认过一次，这里是实施前再确认一次，防止分支之间有别的改动引入了新引用）

- [ ] **Step 2: 删除三个死文件**

```bash
git rm templates/report.html templates/stats.html templates/new_titles.html
```

- [ ] **Step 3: 修掉 Dockerfile**

`docker/Dockerfile` 第 52-57 行现在是：

```dockerfile
COPY pyproject.toml .
COPY trendradar/ ./trendradar/
COPY mcp_server/ ./mcp_server/
COPY templates/ ./templates/
COPY main.py .
COPY docker/manage.py .
```

删掉 `COPY templates/ ./templates/` 这一行，改为：

```dockerfile
COPY pyproject.toml .
COPY trendradar/ ./trendradar/
COPY mcp_server/ ./mcp_server/
COPY main.py .
COPY docker/manage.py .
```

- [ ] **Step 4: 确认 `templates/` 目录在 git 里已经不存在**

Run: `git status --short`
Expected: 看到 `D  templates/new_titles.html`、`D  templates/report.html`、`D  templates/stats.html`（已 `git rm`，处于暂存态），没有任何文件残留在 `templates/` 下

- [ ] **Step 5: 运行完整测试套件确认没有依赖这三个文件的测试**

Run: `uv run pytest --ignore=tests/test_main.py -q`
Expected: 全部通过，不应该出现任何 `FileNotFoundError` 指向 `templates/`

- [ ] **Step 6: 同步修正 README 里的项目结构说明**

`README.md` 第 3512 行（本轮对话早些时候刚加的"项目结构"小节里）现在是：

```
templates/             # 推送/网页报告模板（report.html、new_titles.html、stats.html）
```

这三个文件已经删除，这一行改为：

```
（templates/ 目录已删除：报告 HTML/CSS/JS 现在由 trendradar/html_report.py 生成，不再走模板文件）
```

具体做法：用 Edit 工具把 `README.md` 里这一整行（`templates/             # 推送/网页报告模板（report.html、new_titles.html、stats.html）`）替换成上面这行说明文字，保持它在"项目结构"代码块里原来的相对位置（前后行不变）。

- [ ] **Step 7: Commit**

```bash
git add docker/Dockerfile README.md
git commit -m "chore: remove dead templates/*.html and their Docker COPY line

Nothing loads these via Jinja2 (web_server only loads web_server/templates/);
the real report renderer is trendradar/html_report.py. Leaving the empty,
now git-untracked templates/ directory referenced in the Dockerfile COPY
would break the image build."
```

---

## Task 7: 把 `cards_per_batch` 接入 Web UI 配置界面

**Files:**
- Modify: `web_server/config_manager.py`
- Modify: `web_server/templates/config.html`

- [ ] **Step 1: 在 `get_config_for_form` 里读取字段**

`web_server/config_manager.py` 里（跟随最近刚加的 `max_workers` 那行之后，即 `"request_interval"` 段落里）找到：

```python
            "max_news_per_keyword": config.get("report", {}).get("max_news_per_keyword", 0),
```

在这行后面加：

```python
            "cards_per_batch": config.get("report", {}).get("cards_per_batch", 12),
```

- [ ] **Step 2: 在 `save_config_from_form` 里写回字段**

同文件里找到：

```python
                    "max_news_per_keyword": int(form_data.get("max_news_per_keyword", 0)),
```

在这行后面加：

```python
                    "cards_per_batch": int(form_data.get("cards_per_batch", 12)),
```

- [ ] **Step 3: 在 `config.html` 加表单输入框**

`web_server/templates/config.html` 里找到：

```html
                <div class="form-group">
                    <label>每关键词最大新闻数</label>
                    <input type="number" name="max_news_per_keyword" value="{{ config.max_news_per_keyword or 0 }}" min="0" placeholder="0=不限制">
                </div>
```

在这个 `form-group` 后面加：

```html
                <div class="form-group">
                    <label>报告每批展示数量</label>
                    <input type="number" name="cards_per_batch" value="{{ config.cards_per_batch or 12 }}" min="1" max="100">
                </div>
```

- [ ] **Step 4: 在提交 JS 里收集字段**

`web_server/templates/config.html` 里找到：

```javascript
        max_news_per_keyword: parseInt(form.querySelector('[name="max_news_per_keyword"]').value) || 0,
```

在这行后面加：

```javascript
        cards_per_batch: parseInt(form.querySelector('[name="cards_per_batch"]').value) || 12,
```

（如果上面那行 `max_news_per_keyword` 的确切写法跟这里不完全一致，以文件里实际的那一行为准，在它后面插入新的一行，保持相同的缩进和分号风格。）

- [ ] **Step 5: 手动验证**

Run: `./start-web.sh &` 然后打开 `http://localhost:18080/config`，检查"报告设置"区块里出现"报告每批展示数量"输入框，默认值 12；改成别的数字点保存，刷新页面确认值保留；检查 `config/config.yaml` 里 `report.cards_per_batch` 确实被写成了新值。跑完后 `kill %1` 停掉后台的 web 服务。

- [ ] **Step 6: Commit**

```bash
git add web_server/config_manager.py web_server/templates/config.html
git commit -m "feat: expose cards_per_batch in the web config UI"
```

---

## Task 8: 全量验证

**Files:** 无新增/修改，纯验证

- [ ] **Step 1: 全量测试**

Run: `uv run pytest --ignore=tests/test_main.py -q`
Expected: 全部通过，覆盖率达标（`--cov-fail-under=80`，来自 `pyproject.toml`）

- [ ] **Step 2: mypy 全量检查**

Run: `uv run mypy trendradar/ main.py`
Expected: `Success: no issues found in N source files`

- [ ] **Step 3: 确认 `templates/` 死代码相关字符串不再出现在任何仍在使用的代码路径里**

Run: `grep -rn "word-group\|new-section\|saveAsMultipleImages\|分段保存" --include="*.py" --include="*.html" . | grep -v ".venv\|__pycache__\|node_modules"`
Expected: 无输出

- [ ] **Step 4: 端到端手动验证**

用测试数据跑一次完整的 `main.py`（或者复用 Task 5 Step 7 的方式生成一份真实结构的报告），在浏览器里核对：

1. 桌面宽度（>480px）：新闻卡片以 2 列网格展示，不再看到任何按关键词或平台的分组标题
2. 手机宽度（≤480px，用浏览器开发者工具的设备模拟）：卡片变成单列
3. 每批 12 条（或配置的 `cards_per_batch` 值），点击"换一批"能看到下一批，翻到最后一批再点击会循环回第一批
4. 不足一批时（比如只有 5 条匹配新闻）不显示"换一批"按钮
5. "保存为图片"按钮点击后能正常下载一张图片；页面上没有"分段保存"按钮
6. `is_new` 的新闻卡片右上角有 NEW 角标
7. 打开 Web UI 的"报告浏览"页面（`/reports/latest`），iframe 里嵌入的就是这份新版报告

- [ ] **Step 5: 最终提交（如果验证阶段发现需要小修小补）**

如果 Step 4 发现问题，直接在对应文件修，然后：

```bash
git add -A
git commit -m "fix: address issues found in unified card report manual verification"
```

如果没有问题，这一步无需操作，Task 8 结束即整个功能完成。

---

## Self-Review 记录

- **Spec 覆盖检查**：设计文档里的"架构改动"（新建 html_report.py / 迁移 calculate_news_weight / 删除死模板）对应 Task 1/3/4/5/6；"数据流"（摊平+全局排序，不改 count_word_frequency/prepare_report_data）对应 Task 3/4；"卡片内容与布局"对应 Task 4 Step 7 的 CSS 和卡片渲染代码；"换一批分批机制"（含 cards_per_batch 配置、Web UI 表单）对应 Task 2/4/7；"一并处理"（去掉分段保存、去掉新增热点区块）对应 Task 4（渲染代码里直接没有实现这两块）；"不受影响的部分"通过 Task 1/5 里显式保留 `count_word_frequency`/`prepare_report_data`/推送通知路径来保证；"测试与验证"对应每个 Task 内嵌的测试步骤 + Task 8。全部覆盖，无遗漏。
- **占位符扫描**：全文没有 TBD/TODO/"参照上面类似实现"这类占位表述，每个 Step 要么是可直接运行的命令，要么是完整代码块。
- **类型一致性核对**：`calculate_news_weight(title_data, weight_config, rank_threshold)` 签名在 Task 1（定义+main.py 调用点）和 Task 3（`_flatten_and_sort_news` 内部调用）里保持一致；`_render_news_cards_html(news_list, cards_per_batch) -> Tuple[str, int]` 在 Task 4 定义和 `render_html_content` 内部调用处一致；`generate_html_report(config, stats, total_titles, ...)` 新增的 `config` 首参数在 Task 4 定义和 Task 5 main.py 调用点保持一致。
