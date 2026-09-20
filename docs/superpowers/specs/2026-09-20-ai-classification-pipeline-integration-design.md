# 接入现有爬取/报告管线 - 设计文档

## 背景

子项目 1（AI 分类引擎：`trendradar/ai/`、`trendradar/storage/`）已经完成并推送（见 [2026-09-18-news-category-classification-design.md](./2026-09-18-news-category-classification-design.md)）。它是完全独立的新代码，目前没有任何调用方。本子项目（子项目 2）把它接入现有的爬取/报告主流程，让 `config.yaml` 里的 `filter.method: "ai"` 真正生效。

## 范围

- 接入 `main.py` 的"当日汇总"（`mode="daily"`）报告生成路径。
- 接入 `web_server/news_service.py::get_today_news_cards()`（Web UI 首页，本来就只用 `mode="daily"`）。
- **不**接入"当前榜单"（`mode="current"`）和"增量监控"（`mode="incremental"`）——这两种模式继续固定走现有关键词逻辑，不受 `filter.method` 影响。原因：`AIFilterPipeline` 目前只有"把当天全部新闻分类一遍"这一种能力，没有"只看当前榜单"或"只看增量新增"的语义，现在给这两种模式设计 AI 版本工作量大且不在本次范围内。
- **不**接入 Web UI 的分类 Tab（那是子项目 3 的范围，需要新的前端 UI）。本子项目只保证数据层能正确产出带 `category` 字段的新闻，UI 展示留给子项目 3。

## 关键发现（决定了具体实现方式）

1. **字段命名**：`main.py` 全程（`process_source_data`、`count_word_frequency`）用的是 `mobileUrl`（驼峰），不是 `mobile_url`。`trendradar/notifier/prepare_report_data` 只在**输出**给展示层时才转成 `mobile_url`（下划线）——这是既有的、正确的约定（原始数据驼峰，展示层下划线），不是 bug。`AIFilterPipeline` 本身对字段名无感知（纯透传），所以子项目 2 的"打平"胶水函数只要用 `mobileUrl`，就能跟现有管线无缝对接，不需要改 `AIFilterPipeline` 或 `prepare_report_data` 的任何代码。

2. **stats 结构兼容性**：`prepare_report_data` 读取 `stat.get("percentage", 0)`（有默认值，不会报错），但 `AIFilterPipeline._build_result()` 目前没有计算这个字段——这意味着 AI 分类出来的类目会一直显示 "0%"。需要在 `AIFilterPipeline` 里补上。

3. **`web_server/news_service.py` 已经只用 `mode="daily"`**，调用序列（`read_all_today_titles` → `detect_latest_new_titles` → `load_frequency_words` → `count_word_frequency` → `prepare_report_data`）跟 `main.py` 当日汇总路径几乎一致，可以共用同一个新增入口函数。

4. **`main.py` 里 `_run_analysis_pipeline` 是当日汇总/当前榜单/增量监控三种模式共用的统一入口**（4 个调用点都经过它），`mode` 作为参数传入。只需要在这一个函数里按 `mode == "daily"` 分支即可覆盖所有相关调用点，不需要逐个改调用点。

## 设计

### 新增：`main.py::get_daily_stats()`

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
    filter.method == "keyword"（默认）时直接走关键词匹配，行为与改动前完全一致。

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

设计要点：
- 返回值形状跟 `count_word_frequency` 完全一致，两个调用方只需要把调用目标从 `count_word_frequency` 换成 `get_daily_stats`，函数签名兼容（多了 `word_groups`/`filter_words`/`global_filters` 这些关键词模式专用参数，AI 分支里不使用它们，只在降级 fallback 时才用到——保持签名一致是为了让降级分支能直接复用同一批入参，不用额外传递）。
- `AIFilterStore()` 每次调用新建一个实例（用默认 db 路径 `data/ai_filter.sqlite3`）。它的 `__init__` 只是确保表存在（`CREATE TABLE IF NOT EXISTS`，幂等），开销可忽略，不需要做成单例。

### 新增私有函数：`main.py::_flatten_titles_for_ai()`

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

字段构造逻辑直接对照 `count_word_frequency` 现有的逐条处理逻辑（`title_info` 优先，`title_data` 兜底），复用已有的 `format_time_display`，确保跟关键词路径产出的字段值语义一致，行为可对比。

### 接入点 1：`main.py::_run_analysis_pipeline`

现状（简化）：
```python
stats, total_titles = count_word_frequency(
    data_source, word_groups, filter_words, id_to_name, title_info,
    self.rank_threshold, new_titles, mode=mode, global_filters=global_filters,
)
```

改为：
```python
if mode == "daily":
    stats, total_titles = get_daily_stats(
        data_source, word_groups, filter_words, id_to_name, title_info,
        self.rank_threshold, new_titles, global_filters=global_filters,
    )
else:
    stats, total_titles = count_word_frequency(
        data_source, word_groups, filter_words, id_to_name, title_info,
        self.rank_threshold, new_titles, mode=mode, global_filters=global_filters,
    )
```

这一处改动覆盖全部 4 个调用点（当日汇总的两个调用点走新分支；current/incremental 的调用点走 else 分支，行为完全不变）。

### 接入点 2：`web_server/news_service.py::get_today_news_cards()`

现状：
```python
stats, _total_titles = main.count_word_frequency(
    all_results, word_groups, filter_words, id_to_name, title_info,
    fresh_config["RANK_THRESHOLD"], new_titles, mode="daily", global_filters=global_filters,
)
```

改为：
```python
stats, _total_titles = main.get_daily_stats(
    all_results, word_groups, filter_words, id_to_name, title_info,
    fresh_config["RANK_THRESHOLD"], new_titles, global_filters=global_filters,
)
```

（这里已经用的是 `mode="daily"`，所以直接换调用目标，不需要加分支。`main.CONFIG` 在这个函数里已经被显式重新赋值为 `fresh_config`，所以 `get_daily_stats` 内部读的 `CONFIG["FILTER"]["METHOD"]` 会是最新配置。）

### 子项目 1 的小补丁：`trendradar/ai/filter_pipeline.py`

`AIFilterPipeline._build_result()` 的分组构造里补上 `percentage` 字段：

```python
tag_groups[tag_name] = {
    "word": tag_name,
    "count": 0,
    "position": r.get("tag_priority", 9999),
    "titles": [],
}
```

改为在最终返回前计算（在 `stats = sorted(...)` 之后、构造 `AIFilterResult` 之前）：

```python
for group in stats:
    group["percentage"] = (
        round(group["count"] / total_processed * 100, 2) if total_processed > 0 else 0
    )
```

算法跟 `count_word_frequency` 里的 `round(data["count"] / total_titles * 100, 2)` 完全一致，`total_processed` 语义上等价于 `total_titles`（都是"参与统计的新闻总数"）。

## 错误处理

- `AIFilterPipeline.run()` 返回 `success=False`（兴趣描述缺失、标签提取失败、LLM 调用异常等）：`get_daily_stats` 记录一条 `logger.warning`（包含 `result.error` 里的具体原因），自动降级为 `count_word_frequency`，本次报告正常生成，只是没有 AI 分类结果。不抛异常、不中断爬取/推送流程。
- `AIFilterPipeline.run()` 内部已有的错误处理（LLM 调用失败的批次跳过重试、JSON 解析失败等）不变，本子项目不改动子项目 1 的错误处理逻辑（除了上面提到的 `percentage` 补丁）。
- `filter.method` 配置值既不是 `"keyword"` 也不是 `"ai"`（用户手滑打错）：视同 `"keyword"`（`if CONFIG["FILTER"]["METHOD"] == "ai"` 精确匹配，其他任何值都走 else 分支），不额外校验或报错，保持宽容。

## 测试计划

- `tests/test_main.py`（或新建 `tests/test_get_daily_stats.py`，视现有 `test_main.py` 组织方式而定）：
  - `test_get_daily_stats_keyword_mode_matches_count_word_frequency`：`CONFIG["FILTER"]["METHOD"]="keyword"` 时，`get_daily_stats` 的返回值跟直接调用 `count_word_frequency` 完全一致。
  - `test_get_daily_stats_ai_mode_success`：mock `AIFilterPipeline.run()` 返回 `success=True`，验证 `get_daily_stats` 返回 `(result.stats, result.total_processed)`，且**没有**调用 `count_word_frequency`。
  - `test_get_daily_stats_ai_mode_falls_back_on_failure`：mock `AIFilterPipeline.run()` 返回 `success=False`，验证降级调用了 `count_word_frequency` 并返回其结果。
  - `test_flatten_titles_for_ai_field_mapping`：构造一个小的 `all_results`/`title_info`/`id_to_name`/`new_titles` 样例，验证打平后每个字段（尤其 `mobileUrl`、`is_new`、`ranks` 兜底为 `[99]`）跟 `count_word_frequency` 对同样输入产出的字段值语义一致。
- `tests/test_ai_filter_pipeline.py`：新增 `test_percentage_field_computed_correctly`，验证 `_build_result` 产出的每个分组带正确的 `percentage`。
- `tests/test_news_service.py`：新增一个用例验证 `get_today_news_cards()` 在 `filter.method="ai"` 时调用链路正确（mock `main.get_daily_stats` 或更底层的 `AIFilterPipeline`，视现有测试的 mock 粒度而定）。
- 不新增任何真实 LLM 调用的测试——全部沿用子项目 1 建立的 mock 约定。

## 不在本次范围内（留给后续）

- Web UI 分类 Tab（子项目 3）。
- `current`/`incremental` 模式的 AI 支持。
- 手动爬取按钮（`/api/crawl`）触发的独立简化 HTML 生成路径（`mcp_server/tools/system.py::_generate_simple_html`）——这是第三条完全独立的报告生成实现，不复用 `count_word_frequency`/`prepare_report_data`，本次不接入。
