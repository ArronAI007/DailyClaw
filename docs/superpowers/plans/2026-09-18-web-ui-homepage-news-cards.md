# Web UI 首页改为新闻卡片流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 DailyClaw Web UI 首页（`http://localhost:18080/`）从运维仪表盘改造成新闻卡片流，数据链路跟真实报告完全一致（关键词过滤 + 权重排序 + 去重），并顺带把 `/reports` 页面里跟这次改版风格冲突的按平台分组预览去掉。

**Architecture:** 新建 `web_server/news_service.py` 复用 `main.py` 的 `read_all_today_titles`/`detect_latest_new_titles`/`count_word_frequency`（通过 `import main`）加上 `trendradar.notifier.prepare_report_data` 和 `trendradar/html_report.py` 里刚从私有改成公开的 `flatten_and_sort_news`，组装出今日新闻卡片列表，用 `mcp_server.services.cache_service.get_cache()` 的全局单例缓存 5 分钟；`dashboard.html`/`dashboard.css`/`dashboard.js` 改造成展示这份数据的卡片网格 + "换一批"；`reports.html` 去掉按平台分组的内联展开预览。

**Tech Stack:** Python 3.10+, FastAPI + Jinja2（web_server），pytest，mypy，原生 HTML/CSS/JS（沿用 `web_server/static/` 现有的单文件 CSS/JS 约定，不新开文件）。

---

## 背景速览（执行者必读）

- 设计文档：`docs/superpowers/specs/2026-09-18-web-ui-homepage-news-cards-design.md`（已提交，已获批准）。
- **`web_server`（`web_server/server.py`，FastAPI，`./start-web.sh` 启动，默认端口 18080）此前从未 `import main`**。这次是第一次这么做。`main.py` 顶层只做 `CONFIG = load_config()` 和日志配置，没有爬取/推送等副作用（`NewsAnalyzer().run()` 在 `if __name__ == "__main__":` 保护之下），确认过 import 是安全的；之前会让 `load_config()` 崩溃的 `config/config.yaml` 缺 `feishu_message_separator` 的预存在 bug 已经在这个 session 里单独修过了。
- `main.py` 的 `count_word_frequency` 函数**不是纯函数**：除了显式参数外，内部还直接读模块全局变量 `CONFIG`（`CONFIG.get("SORT_BY_POSITION_FIRST", False)`、`CONFIG.get("MAX_NEWS_PER_KEYWORD", 0)`、以及通过 `calculate_news_weight(x, CONFIG["WEIGHT_CONFIG"], ...)` 读权重配置）。`main.CONFIG` 只在第一次 `import main` 时赋值一次。为了让"配置管理"页面改了配置后，首页新闻计算不用重启 web_server 就能生效，`news_service.py` 每次重新计算（缓存未命中时）都会先用 `trendradar.config.load_config()` 读一份新鲜配置，再显式赋值给 `main.CONFIG`，这样 `count_word_frequency` 内部的隐式读取也能拿到最新值。这是设计文档里明确写过的取舍，不是意外的全局变量污染，但意味着这个函数有一个"运行时会修改 `main` 模块全局状态"的副作用，测试的时候要注意隔离（每个测试用完要把 `main.CONFIG` 还原，避免污染同一个 pytest 进程里跑的其它测试）。
- 缓存复用 `mcp_server/services/cache_service.py` 的**模块级全局单例** `get_cache()`（不是 `DataService` 实例的 `.cache` 属性——那样会导致 `web_server/news_service.py` 和 `web_server/server.py` 互相 import 形成循环依赖）。`get_cache()` 内部维护一个进程级 `_global_cache = None`，第一次调用时创建，之后每次调用都返回同一个实例；`mcp_server/services/data_service.py: DataService.__init__` 内部也是通过 `self.cache = get_cache()` 拿到这个同一个单例的——所以 `news_service.py` 直接 `from mcp_server.services.cache_service import get_cache` 用，跟 `DataService` 用的是同一份缓存存储，没有新增缓存基础设施，也没有循环 import。
- `web_server/server.py` 顶部的 `PROJECT_ROOT = Path(__file__).parent.parent` 是**固定的真实仓库路径**，`get_report_list()`/`get_platform_status()` 等函数都是基于这个固定路径读文件的，不受测试里 `monkeypatch.chdir()` 影响。但 `main.py` 的 `read_all_today_titles`/`detect_latest_new_titles` 用的是 `Path("output")`**相对路径**（相对于当前工作目录）。这个差异意味着：`web_server/news_service.py` 的单元测试可以用 `monkeypatch.chdir(tmp_path)` 干净地隔离（跟上一个 plan 里测 `generate_html_report` 用的是同一个模式），但如果想写一个端到端的、经过 FastAPI 路由的 HTTP 测试，`get_system_tools()`/`get_platform_status()` 这些走 `PROJECT_ROOT` 的部分没法这样隔离。所以这次的测试策略是：`news_service.py` 用干净的临时目录单元测试；`dashboard()` 路由改造后不写 HTTP 层测试，改用直接渲染 Jinja 模板（不经过路由）的方式验证模板逻辑，另外做一次手动的端到端验证（跟上一个 plan 收尾时用的 `curl` 方式一致）。
- 全程无需再确认设计，直接按任务顺序实施。

---

## Task 1: 把 `_flatten_and_sort_news` 提升为 `trendradar/html_report.py` 的公开函数

**Files:**
- Modify: `trendradar/html_report.py`
- Modify: `tests/test_html_report.py`

**背景**：`_flatten_and_sort_news` 目前是模块内部函数（下划线开头，只给 `trendradar/html_report.py` 自己用）。这次 `web_server/news_service.py` 要跨模块调用它，按 Python 的命名惯例应该去掉下划线、变成这个模块的公开接口的一部分——这是个纯改名操作，不改逻辑。

- [ ] **Step 1: 改名**

Run: `grep -n "_flatten_and_sort_news" trendradar/html_report.py`
Expected 会看到 2 处：函数定义 `def _flatten_and_sort_news(` 和 `generate_html_report` 内部的调用 `news_list = _flatten_and_sort_news(`。

把这两处的 `_flatten_and_sort_news` 都改成 `flatten_and_sort_news`（去掉开头的下划线），函数体和参数不动。

- [ ] **Step 2: 同步改测试文件里的引用**

Run: `grep -n "_flatten_and_sort_news" tests/test_html_report.py`

把找到的所有 `_flatten_and_sort_news` 引用（包括 `from trendradar.html_report import` 里的导入名和测试函数体里的调用）都改成 `flatten_and_sort_news`。

- [ ] **Step 3: 运行测试确认改名没破坏任何东西**

Run: `uv run pytest tests/test_html_report.py -v --no-cov`
Expected: 23 个测试全部通过（改名前是多少个还是多少个，纯重命名不影响测试数量）

- [ ] **Step 4: mypy 检查**

Run: `uv run mypy trendradar/html_report.py`
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 5: Commit**

```bash
git add trendradar/html_report.py tests/test_html_report.py
git commit -m "refactor: rename _flatten_and_sort_news to flatten_and_sort_news

Promoting it to a public function since web_server/news_service.py
(added in a later commit) needs to call it across module boundaries."
```

---

## Task 2: 新建 `web_server/news_service.py`

**Files:**
- Create: `web_server/news_service.py`
- Create: `tests/test_news_service.py`

- [ ] **Step 1: 写失败的测试（今天没数据的情况）**

创建 `tests/test_news_service.py`：

```python
# coding=utf-8

from pathlib import Path

import pytest
import yaml

import main
from trendradar.utils import format_date_folder
from web_server.news_service import get_today_news_cards


def _write_config(tmp_path: Path, cards_per_batch: int = 12) -> None:
    config_data = {
        "app": {"version_check_url": "", "show_version_update": False},
        "crawler": {
            "request_interval": 1000,
            "max_workers": 5,
            "use_proxy": False,
            "default_proxy": "",
            "enable_crawler": True,
        },
        "report": {
            "mode": "daily",
            "rank_threshold": 5,
            "sort_by_position_first": False,
            "max_news_per_keyword": 0,
            "cards_per_batch": cards_per_batch,
            "reverse_content_order": False,
        },
        "notification": {
            "enable_notification": False,
            "message_batch_size": 4000,
            "batch_send_interval": 3,
            "max_accounts_per_channel": 3,
            "feishu_message_separator": "---",
            "webhooks": {},
        },
        "weight": {"rank_weight": 0.6, "frequency_weight": 0.3, "hotness_weight": 0.1},
        "platforms": [
            {"id": "zhihu", "name": "知乎"},
            {"id": "weibo", "name": "微博"},
        ],
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(config_data, allow_unicode=True), encoding="utf-8"
    )
    (config_dir / "frequency_words.txt").write_text("", encoding="utf-8")


@pytest.fixture(autouse=True)
def _restore_main_config():
    """get_today_news_cards 会修改 main.CONFIG 这个全局变量，测试完必须还原，
    避免污染同一个 pytest 进程里跑的其它测试。"""
    original = main.CONFIG
    yield
    main.CONFIG = original


class TestGetTodayNewsCards:
    def test_no_data_today_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path)

        news_list, cards_per_batch, total_batches = get_today_news_cards()

        assert news_list == []
        assert cards_per_batch == 12
        assert total_batches == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_news_service.py -v --no-cov`
Expected: FAIL，`ModuleNotFoundError: No module named 'web_server.news_service'`

- [ ] **Step 3: 实现 `web_server/news_service.py`（先只支持空数据分支）**

创建 `web_server/news_service.py`：

```python
# coding=utf-8

from typing import Any, Dict, List, Tuple

import main
from mcp_server.services.cache_service import get_cache
from trendradar import config as trendradar_config
from trendradar.html_report import flatten_and_sort_news
from trendradar.notifier import prepare_report_data
from trendradar.utils import load_frequency_words


CACHE_KEY = "homepage_news_cards"
CACHE_TTL = 300


def _compute_total_batches(news_count: int, cards_per_batch: int) -> int:
    """计算总批次数，cards_per_batch <= 0 时视为不分批（1 批装下全部）"""
    if news_count == 0:
        return 0
    if cards_per_batch <= 0:
        return 1
    return (news_count + cards_per_batch - 1) // cards_per_batch


def get_today_news_cards() -> Tuple[List[Dict[str, Any]], int, int]:
    """组装首页用的今日新闻卡片列表

    跟真实报告用的是同一条计算链路（关键词过滤 -> count_word_frequency 分组初排 ->
    摊平按权重全局重排），只是最后不生成 HTML 文件，把排序好的数据交给调用方去渲染。

    Returns:
        (news_list, cards_per_batch, total_batches)
    """
    cache = get_cache()
    cached = cache.get(CACHE_KEY, ttl=CACHE_TTL)
    if cached is not None:
        return cached

    fresh_config = trendradar_config.load_config()
    main.CONFIG = fresh_config

    current_platform_ids = [p["id"] for p in fresh_config["PLATFORMS"]]

    all_results, id_to_name, title_info = main.read_all_today_titles(current_platform_ids)

    cards_per_batch = fresh_config.get("CARDS_PER_BATCH", 12)

    if not all_results:
        result: Tuple[List[Dict[str, Any]], int, int] = ([], cards_per_batch, 0)
        cache.set(CACHE_KEY, result)
        return result

    new_titles = main.detect_latest_new_titles(current_platform_ids)
    word_groups, filter_words, global_filters = load_frequency_words()

    stats, _total_titles = main.count_word_frequency(
        all_results,
        word_groups,
        filter_words,
        id_to_name,
        title_info,
        fresh_config["RANK_THRESHOLD"],
        new_titles,
        mode="daily",
        global_filters=global_filters,
    )

    report_data = prepare_report_data(fresh_config, stats, mode="daily")

    news_list = flatten_and_sort_news(
        report_data["stats"], fresh_config["WEIGHT_CONFIG"], fresh_config["RANK_THRESHOLD"]
    )

    total_batches = _compute_total_batches(len(news_list), cards_per_batch)

    result = (news_list, cards_per_batch, total_batches)
    cache.set(CACHE_KEY, result)
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_news_service.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: 写失败的测试（有数据时的完整链路）**

在 `tests/test_news_service.py` 文件末尾追加：

```python

    def test_returns_weight_sorted_news_with_keyword_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path)

        date_folder = format_date_folder()
        txt_dir = tmp_path / "output" / date_folder / "txt"
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / "12时00分.txt").write_text(
            "zhihu | 知乎\n"
            "1. AI大模型最新进展 [URL:http://a.com/1]\n"
            "2. 无关新闻标题 [URL:http://a.com/2]\n"
            "\n"
            "weibo | 微博\n"
            "1. 今日热搜AI话题 [URL:http://b.com/1]\n",
            encoding="utf-8",
        )

        # 只关注含"AI"的新闻，验证关键词过滤生效
        (tmp_path / "config" / "frequency_words.txt").write_text("AI", encoding="utf-8")

        news_list, cards_per_batch, total_batches = get_today_news_cards()

        titles = {item["title"] for item in news_list}
        assert titles == {"AI大模型最新进展", "今日热搜AI话题"}
        assert "无关新闻标题" not in titles
        assert cards_per_batch == 12
        assert total_batches == 1

        # zhihu 排名 1（min_rank=1）应该排在 weibo 排名 1 前面还是后面取决于权重计算，
        # 这里只断言两条都在，且返回的是一个扁平列表（不是按分组嵌套的结构）
        assert all("word" not in item for item in news_list)
        assert all("ranks" in item for item in news_list)

    def test_cache_hit_skips_recomputation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path)

        date_folder = format_date_folder()
        txt_dir = tmp_path / "output" / date_folder / "txt"
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / "12时00分.txt").write_text(
            "zhihu | 知乎\n1. 标题一 [URL:http://a.com/1]\n", encoding="utf-8"
        )

        first_result = get_today_news_cards()

        call_count = {"n": 0}
        original_read = main.read_all_today_titles

        def _counting_read(*args, **kwargs):
            call_count["n"] += 1
            return original_read(*args, **kwargs)

        monkeypatch.setattr(main, "read_all_today_titles", _counting_read)

        second_result = get_today_news_cards()

        assert call_count["n"] == 0  # 命中缓存，完全没再调用 read_all_today_titles
        assert second_result == first_result
```

在文件顶部 import 块里，`from web_server.news_service import get_today_news_cards` 那一行不用改；`main` 已经导入了。

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/test_news_service.py -v --no-cov`
Expected: `test_returns_weight_sorted_news_with_keyword_filter` 和 `test_cache_hit_skips_recomputation` 都失败（此时 `get_today_news_cards` 里"有数据"分支还没实现，只有空数据分支能返回结果——空数据测试会通过但新测试会失败，具体报错取决于函数当前只处理空 `all_results` 的情况；如果发现"没数据"分支已经能让这两个新测试意外通过，说明测试数据没有正确写入临时目录，先检查 `_write_config`/txt 文件路径是否正确）

- [ ] **Step 7: 确认"有数据"分支的实现已经在 Step 3 写好，运行测试确认全部通过**

Step 3 写的 `get_today_news_cards` 已经包含了完整的"有数据"分支实现（不是只有空分支），所以这一步不需要再写新代码，只需要重新跑测试：

Run: `uv run pytest tests/test_news_service.py -v --no-cov`
Expected: 3 个测试全部通过

如果失败，检查错误信息：
- 如果是 `KeyError`/`AttributeError` 之类，通常是 `_write_config` 里的 yaml 结构跟 `trendradar/config.py: load_config()` 期望的字段对不上，对照 `tests/test_config_load.py` 里的 `_make_config_data()` 核对字段是否齐全。
- 如果是关键词过滤没生效（`titles` 断言失败），检查 `frequency_words.txt` 的写入时机是不是在 `get_today_news_cards()` 调用**之前**（必须在调用前写好，因为 `load_frequency_words()` 是同步读取的，没有缓存这一层）。

- [ ] **Step 8: mypy 检查**

Run: `uv run mypy web_server/news_service.py`
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 9: Commit**

```bash
git add web_server/news_service.py tests/test_news_service.py
git commit -m "feat: add web_server/news_service.py for homepage news card data

Reuses main.py's read_all_today_titles/detect_latest_new_titles/
count_word_frequency plus trendradar.html_report.flatten_and_sort_news
so the homepage sees the exact same keyword-filtered, weight-sorted
news as the real report. Cached via mcp_server's shared cache
singleton (5 min TTL)."
```

---

## Task 3: 新闻卡片 CSS + "换一批" JS

**Files:**
- Modify: `web_server/static/css/dashboard.css`
- Modify: `web_server/static/js/dashboard.js`

**背景**：先把首页要用到的样式和交互脚本准备好（这一步不改模板/路由，所以现在还不会在页面上生效，Task 4 接上模板后才会真正用到）。

- [ ] **Step 1: 在 `dashboard.css` 末尾追加新闻卡片样式**

在 `web_server/static/css/dashboard.css` 文件末尾（`@media (max-width: 480px) { ... }` 这个块结束的 `}` 之后）追加：

```css

/* ============================================
   News Cards (首页新闻卡片流)
   ============================================ */

.news-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 14px 18px;
  margin-bottom: 20px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.news-toolbar-status {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.9rem;
  color: var(--color-text-secondary);
}

.news-toolbar-sep {
  color: var(--color-text-muted);
}

.platform-status-card {
  margin-bottom: 20px;
}

.news-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.news-card {
  position: relative;
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: var(--transition);
}

.news-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}

.news-card.new::after {
  content: "NEW";
  position: absolute;
  top: 10px;
  right: 10px;
  background: var(--color-warning);
  color: #fff;
  font-size: 0.65rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}

.news-card-number {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
  color: var(--color-text-muted);
  border-radius: 50%;
  font-size: 0.75rem;
  font-weight: 700;
}

.news-card-content {
  flex: 1;
  min-width: 0;
}

.news-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
  font-size: 0.75rem;
}

.news-card-source {
  color: var(--color-text-secondary);
  font-weight: 500;
}

.news-card-rank {
  color: #fff;
  background: var(--color-text-muted);
  font-size: 0.65rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 10px;
}

.news-card-rank.top { background: var(--color-danger); }
.news-card-rank.high { background: var(--color-warning); }

.news-card-time,
.news-card-count {
  color: var(--color-text-muted);
}

.news-card-count { color: var(--color-success); font-weight: 500; }

.news-card-title {
  font-size: 0.95rem;
  line-height: 1.4;
  color: var(--color-text);
}

.batch-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 16px 0 4px;
}

.batch-indicator {
  font-size: 0.85rem;
  color: var(--color-text-muted);
}

@media (max-width: 768px) {
  .news-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: 在 `dashboard.js` 里加"换一批"函数**

`web_server/static/js/dashboard.js` 文件末尾追加（在现有代码最后一行之后）：

```javascript

let currentNewsBatch = 0;

function showNextNewsBatch() {
  const grid = document.getElementById('newsGrid');
  if (!grid) return;
  const totalBatches = parseInt(grid.dataset.totalBatches || '1', 10);
  if (totalBatches <= 1) return;

  currentNewsBatch = (currentNewsBatch + 1) % totalBatches;

  grid.querySelectorAll('.news-card').forEach(function (card) {
    const batchIndex = parseInt(card.getAttribute('data-batch'), 10);
    card.style.display = batchIndex === currentNewsBatch ? '' : 'none';
  });

  const indicator = document.getElementById('currentNewsBatchNum');
  if (indicator) {
    indicator.textContent = currentNewsBatch + 1;
  }
}
```

- [ ] **Step 3: 确认没有语法错误**

Run: `node --check web_server/static/js/dashboard.js 2>&1 || python3 -c "print('node 不可用时跳过，改为下一步在浏览器里验证')"`

如果 `node` 命令在当前环境不可用（报 `command not found`），跳过这一步，Task 4 手动验证阶段会在浏览器里实际执行这段 JS 来间接验证语法正确性。

- [ ] **Step 4: Commit**

```bash
git add web_server/static/css/dashboard.css web_server/static/js/dashboard.js
git commit -m "feat: add news card grid CSS and batch-switch JS for homepage

Not wired into any template yet (that's the next commit) — this is
pure static asset additions, following the same data-batch attribute
+ display:none toggling pattern already used by the standalone HTML
report (trendradar/html_report.py)."
```

---

## Task 4: 首页路由 + 模板改造

**Files:**
- Modify: `web_server/server.py:330-376`（`dashboard()` 路由）
- Modify: `web_server/templates/dashboard.html`

- [ ] **Step 1: 改 `dashboard()` 路由**

在 `web_server/server.py` 顶部 import 块（第 25-30 行附近）里，`from web_server.config_manager import ConfigManager` 后面加一行：

```python
from web_server.news_service import get_today_news_cards
```

把 `web_server/server.py` 里整个 `dashboard()` 函数（第 330-376 行，从 `@app.get("/", response_class=HTMLResponse)` 到 `})` 结尾）：

```python
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard 概览页"""
    try:
        system_tools = get_system_tools()
        status = system_tools.get_system_status()
    except Exception as e:
        logger.exception(f"获取系统状态失败: {e}")
        status = {"system": {"version": VERSION}, "data": {}, "health": "unknown"}

    # 获取最新新闻统计：取最近一次采集报告的实际条数，
    # 而不是严格按自然日"今天"过滤——避免刚过零点、
    # 当天还没有新采集数据时统计错误地显示为 0
    try:
        latest_reports = get_report_list()
        if latest_reports:
            latest_groups = parse_news_txt(latest_reports[0]["txt_path"])
            total_news_today = sum(len(g["news_items"]) for g in latest_groups)
        else:
            total_news_today = 0
    except Exception as e:
        logger.exception(f"获取最新新闻统计失败: {e}")
        total_news_today = 0

    # 获取趋势话题（基于最近一次采集报告，同样不卡"今天"这个硬边界）
    try:
        trending_topics = get_trending_topics_from_latest_report(top_n=5)
    except Exception as e:
        logger.exception(f"获取趋势话题失败: {e}")
        trending_topics = []

    # 平台状态
    platforms = get_platform_status()

    # 最近阅读过的新闻
    read_history = load_read_history()[:8]
    for item in read_history:
        item["time_ago"] = format_relative_time(item.get("read_at", ""))

    return templates.TemplateResponse(request, "dashboard.html", {
        "version": VERSION,
        "status": status,
        "total_news_today": total_news_today,
        "trending_topics": trending_topics,
        "platforms": platforms,
        "read_history": read_history,
    })
```

改成：

```python
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard 概览页：今日新闻卡片流"""
    try:
        system_tools = get_system_tools()
        status = system_tools.get_system_status()
    except Exception as e:
        logger.exception(f"获取系统状态失败: {e}")
        status = {"system": {"version": VERSION}, "data": {}, "health": "unknown"}

    # 平台状态
    platforms = get_platform_status()

    # 今日新闻卡片（跟真实报告同一条计算链路：关键词过滤 + 权重排序）
    try:
        news_list, cards_per_batch, total_batches = get_today_news_cards()
    except Exception as e:
        logger.exception(f"获取今日新闻失败: {e}")
        news_list, cards_per_batch, total_batches = [], 12, 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "version": VERSION,
        "status": status,
        "platforms": platforms,
        "news_list": news_list,
        "cards_per_batch": cards_per_batch,
        "total_batches": total_batches,
    })
```

注意：`total_news_today`/`trending_topics`/`read_history` 三个变量和它们对应的计算逻辑（`parse_news_txt` 调用、`get_trending_topics_from_latest_report()` 调用、`load_read_history()`/`format_relative_time()` 调用）**从这个路由函数里删除**，但 `get_trending_topics_from_latest_report`、`load_read_history`、`format_relative_time`、`add_read_history` 这几个函数本身**不要删**（它们定义在 `server.py` 别处，函数体不动——`load_read_history`/`add_read_history` 还在被 `/api/read-history` 用，`format_relative_time` 是通用工具函数，`get_trending_topics_from_latest_report` 暂时没人调用了但保留着不算错误，是设计文档里明确写的"保留函数本身、只是不从 dashboard 路由调用"）。

- [ ] **Step 2: 改 `dashboard.html` 模板**

用 Read 工具先读一遍 `web_server/templates/dashboard.html` 现在的完整内容确认没有别人在这之间改过它，然后把整个文件替换成：

```html
{% extends "base.html" %}

{% set active_page = "dashboard" %}
{% block title %}首页 - DailyClaw{% endblock %}
{% block page_title %}首页{% endblock %}

{% block content %}
<div class="news-toolbar">
    <div class="news-toolbar-status">
        <span class="status-dot {{ 'ok' if status.health == 'healthy' else 'warn' }}"></span>
        <span>{{ '正常' if status.health == 'healthy' else status.health }}</span>
        <span class="news-toolbar-sep">·</span>
        <span>{{ status.data.total_storage or '0 MB' }}</span>
        <span class="news-toolbar-sep">·</span>
        <span>今日 {{ news_list | length }} 条新闻</span>
    </div>
    <button class="btn btn-primary btn-sm" id="btnCrawl" onclick="triggerCrawl()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 4v6h-6M1 20v-6h6"/>
            <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>
        </svg>
        手动爬取
    </button>
</div>

<div class="card platform-status-card">
    <div class="card-header">
        <div class="card-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-primary">
                <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>
                <line x1="8" y1="21" x2="16" y2="21"/>
                <line x1="12" y1="17" x2="12" y2="21"/>
            </svg>
            平台状态
        </div>
        <span class="card-badge">{{ platforms | selectattr('active') | list | length }}/{{ platforms | length }}</span>
    </div>
    <div class="card-body">
        <div class="platform-list">
            {% for platform in platforms %}
            <div class="platform-item">
                <span class="platform-dot {{ 'active' if platform.active else 'inactive' }}"></span>
                <span class="platform-name">{{ platform.name }}</span>
            </div>
            {% endfor %}
        </div>
    </div>
</div>

{% if news_list %}
<div class="news-grid" id="newsGrid" data-total-batches="{{ total_batches }}">
    {% for item in news_list %}
    {% set batch_index = loop.index0 // cards_per_batch if cards_per_batch > 0 else 0 %}
    <div class="news-card{{ ' new' if item.is_new else '' }}" data-batch="{{ batch_index }}"{% if batch_index != 0 %} style="display:none"{% endif %}>
        <div class="news-card-number">{{ (loop.index0 % cards_per_batch) + 1 if cards_per_batch > 0 else loop.index }}</div>
        <div class="news-card-content">
            <div class="news-card-header">
                <span class="news-card-source">{{ item.source_name }}</span>
                {% if item.ranks %}
                {% set min_rank = item.ranks | min %}
                {% set max_rank = item.ranks | max %}
                {% set rank_class = 'top' if min_rank <= 3 else ('high' if min_rank <= item.rank_threshold else '') %}
                <span class="news-card-rank {{ rank_class }}">{{ min_rank if min_rank == max_rank else min_rank ~ '-' ~ max_rank }}</span>
                {% endif %}
                {% if item.time_display %}
                <span class="news-card-time">{{ item.time_display | replace(" ~ ", "~") | replace("[", "") | replace("]", "") }}</span>
                {% endif %}
                {% if item.count and item.count > 1 %}
                <span class="news-card-count">{{ item.count }}次</span>
                {% endif %}
            </div>
            <div class="news-card-title">
                {% set link_url = item.mobile_url or item.url %}
                {% if link_url %}
                <a href="{{ link_url }}" target="_blank" rel="noopener noreferrer" class="report-news-link" data-title="{{ item.title }}" data-platform="{{ item.source_name }}">{{ item.title }}</a>
                {% else %}
                {{ item.title }}
                {% endif %}
            </div>
        </div>
    </div>
    {% endfor %}
</div>

{% if total_batches > 1 %}
<div class="batch-controls">
    <button class="btn btn-primary" onclick="showNextNewsBatch()">换一批</button>
    <span class="batch-indicator">第 <span id="currentNewsBatchNum">1</span> / 共 {{ total_batches }} 批</span>
</div>
{% endif %}

{% else %}
<div class="empty-state empty-state-large">
    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
        <circle cx="12" cy="12" r="3"/>
    </svg>
    <h3>今天还没有新闻数据</h3>
    <p>点击右上角"手动爬取"抓取今天的第一批数据</p>
</div>
{% endif %}
{% endblock %}

{% block extra_js %}
<script>
async function triggerCrawl() {
    const btn = document.getElementById('btnCrawl');
    const originalText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin">
            <path d="M23 4v6h-6M1 20v-6h6"/>
            <path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/>
        </svg>
        爬取中...
    `;

    try {
        const res = await fetch('/api/crawl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ platforms: null, save_to_local: true })
        });
        const data = await res.json();

        if (data.success) {
            btn.innerHTML = `
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="20 6 9 17 4 12"/>
                </svg>
                完成 (${data.total_news}条)
            `;
            setTimeout(() => location.reload(), 1500);
        } else {
            btn.innerHTML = originalText;
            alert('爬取失败: ' + (data.error?.message || '未知错误'));
        }
    } catch (e) {
        btn.innerHTML = originalText;
        alert('请求失败: ' + e.message);
    }

    setTimeout(() => {
        btn.disabled = false;
        if (btn.innerHTML.indexOf('完成') === -1) {
            btn.innerHTML = originalText;
        }
    }, 3000);
}
</script>
{% endblock %}
```

（`triggerCrawl()` 是从原来 `dashboard.html` 里原样搬过来的，只是按钮文案从"手动爬取"改成图标+文字更紧凑的版本，逻辑完全不变——沿用相同的 `/api/crawl` 调用。）

- [ ] **Step 3: 手动渲染模板做一次快速验证（不启动完整服务器）**

`base.html` 里没有任何地方用到 FastAPI 的 `request` 对象（已经用 `grep -n "request\." web_server/templates/base.html` 确认过是空输出），所以可以绕开 FastAPI，直接用 `jinja2.Environment` 渲染：

Run:

```bash
uv run python -c "
import jinja2

env = jinja2.Environment(loader=jinja2.FileSystemLoader('web_server/templates'))
template = env.get_template('dashboard.html')

news_list = [
    {'title': f'测试新闻{i}', 'source_name': '知乎', 'time_display': '10:00', 'count': 1,
     'ranks': [i % 10 + 1], 'rank_threshold': 5, 'url': f'http://example.com/{i}',
     'mobile_url': '', 'is_new': i < 3}
    for i in range(15)
]

html = template.render(
    active_page='dashboard',
    version='1.0.0',
    status={'health': 'healthy', 'data': {'total_storage': '1.2 MB'}, 'system': {'version': '1.0.0'}},
    platforms=[{'id': 'zhihu', 'name': '知乎', 'active': True}],
    news_list=news_list,
    cards_per_batch=12,
    total_batches=2,
)
assert 'news-grid' in html
assert 'news-card new' in html
assert '换一批' in html
assert '共 2 批' in html
print('模板渲染 OK，长度:', len(html))
"
```

Expected: 打印 `模板渲染 OK，长度: ...`，无异常抛出。

- [ ] **Step 4: 运行完整测试套件确认没有回归**

Run: `uv run pytest --ignore=tests/test_main.py -q`
Expected: 全部通过

- [ ] **Step 5: mypy 检查**

Run: `uv run mypy web_server/server.py`
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 6: Commit**

```bash
git add web_server/server.py web_server/templates/dashboard.html
git commit -m "feat: turn Web UI homepage into a news card feed

dashboard() now fetches today's keyword-filtered, weight-sorted news
via web_server/news_service.py instead of showing ops-dashboard-only
stats. Drops the 今日统计/热门关注词/最近阅读 blocks; read-history
click tracking still works via the same report-news-link JS listener,
just without a dedicated list on the homepage."
```

---

## Task 5: 简化 `/reports` 页面，去掉按平台分组的内联预览

**Files:**
- Modify: `web_server/server.py`（`reports_page()` 函数）
- Modify: `web_server/templates/reports.html`
- Modify: `web_server/static/css/dashboard.css`（去掉现在变成死代码的几个 class）

- [ ] **Step 1: 改 `reports_page()`**

`web_server/server.py` 里的 `reports_page()`（第 379-405 行附近）现在是：

```python
@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    """报告列表页"""
    reports = get_report_list()

    # 解析每份报告的文本内容，提取新闻标题与链接
    for r in reports:
        try:
            r["groups"] = parse_news_txt(r["txt_path"])
            r["total_items"] = sum(len(g["news_items"]) for g in r["groups"])
        except Exception as e:
            logger.exception(f"解析报告内容失败: {r['txt_path']}: {e}")
            r["groups"] = []
            r["total_items"] = 0

    # 按日期分组
    grouped = {}
    for r in reports:
        date = r["date"]
        if date not in grouped:
            grouped[date] = []
        grouped[date].append(r)

    return templates.TemplateResponse(request, "reports.html", {
        "grouped_reports": grouped,
        "total_reports": len(reports),
    })
```

改成（去掉 `r["groups"]`，只保留条数统计）：

```python
@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    """报告列表页"""
    reports = get_report_list()

    # 统计每份报告的新闻条数（不再解析成按平台分组的结构，
    # 详细内容通过"查看完整报告"链接到实际生成的报告文件里看）
    for r in reports:
        try:
            groups = parse_news_txt(r["txt_path"])
            r["total_items"] = sum(len(g["news_items"]) for g in groups)
        except Exception as e:
            logger.exception(f"统计报告条数失败: {r['txt_path']}: {e}")
            r["total_items"] = 0

    # 按日期分组
    grouped = {}
    for r in reports:
        date = r["date"]
        if date not in grouped:
            grouped[date] = []
        grouped[date].append(r)

    return templates.TemplateResponse(request, "reports.html", {
        "grouped_reports": grouped,
        "total_reports": len(reports),
    })
```

- [ ] **Step 2: 改 `reports.html`**

把 `web_server/templates/reports.html` 整个文件替换成：

```html
{% extends "base.html" %}

{% set active_page = "reports" %}
{% block title %}报告 - DailyClaw{% endblock %}
{% block page_title %}报告<span class="page-subtitle">共 {{ total_reports }} 份</span>{% endblock %}

{% block content %}
{% if grouped_reports %}
    {% for date, reports in grouped_reports.items() %}
    <div class="report-group">
        <div class="report-group-header">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                <line x1="16" y1="2" x2="16" y2="6"/>
                <line x1="8" y1="2" x2="8" y2="6"/>
                <line x1="3" y1="10" x2="21" y2="10"/>
            </svg>
            {{ date }}
            <span class="report-count">{{ reports | length }} 份</span>
        </div>

        <div class="report-list">
            {% for report in reports %}
            <a href="/reports/view?path={{ report.path }}" class="report-list-item">
                <span class="report-detail-time">{{ report.time_label }}</span>
                <span class="report-detail-count">{{ report.total_items }} 条新闻</span>
                <span class="report-detail-link">查看完整报告 ↗</span>
            </a>
            {% endfor %}
        </div>
    </div>
    {% endfor %}
{% else %}
<div class="empty-state empty-state-large">
    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
    </svg>
    <h3>暂无报告</h3>
    <p>请先执行爬取任务以生成报告</p>
    <a href="/" class="btn btn-primary">前往首页</a>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: 在 `dashboard.css` 里新增 `.report-list`/`.report-list-item` 样式，删掉变成死代码的几个 class**

先加新样式：在 `.report-detail-body { ... }` 规则块（现在应该在 `.report-detail-link` 之后、`.report-platform-grid` 之前）后面插入：

```css

.report-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.report-list-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  transition: var(--transition);
}

.report-list-item:hover {
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}
```

然后删除现在已经没有任何模板在用的这几个规则块（用 `grep -n` 先确认它们在 `web_server/templates/*.html` 里已经没有引用，再删除对应的 CSS 规则）：

Run: `grep -rn "report-detail\b\|report-platform-grid\|report-platform-group\|report-platform-header\|report-platform-count\|report-news-list\b" web_server/templates/*.html`
Expected: 无输出（说明这些 class 已经没有任何模板在用了）

确认无输出后，删除 `dashboard.css` 里以下几个规则块（用 `grep -n "^\.report-detail\b\|^\.report-detail-summary\|^\.report-detail-summary::\|^\.report-detail\[open\]\|^\.report-detail-time\|^\.report-detail-count\|^\.report-detail-link\|^\.report-detail-body\|^\.report-platform-grid\|^\.report-platform-group\|^\.report-platform-header\|^\.report-platform-count\|^\.report-news-list\b\|^\.report-news-list li" web_server/static/css/dashboard.css` 定位精确行号后删除对应块）：
- `.report-detail { ... }`
- `.report-detail-summary { ... }`
- `.report-detail-summary::-webkit-details-marker { ... }`
- `.report-detail-summary::before { ... }`
- `.report-detail[open] > .report-detail-summary::before { ... }`
- `.report-detail-time { ... }`（注意：新的 `report-list-item` 里还继续用这个 class 名字，**不要删**——见下方说明）
- `.report-detail-count { ... }`（同上，继续用，不要删）
- `.report-detail-link { ... }`（同上，继续用，不要删）
- `.report-detail-body { ... }`
- `.report-platform-grid { ... }`
- `.report-platform-group { ... }`
- `.report-platform-header { ... }`
- `.report-platform-count { ... }`
- `.report-news-list { ... }`
- `.report-news-list li { ... }`

也就是只删 `.report-detail`（容器本身）、`.report-detail-summary` 相关的几条、`.report-detail-body`、以及所有 `.report-platform-*`、`.report-news-list*`——`.report-detail-time`/`.report-detail-count`/`.report-detail-link` 这三个类名在新的 `reports.html` 里还在用（作为 `.report-list-item` 内部子元素的样式），**保留不动**。

同时删除 `@media (max-width: 768px)` 块里这三行（现在指向已删除的类，且 `.report-detail-summary` 也一起没了）：

```css
  .report-platform-grid {
    grid-template-columns: 1fr;
  }

  .report-detail-summary {
    flex-wrap: wrap;
  }

  .report-detail-link {
    margin-left: 0;
  }
```

删除前先确认：`.report-detail-link` 这个类名在新的 `.report-list-item` 里还在用，所以**这条媒体查询规则要不要留取决于新样式在窄屏下是否也需要 `margin-left: 0` 这个调整**——`report-list-item` 用的是 `display: flex; align-items: center; gap: 12px;`（没有 `margin-left: auto` 那种撑开布局），所以这条媒体查询规则对新样式没有意义，可以一并删除；但因为类名复用了，删除的时候只删 `@media` 块里这一小段，不要动 `.report-detail-link` 本身的基础定义（那个还在被使用，见上一段）。

- [ ] **Step 4: 运行完整测试套件**

Run: `uv run pytest --ignore=tests/test_main.py -q`
Expected: 全部通过（这个任务没有 Python 逻辑测试，主要靠不报错 + Step 5 的手动验证）

- [ ] **Step 5: 手动验证模板渲染**

Run:

```bash
uv run python -c "
import jinja2

env = jinja2.Environment(loader=jinja2.FileSystemLoader('web_server/templates'))
template = env.get_template('reports.html')

html = template.render(
    active_page='reports',
    total_reports=1,
    grouped_reports={
        '2026年09月18日': [
            {'time_label': '14:30', 'total_items': 255, 'path': '/output/2026年09月18日/html/14时30分.html'},
        ]
    },
)
assert 'report-platform-grid' not in html
assert 'report-list-item' in html
assert '255 条新闻' in html
assert '查看完整报告' in html
print('reports.html 渲染 OK')
"
```

Expected: 打印 `reports.html 渲染 OK`

- [ ] **Step 6: Commit**

```bash
git add web_server/server.py web_server/templates/reports.html web_server/static/css/dashboard.css
git commit -m "refactor: simplify /reports to a plain list, drop platform-grouped preview

reports.html no longer inline-expands each report into a per-platform
news list (the old .report-platform-grid) — that duplicated, and was
inconsistent with, the actual card-grid report you get to via '查看
完整报告'. Report entries are now a flat list; total_items count is
kept."
```

---

## Task 6: 全量验证

**Files:** 无新增/修改，纯验证

- [ ] **Step 1: 全量测试**

Run: `uv run pytest --ignore=tests/test_main.py -q`
Expected: 全部通过，覆盖率达标

- [ ] **Step 2: mypy 全量检查**

Run: `uv run mypy trendradar/ main.py web_server/`
Expected: `Success: no issues found in N source files`

- [ ] **Step 3: 确认死代码 CSS class 真的清干净了**

Run: `grep -rn "report-platform-grid\|report-platform-group\|report-platform-header\|report-platform-count" web_server/`
Expected: 无输出

Run: `grep -c "topic-list\|read-history-list" web_server/templates/dashboard.html`
Expected: `0`（这两个 class 在 Task 4 重写 `dashboard.html` 时应该已经自然消失，这里是最后确认一遍；如果 `dashboard.css` 里还留着 `.topic-*`/`.read-history-*` 规则块也没关系——它们虽然现在没有模板引用了，但不是本次改动直接产生的新死代码问题的核心，值不值得顺手清理由实现者自行判断，不强制要求）

- [ ] **Step 4: 端到端手动验证（真实启动 Web 服务）**

Run: `./start-web.sh` 放到后台运行（比如 `(nohup ./start-web.sh > /tmp/web_verify.log 2>&1 &)`，等 3-4 秒让服务起来）。

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:18080/`
Expected: `200`

Run: `curl -s http://localhost:18080/ -o /tmp/homepage.html && grep -c "news-grid" /tmp/homepage.html`
Expected: 至少 `1`（如果今天 `output/` 目录下已经有真实抓取数据，比如这个 session 前面手动跑过一次 `main.py`，应该能看到真实的新闻卡片；如果没有数据，会看到空状态提示"今天还没有新闻数据"，这也是正确行为，检查 `grep -c "今天还没有新闻数据" /tmp/homepage.html` 应该是 `1`）

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:18080/reports`
Expected: `200`，用 Read 工具读一下 `curl -s http://localhost:18080/reports -o /tmp/reports.html` 的输出，确认没有 `report-platform-grid` 字样。

验证完后停止后台服务：`lsof -ti tcp:18080 | xargs -r kill -9`

- [ ] **Step 5: 清理验证过程中产生的临时文件**

Run: `rm -f /tmp/homepage.html /tmp/reports.html /tmp/web_verify.log`

Run: `git status --short`
Expected: 干净（除了这次 plan 之前 session 里已经存在的、跟这次改动无关的未跟踪文件，比如真实抓取产生的 `output/<日期>/` 目录——不要动它们）

- [ ] **Step 6: 如果验证阶段发现需要小修小补**

如果 Step 3/4 发现问题，直接在对应文件修，然后：

```bash
git add -A
git commit -m "fix: address issues found in homepage news card manual verification"
```

如果没有问题，这一步无需操作，Task 6 结束即整个功能完成。

---

## Self-Review 记录

- **Spec 覆盖检查**：设计文档的"架构"（`news_service.py` 复用 main.py 管道 + 缓存）对应 Task 2；"数据流细节"（mode="daily"、关键词过滤、空状态、缓存 key/TTL）对应 Task 2 的实现和测试；"页面改造"里首页部分对应 Task 3+4，`/reports` 部分对应 Task 5；"一并处理"（`parse_news_txt` 调用去留、`main.CONFIG` 重新赋值范围说明）在 Task 5 Step 1 和背景说明里都有体现；"不受影响的部分"通过 Task 1-5 里都没有触碰 `count_word_frequency`/`prepare_report_data`/`trendradar/notifier/`/`config_manager.py` 来保证；"测试与验证"对应每个 Task 内嵌的测试 + Task 6。全部覆盖。
- **占位符扫描**：全文没有 TBD/TODO/"类似 Task N"这类占位表述；Task 3 Step 3 的 `node --check` 那一步允许"不可用就跳过"，但给出了明确的替代验证路径（Task 4 手动验证），不是悬空的占位符。
- **类型一致性核对**：`get_today_news_cards() -> Tuple[List[Dict[str, Any]], int, int]` 的返回值形状（`news_list, cards_per_batch, total_batches`）在 Task 2 的定义、测试断言、Task 4 路由里的解包 `news_list, cards_per_batch, total_batches = get_today_news_cards()`、以及模板里对 `cards_per_batch`/`total_batches` 变量的使用之间保持一致。`flatten_and_sort_news`（Task 1 改名后）在 Task 2 的 import 和调用里用的是改名后的新名字，没有遗留 `_flatten_and_sort_news` 的引用。
