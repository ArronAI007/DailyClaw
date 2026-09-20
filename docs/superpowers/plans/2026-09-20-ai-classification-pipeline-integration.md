# 接入现有爬取/报告管线（子项目 2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `config.yaml` 的 `filter.method: "ai"` 真正生效——在 `main.py` 的"当日汇总"报告路径和 `web_server/news_service.py`（Web UI 首页）两个入口，按配置在 AI 分类（子项目 1 的 `AIFilterPipeline`）和现有关键词匹配（`count_word_frequency`）之间二选一，AI 失败时自动降级。

**Architecture:** 新增 `main.py::get_daily_stats()` 作为统一入口（返回值形状跟 `count_word_frequency` 完全一致），内部按 `CONFIG["FILTER"]["METHOD"]` 分支；新增 `main.py::_flatten_titles_for_ai()` 把按平台分组的数据打平成 `AIFilterPipeline.run()` 要的格式。`main.py::_run_analysis_pipeline`（当日/当前/增量三种模式共用的入口）和 `web_server/news_service.py::get_today_news_cards()` 都改为调用 `get_daily_stats()`。

**Tech Stack:** Python 3.10+, pytest（LLM/AIFilterPipeline 全部 mock，不产生真实 API 费用）

**参考设计文档：** `docs/superpowers/specs/2026-09-20-ai-classification-pipeline-integration-design.md`

**范围边界：** 只接入 `mode="daily"` 这一条路径。`current`/`incremental` 模式不受影响，继续固定走 `count_word_frequency`。Web UI 分类 Tab 是子项目 3 的范围，本计划不涉及任何前端改动。

---

### Task 1: `AIFilterPipeline` 补上 `percentage` 字段

**Files:**
- Modify: `trendradar/ai/filter_pipeline.py`
- Modify: `tests/test_ai_filter_pipeline.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_ai_filter_pipeline.py` 的 `TestAIFilterPipelineRun` 类里追加（放在 `test_min_score_filters_low_relevance_results` 后面）：

```python
    def test_percentage_field_computed_correctly(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技和财经"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"
        mock_filter.extract_tags.return_value = [
            {"tag": "科技", "description": ""},
            {"tag": "财经", "description": ""},
        ]

        def fake_classify(titles_for_ai, active_tags, interests_content):
            tag_map = {t["tag"]: t["id"] for t in active_tags}
            results = []
            for item in titles_for_ai:
                tag = "科技" if item["title"] in ("科技新闻一", "科技新闻二", "科技新闻三") else "财经"
                results.append({
                    "title": item["title"], "tag": tag,
                    "tag_id": tag_map[tag], "relevance_score": 0.9,
                })
            return results

        mock_filter.classify_batch.side_effect = fake_classify

        pipeline = _make_pipeline(store, mock_filter)
        all_titles = [
            _title_entry("科技新闻一"), _title_entry("科技新闻二"),
            _title_entry("科技新闻三"), _title_entry("财经新闻一"),
        ]

        result = pipeline.run(all_titles)

        tech_group = next(g for g in result.stats if g["word"] == "科技")
        finance_group = next(g for g in result.stats if g["word"] == "财经")
        assert tech_group["percentage"] == 75.0
        assert finance_group["percentage"] == 25.0

    def test_percentage_is_zero_when_total_processed_is_zero(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = None

        pipeline = _make_pipeline(store, mock_filter)
        result = pipeline.run([])

        # total_processed=0 走的是失败分支（兴趣描述缺失），stats 本来就是空列表，
        # 这里只是确认不会因为除以零而抛异常
        assert result.stats == []
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_pipeline.py -v -k percentage`
Expected: FAIL，`KeyError: 'percentage'`

- [ ] **Step 3: 实现**

打开 `trendradar/ai/filter_pipeline.py`，找到 `_build_result` 方法里的这一段（`stats = sorted(...)` 那一行）：

```python
        stats = sorted(tag_groups.values(), key=lambda g: g["position"])
        total_matched = sum(g["count"] for g in stats)

        return AIFilterResult(
```

改成：

```python
        stats = sorted(tag_groups.values(), key=lambda g: g["position"])
        total_matched = sum(g["count"] for g in stats)

        for group in stats:
            group["percentage"] = (
                round(group["count"] / total_processed * 100, 2)
                if total_processed > 0
                else 0
            )

        return AIFilterResult(
```

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_pipeline.py -v`
Expected: 11 passed（原有 9 个 + 新增 2 个）

- [ ] **Step 5: Commit**

```bash
git add trendradar/ai/filter_pipeline.py tests/test_ai_filter_pipeline.py
git commit -m "feat: add percentage field to AIFilterPipeline stats output"
```

---

### Task 2: `main.py::_flatten_titles_for_ai()`

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_main.py` 末尾追加一个新的测试类：

```python
class TestFlattenTitlesForAI:
    def test_maps_fields_correctly(self):
        all_results = {
            "zhihu": {
                "标题一": {"ranks": [3], "url": "http://a.com/1", "mobileUrl": "http://m.a.com/1"},
            },
        }
        title_info = {
            "zhihu": {
                "标题一": {
                    "first_time": "10:00", "last_time": "10:30", "count": 2,
                    "ranks": [1, 3], "url": "http://a.com/1", "mobileUrl": "http://m.a.com/1",
                },
            },
        }
        id_to_name = {"zhihu": "知乎"}
        new_titles = {"zhihu": {"标题一": {}}}

        flat = main._flatten_titles_for_ai(all_results, title_info, id_to_name, new_titles, rank_threshold=5)

        assert len(flat) == 1
        item = flat[0]
        assert item["title"] == "标题一"
        assert item["source_name"] == "知乎"
        assert item["ranks"] == [1, 3]
        assert item["rank_threshold"] == 5
        assert item["url"] == "http://a.com/1"
        assert item["mobileUrl"] == "http://m.a.com/1"
        assert item["count"] == 2
        assert item["time_display"] == "[10:00 ~ 10:30]"
        assert item["is_new"] is True

    def test_defaults_ranks_to_99_when_missing(self):
        all_results = {"zhihu": {"标题": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"标题": {}}}

        flat = main._flatten_titles_for_ai(all_results, title_info, {}, None, rank_threshold=5)

        assert flat[0]["ranks"] == [99]
        assert flat[0]["is_new"] is False

    def test_flattens_multiple_platforms(self):
        all_results = {
            "zhihu": {"标题一": {"url": "", "mobileUrl": ""}},
            "weibo": {"标题二": {"url": "", "mobileUrl": ""}},
        }
        title_info = {"zhihu": {"标题一": {}}, "weibo": {"标题二": {}}}
        id_to_name = {"zhihu": "知乎", "weibo": "微博"}

        flat = main._flatten_titles_for_ai(all_results, title_info, id_to_name, None, rank_threshold=5)

        titles = {item["title"]: item["source_name"] for item in flat}
        assert titles == {"标题一": "知乎", "标题二": "微博"}

    def test_no_keyword_filtering_applied(self):
        """AI 模式的核心前提：打平不做任何关键词过滤，全部标题都进来"""
        all_results = {
            "zhihu": {
                "无关新闻标题": {"url": "", "mobileUrl": ""},
                "另一条无关新闻": {"url": "", "mobileUrl": ""},
            },
        }
        title_info = {"zhihu": {"无关新闻标题": {}, "另一条无关新闻": {}}}

        flat = main._flatten_titles_for_ai(all_results, title_info, {}, None, rank_threshold=5)

        assert len(flat) == 2
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_main.py -v -k TestFlattenTitlesForAI`
Expected: FAIL，`AttributeError: module 'main' has no attribute '_flatten_titles_for_ai'`

（注意：这个仓库在这台机器上有一个已知的、跟本次改动无关的预存在环境问题——一个残留的 `PYTHONPATH` 环境变量会在跑 `tests/test_main.py` 时污染 `import main`，报 `ModuleNotFoundError: No module named 'openai'`。所有涉及这个测试文件的命令都要带 `env -u PYTHONPATH` 前缀，跟本计划别的 task 一样。）

- [ ] **Step 3: 实现**

在 `main.py` 里，找到 `count_word_frequency` 函数定义（`def count_word_frequency(` 那一行）的**前面**，插入：

```python
def _flatten_titles_for_ai(
    all_results: Dict,
    title_info: Dict,
    id_to_name: Dict,
    new_titles: Optional[Dict],
    rank_threshold: int,
) -> List[Dict]:
    """把按平台分组的当天新闻打平成 AIFilterPipeline.run() 需要的平铺列表。

    不做任何关键词过滤——AI 模式的目标就是让用户看到全部新闻，只是分好类。
    """
    new_titles = new_titles or {}
    flat: List[Dict] = []

    for source_id, titles_data in all_results.items():
        source_name = id_to_name.get(source_id, source_id)
        new_titles_for_source = new_titles.get(source_id, {})

        for title, title_data in titles_data.items():
            info = title_info.get(source_id, {}).get(title, {})

            ranks = info.get("ranks") or title_data.get("ranks", []) or [99]
            url = info.get("url", title_data.get("url", ""))
            mobile_url = info.get("mobileUrl", title_data.get("mobileUrl", ""))
            first_time = info.get("first_time", "")
            last_time = info.get("last_time", "")
            count = info.get("count", 1)

            flat.append({
                "title": title,
                "source_name": source_name,
                "first_time": first_time,
                "last_time": last_time,
                "time_display": format_time_display(first_time, last_time),
                "count": count,
                "ranks": ranks,
                "rank_threshold": rank_threshold,
                "url": url,
                "mobileUrl": mobile_url,
                "is_new": title in new_titles_for_source,
            })

    return flat
```

（`format_time_display` 已经在 `main.py` 里定义，直接复用，不需要新增 import。）

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_main.py -v -k TestFlattenTitlesForAI`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add _flatten_titles_for_ai to bridge main.py data shapes into AIFilterPipeline"
```

---

### Task 3: `main.py::get_daily_stats()`

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_main.py` 末尾追加：

```python
class TestGetDailyStats:
    @pytest.fixture(autouse=True)
    def _restore_config(self):
        original = dict(main.CONFIG)
        yield
        main.CONFIG.clear()
        main.CONFIG.update(original)

    def test_keyword_mode_matches_count_word_frequency_directly(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "keyword"}

        all_results = {"zhihu": {"AI新闻": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"AI新闻": {}}}
        id_to_name = {"zhihu": "知乎"}
        word_groups = [{"required": [], "normal": ["AI"], "group_key": "AI"}]

        direct_stats, direct_total = main.count_word_frequency(
            all_results, word_groups, [], id_to_name, title_info, 5, {}, mode="daily",
        )
        via_helper_stats, via_helper_total = main.get_daily_stats(
            all_results, word_groups, [], id_to_name, title_info, 5, {},
        )

        assert via_helper_stats == direct_stats
        assert via_helper_total == direct_total

    def test_ai_mode_success_skips_keyword_matching(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "ai"}
        main.CONFIG["AI"] = {}
        main.CONFIG["AI_FILTER"] = {}

        from trendradar.ai.filter_pipeline import AIFilterResult

        fake_stats = [{"word": "科技", "count": 1, "position": 1, "percentage": 100.0, "titles": []}]

        class _FakePipeline:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, all_titles):
                return AIFilterResult(
                    stats=fake_stats, total_matched=1, total_processed=1, success=True,
                )

        monkeypatch.setattr(main, "AIFilterPipeline", _FakePipeline)

        count_word_frequency_called = {"n": 0}
        original = main.count_word_frequency

        def _counting(*args, **kwargs):
            count_word_frequency_called["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(main, "count_word_frequency", _counting)

        all_results = {"zhihu": {"标题": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"标题": {}}}

        stats, total = main.get_daily_stats(all_results, [], [], {}, title_info, 5, {})

        assert stats == fake_stats
        assert total == 1
        assert count_word_frequency_called["n"] == 0

    def test_ai_mode_falls_back_to_keyword_on_failure(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "ai"}
        main.CONFIG["AI"] = {}
        main.CONFIG["AI_FILTER"] = {}

        from trendradar.ai.filter_pipeline import AIFilterResult

        class _FailingPipeline:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, all_titles):
                return AIFilterResult(success=False, error="兴趣描述文件为空或不存在")

        monkeypatch.setattr(main, "AIFilterPipeline", _FailingPipeline)

        all_results = {"zhihu": {"AI新闻": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"AI新闻": {}}}
        id_to_name = {"zhihu": "知乎"}
        word_groups = [{"required": [], "normal": ["AI"], "group_key": "AI"}]

        stats, total = main.get_daily_stats(
            all_results, word_groups, [], id_to_name, title_info, 5, {},
        )

        # 降级成功：跟直接调用 count_word_frequency 的结果一致
        expected_stats, expected_total = main.count_word_frequency(
            all_results, word_groups, [], id_to_name, title_info, 5, {}, mode="daily",
        )
        assert stats == expected_stats
        assert total == expected_total
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_main.py -v -k TestGetDailyStats`
Expected: FAIL，`AttributeError: module 'main' has no attribute 'get_daily_stats'`

- [ ] **Step 3: 添加 import**

在 `main.py` 顶部的 import 区域（`from trendradar.logging_config import configure_logging, get_logger` 那一行后面）追加：

```python
from trendradar.ai.filter_pipeline import AIFilterPipeline
from trendradar.storage.ai_filter_store import AIFilterStore
```

- [ ] **Step 4: 实现**

紧跟在 Task 2 新增的 `_flatten_titles_for_ai` 函数后面（也就是 `count_word_frequency` 定义之前）插入：

```python
def get_daily_stats(
    all_results: Dict,
    word_groups: List[Dict],
    filter_words: List[str],
    id_to_name: Dict,
    title_info: Dict,
    rank_threshold: int,
    new_titles: Optional[Dict] = None,
    global_filters: Optional[List[str]] = None,
) -> Tuple[List[Dict], int]:
    """当日汇总模式的统计入口。

    filter.method == "ai" 时走 AI 分类（AIFilterPipeline），失败时自动降级为
    关键词匹配（count_word_frequency），并记录一条 warning 日志。
    filter.method == "keyword"（默认）或其他任意值时直接走关键词匹配，行为与
    改动前完全一致。

    返回值结构与 count_word_frequency 完全一致：(stats, total_titles)。
    """
    if CONFIG["FILTER"]["METHOD"] == "ai":
        all_titles = _flatten_titles_for_ai(
            all_results, title_info, id_to_name, new_titles, rank_threshold
        )
        pipeline = AIFilterPipeline(CONFIG["AI"], CONFIG["AI_FILTER"], AIFilterStore())
        result = pipeline.run(all_titles)
        if result.success:
            return result.stats, result.total_processed
        logger.warning(f"AI 分类失败（{result.error}），本次降级为关键词匹配")

    return count_word_frequency(
        all_results, word_groups, filter_words, id_to_name, title_info,
        rank_threshold, new_titles, mode="daily", global_filters=global_filters,
    )
```

- [ ] **Step 5: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_main.py -v -k TestGetDailyStats`
Expected: 3 passed

- [ ] **Step 6: 跑一下 test_main.py 全部测试，确认没有破坏现有的**

Run: `env -u PYTHONPATH uv run pytest tests/test_main.py -v`
Expected: 全部 passed（含 Task 2 新增的 `TestFlattenTitlesForAI` 和这里新增的 `TestGetDailyStats`）

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: add get_daily_stats entry point with AI/keyword branching and fallback"
```

---

### Task 4: 接入 `main.py::_run_analysis_pipeline`

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

`_run_analysis_pipeline` 是 `NewsAnalyzer` 类的实例方法，目前完全没有单元测试（它耦合了 HTML 生成、通知发送等重副作用，一直是靠手动/集成方式验证的）。这个 task 延续现有约定：只测新加的分支逻辑本身，不引入对整个 `NewsAnalyzer` 类的测试基础设施。测试方式是直接调用未绑定方法 `main.NewsAnalyzer._run_analysis_pipeline(fake_self, ...)`，`fake_self` 用 `MagicMock()` 代替，只设置这个方法真正用到的 `self.rank_threshold`/`self.update_info` 属性，避免实例化整个 `NewsAnalyzer`（会触发 `DataFetcher` 初始化等不必要的副作用）。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_main.py` 末尾追加：

```python
class TestRunAnalysisPipelineAIRouting:
    @pytest.fixture(autouse=True)
    def _restore_config(self):
        original = dict(main.CONFIG)
        yield
        main.CONFIG.clear()
        main.CONFIG.update(original)

    def _fake_self(self):
        fake_self = MagicMock()
        fake_self.rank_threshold = 5
        fake_self.update_info = None
        return fake_self

    def test_daily_mode_routes_through_get_daily_stats(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "keyword"}
        monkeypatch.setattr(main, "generate_html_report", lambda *a, **kw: "fake.html")

        called = {"n": 0}
        original = main.get_daily_stats

        def _counting(*args, **kwargs):
            called["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(main, "get_daily_stats", _counting)

        stats, html_file = main.NewsAnalyzer._run_analysis_pipeline(
            self._fake_self(), {}, "daily", {}, {}, [], [], {},
        )

        assert called["n"] == 1
        assert html_file == "fake.html"

    def test_current_mode_does_not_route_through_get_daily_stats(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "ai"}
        monkeypatch.setattr(main, "generate_html_report", lambda *a, **kw: "fake.html")

        called = {"n": 0}

        def _counting(*args, **kwargs):
            called["n"] += 1
            return [], 0

        monkeypatch.setattr(main, "get_daily_stats", _counting)

        main.NewsAnalyzer._run_analysis_pipeline(
            self._fake_self(), {}, "current", {}, {}, [], [], {},
        )

        # current 模式即使 filter.method=ai 也不应该调用 get_daily_stats——
        # 应该直接走 count_word_frequency，不受 AI 配置影响
        assert called["n"] == 0
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_main.py -v -k TestRunAnalysisPipelineAIRouting`
Expected: FAIL（`test_daily_mode_routes_through_get_daily_stats` 失败，因为现在 `_run_analysis_pipeline` 还是直接调 `count_word_frequency`，`called["n"]` 会是 0 不是 1）

- [ ] **Step 3: 实现**

在 `main.py` 的 `_run_analysis_pipeline` 方法里，把：

```python
        # 统计计算
        stats, total_titles = count_word_frequency(
            data_source,
            word_groups,
            filter_words,
            id_to_name,
            title_info,
            self.rank_threshold,
            new_titles,
            mode=mode,
            global_filters=global_filters,
        )
```

改成：

```python
        # 统计计算：当日汇总模式按 filter.method 走 AI 分类或关键词匹配；
        # 当前榜单/增量监控模式固定走关键词匹配（AIFilterPipeline 目前不支持这两种模式）
        if mode == "daily":
            stats, total_titles = get_daily_stats(
                data_source,
                word_groups,
                filter_words,
                id_to_name,
                title_info,
                self.rank_threshold,
                new_titles,
                global_filters=global_filters,
            )
        else:
            stats, total_titles = count_word_frequency(
                data_source,
                word_groups,
                filter_words,
                id_to_name,
                title_info,
                self.rank_threshold,
                new_titles,
                mode=mode,
                global_filters=global_filters,
            )
```

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_main.py -v -k TestRunAnalysisPipelineAIRouting`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: route daily-mode report generation through get_daily_stats"
```

---

### Task 5: 接入 `web_server/news_service.py`

**Files:**
- Modify: `web_server/news_service.py`
- Modify: `tests/test_news_service.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_news_service.py` 里，先给 `_write_config` 加一个可选参数（修改现有函数签名，不是新增函数）：

把：
```python
def _write_config(tmp_path: Path, cards_per_batch: int = 12) -> None:
    config_data = {
```

改成：

```python
def _write_config(tmp_path: Path, cards_per_batch: int = 12, filter_method: str = "keyword") -> None:
    config_data = {
        "filter": {"method": filter_method},
```

（`config_data` 字典字面量里加一个 `"filter"` 键，跟已有的 `"app"`/`"crawler"` 等键同级，放在字典最前面即可，不影响其它键。）

然后在 `TestGetTodayNewsCards` 类里追加：

```python
    def test_ai_mode_tags_news_with_category(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path, filter_method="ai")

        date_folder = format_date_folder()
        txt_dir = tmp_path / "output" / date_folder / "txt"
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / "12时00分.txt").write_text(
            "zhihu | 知乎\n1. 科技新闻标题 [URL:http://a.com/1]\n",
            encoding="utf-8",
        )

        from trendradar.ai.filter_pipeline import AIFilterResult

        class _FakePipeline:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, all_titles):
                titles = [dict(t, category="科技") for t in all_titles]
                return AIFilterResult(
                    stats=[{
                        "word": "科技", "count": len(titles), "position": 1,
                        "percentage": 100.0, "titles": titles,
                    }],
                    total_matched=len(titles),
                    total_processed=len(titles),
                    success=True,
                )

        monkeypatch.setattr(main, "AIFilterPipeline", _FakePipeline)

        news_list, _cards_per_batch, _total_batches = get_today_news_cards()

        assert len(news_list) == 1
        assert news_list[0]["title"] == "科技新闻标题"
        assert news_list[0]["category"] == "科技"

    def test_ai_mode_falls_back_to_keyword_on_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path, filter_method="ai")
        # 关注词留空，等价于"全部新闻都算匹配"（跟现有关键词模式默认行为一致）

        date_folder = format_date_folder()
        txt_dir = tmp_path / "output" / date_folder / "txt"
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / "12时00分.txt").write_text(
            "zhihu | 知乎\n1. 标题一 [URL:http://a.com/1]\n",
            encoding="utf-8",
        )

        from trendradar.ai.filter_pipeline import AIFilterResult

        class _FailingPipeline:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, all_titles):
                return AIFilterResult(success=False, error="没有配置 API Key")

        monkeypatch.setattr(main, "AIFilterPipeline", _FailingPipeline)

        news_list, _cards_per_batch, _total_batches = get_today_news_cards()

        # 降级成功：走了关键词模式，正常返回新闻（没有 category 字段）
        assert len(news_list) == 1
        assert news_list[0]["title"] == "标题一"
        assert "category" not in news_list[0]
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_news_service.py -v -k ai_mode`
Expected: FAIL（`get_today_news_cards` 还在调用 `main.count_word_frequency`，不会调用 mock 的 `AIFilterPipeline`，所以返回的新闻不会带 `category`）

- [ ] **Step 3: 实现**

打开 `web_server/news_service.py`，把：

```python
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
```

改成：

```python
        stats, _total_titles = main.get_daily_stats(
            all_results,
            word_groups,
            filter_words,
            id_to_name,
            title_info,
            fresh_config["RANK_THRESHOLD"],
            new_titles,
            global_filters=global_filters,
        )
```

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_news_service.py -v`
Expected: 全部 passed（原有 3 个 + 新增 2 个）

- [ ] **Step 5: Commit**

```bash
git add web_server/news_service.py tests/test_news_service.py
git commit -m "feat: route Web UI homepage news through get_daily_stats"
```

---

### Task 6: 全量验证

**Files:** 无新增/修改，只跑检查

- [ ] **Step 1: 跑完整测试套件**

Run: `env -u PYTHONPATH uv run pytest tests/ -q`
Expected: 全部 passed（`tests/test_records.py::test_normalize_time` 如果因为跑的时间点在 18:00 之后而失败，是已知的、跟本次改动无关的预存在问题），覆盖率不低于之前的基线

- [ ] **Step 2: mypy 检查改动的文件**

Run: `env -u PYTHONPATH uv run mypy main.py web_server/news_service.py trendradar/ai/filter_pipeline.py --ignore-missing-imports`
Expected: 无新增错误

- [ ] **Step 3: 手动验证关键词模式完全不受影响**

确认 `config/config.yaml` 里 `filter.method` 还是默认值 `"keyword"`（sub-project 1 设的默认值，本计划没有改过它）：

Run: `env -u PYTHONPATH uv run python -c "from trendradar.config import load_config; print(load_config()['FILTER']['METHOD'])"`
Expected: 输出 `keyword`

- [ ] **Step 4: 手动验证 AI 模式端到端调用链路（mock AIFilterPipeline，不花真实 API 费用）**

Run:
```bash
env -u PYTHONPATH uv run python3 -c "
from unittest.mock import MagicMock
import main
from trendradar.ai.filter_pipeline import AIFilterResult

main.CONFIG['FILTER'] = {'METHOD': 'ai'}
main.CONFIG['AI'] = {}
main.CONFIG['AI_FILTER'] = {}

class FakePipeline:
    def __init__(self, *a, **kw): pass
    def run(self, all_titles):
        titles = [dict(t, category='科技') for t in all_titles]
        return AIFilterResult(stats=[{'word': '科技', 'count': len(titles), 'position': 1, 'percentage': 100.0, 'titles': titles}], total_matched=len(titles), total_processed=len(titles), success=True)

main.AIFilterPipeline = FakePipeline

all_results = {'zhihu': {'测试新闻标题': {'url': '', 'mobileUrl': ''}}}
title_info = {'zhihu': {'测试新闻标题': {}}}
stats, total = main.get_daily_stats(all_results, [], [], {'zhihu': '知乎'}, title_info, 5, {})
print('stats:', stats)
print('total:', total)
"
```
Expected: 输出的 `stats` 里能看到 `category: 科技`，`total` 为 1

- [ ] **Step 5: 确认没有遗留未提交的改动**

Run: `git status --short`
Expected: 干净

---

## 完成后

全部 6 个 Task 完成后，`filter.method: "ai"` 在 `main.py` 当日汇总路径和 Web UI 首页两个入口都真正生效，失败自动降级，`current`/`incremental` 模式不受影响。子项目 3（Web UI 分类 Tab）可以开始设计——这时候 `news_list`/`stats` 里已经会带着 `category` 字段，前端只需要基于这个字段做筛选展示。

按照 subagent-driven-development 流程，全部 Task 完成后使用 superpowers:finishing-a-development-branch 收尾。
