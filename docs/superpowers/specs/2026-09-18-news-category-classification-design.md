# 新闻分类浏览 - 设计文档

## 背景与目标

用户希望在新闻卡片流（Web UI 首页 + 静态报告）里把新闻按类型（科技、娱乐、军事、财经等）分类展示。

现状调研发现一个关键事实：`config/frequency_words.txt` 目前只配置了一组关键词（DeepSeek/字节/英伟达/AI/芯片等），而且它的语义是**过滤器**——不匹配任何关键词的新闻会被直接丢弃，根本不出现在卡片流里。这就是为什么首页现在看到的新闻几乎全是 AI/科技相关内容。

用户确认目标是**扩大新闻覆盖范围，同时按类别组织展示**，而不是仅仅给现有的一小撮新闻贴标签。用户同时明确拒绝了纯关键词匹配的分类方案（"关键词太死板"），要求分类必须真正理解标题语义。

DailyClaw 是 [TrendRadar](https://github.com/sansan0/TrendRadar) 的 fork，上游已经实现了一套成熟的"LLM 批量分类"方案（`trendradar/ai/` + `trendradar/storage/`），基于 LiteLLM 统一接入 100+ 家模型。用户明确要求**完全照搬上游的设计思路**（包括 SQLite 存储层和动态标签提取机制），而不是自己另起炉灶做简化版。

## 范围与依据

- 参考实现：`github.com/sansan0/TrendRadar`，`trendradar/ai/`、`trendradar/storage/`、`config/ai_filter/`、`config/ai_interests.txt`、`config.yaml` 的 `ai:`/`ai_filter:`/`filter:` 三段。
- RSS 相关代码（上游有 RSS 抓取支持）整体跳过——DailyClaw 没有 RSS 模块，用户也没有要求。
- 上游的 `ai_analysis`（AI 生成文字分析报告，不同于分类）不在本次范围内。

## 整体拆分（3 个子项目）

按依赖顺序拆成 3 个独立可交付的子项目，各自走完整的 spec → plan → implementation 流程：

1. **AI 分类引擎移植**（本文档详细设计的范围）：`trendradar/ai/` + `trendradar/storage/` + 相关配置和 prompt 文件。独立可测试，不接入爬取主流程和 Web UI。
2. **接入现有爬取/报告管线**：`main.py` 根据新增的 `filter.method` 配置（`keyword` | `ai`）二选一，产出 `stats` 结构（两条路径产出的结构字段一致，下游代码基本不用改）；`trendradar/notifier/prepare_report_data` 的 title 字典新增 `category` 字段透传。
3. **Web UI 分类浏览**：首页 + 报告页加分类 Tab（Tab 集合由 `ai_filter_tags` 表里的 active 标签决定），复用现有"换一批"的前端分批思路做筛选；`filter.method=keyword` 时不显示分类 Tab（因为没有分类数据）。

本文档只详细设计子项目 1。子项目 2、3 的详细设计在各自启动时另写 spec。

---

## 子项目 1 详细设计：AI 分类引擎

### 目录结构

```
trendradar/ai/
├── __init__.py
├── client.py             # AIClient：基于 litellm 的统一模型接口
├── prompt_loader.py      # 解析 [system]/[user] 格式的 prompt 文件
├── filter.py              # AIFilter：标签提取(阶段A) + 标签更新(阶段A') + 批量分类(阶段B)
└── filter_pipeline.py     # AIFilterPipeline：编排完整流程，产出 stats 兼容结构

trendradar/storage/
├── __init__.py
└── ai_filter_store.py     # SQLite 存储：标签版本管理 + 已分类新闻去重 + 分类结果
```

新增依赖：`litellm`（加入 `pyproject.toml`）。SQLite 走标准库 `sqlite3`，无需额外依赖。

### 配置文件

```
config/ai_interests.txt                  # 兴趣描述（自然语言段落）
config/ai_filter/prompt.txt              # 分类 prompt
config/ai_filter/extract_prompt.txt      # 标签提取 prompt（首次运行/全量重分类时用）
config/ai_filter/update_tags_prompt.txt  # 标签增量更新 prompt（兴趣描述变更时用）
```

`config/ai_interests.txt` 默认种子内容（用户可随时编辑，下次运行自动生效）：

```
下面是我要关注的内容：
# 重要性排序说明：从上到下优先级递减，越靠前越重要。

1. 科技：关注人工智能、大模型、芯片、半导体、互联网大厂、消费电子等科技产业动态。
2. 财经：关注股市、汇率、利率、宏观经济政策、企业财报、并购等金融与商业新闻。
3. 娱乐：关注影视、音乐、游戏、明星动态、文化 IP 等娱乐产业内容。
4. 军事：关注国防、武器装备、军事冲突、地缘安全等军事相关新闻。
```

`config.yaml` 新增（沿用上游字段名，走 `trendradar/config.py::load_config()` 现有的"小写 yaml → 大写 CONFIG key"映射约定，与 `CONFIG["WEIGHT_CONFIG"]`、`CONFIG["MAX_WORKERS"]` 等现有字段的接入方式一致）：

```yaml
filter:
  method: "keyword"          # keyword（现状） | ai（本功能启用后可选）

ai:
  model: "deepseek/deepseek-v4-flash"
  api_key: ""                # 建议用环境变量 AI_API_KEY，不要写进文件
  timeout: 120
  temperature: 1.0
  max_tokens: 5000
  num_retries: 1
  fallback_models: []

ai_filter:
  batch_size: 200
  batch_interval: 2
  min_score: 0.7
  reclassify_threshold: 0.6
  prompt_file: "prompt.txt"
  extract_prompt_file: "extract_prompt.txt"
  update_tags_prompt_file: "update_tags_prompt.txt"
```

`filter.method` 本身只在子项目 2 里被消费；子项目 1 阶段只需要把配置读进 `CONFIG["AI"]` / `CONFIG["AI_FILTER"]`。

### 与上游的关键差异：存储层适配

上游的 SQLite 表（`ai_filter_results`、`ai_filter_analyzed_news`）用整数 `news_item_id` 关联它自己的 `news_items` 全量新闻库（上游整个抓取管线都存在 SQLite 里）。DailyClaw 没有这套存储——新闻身份从头到尾都是"标题文本"本身（`main.py` 里所有去重、"是否新增"判断都按标题做）。

因此本次移植把这两张表的主键从 `news_item_id INTEGER` 改成 `title_hash TEXT`（标题的 md5，用 `AIFilter.compute_interests_hash` 已有的同款 hash 方式），语义完全一致（"这条新闻分析过没有/结果是什么"），只是外键类型换成跟 DailyClaw 现有身份模型匹配的。同时去掉 `source_type` 字段和所有 RSS 相关列（DailyClaw 没有 RSS，只有 hotlist 一种来源）。

`ai_filter_tags` 表（标签版本管理：标签名/描述/优先级/active-deprecated 状态/版本号/prompt_hash）原样保留，这张表本身不依赖新闻身份，跟上游一致。

SQLite 文件路径：`data/ai_filter.sqlite3`（新建 `data/` 目录，加入 `.gitignore`，跟 `output/` 一样是本地生成数据，不提交到仓库）。

调整后的表结构：

```sql
CREATE TABLE IF NOT EXISTS ai_filter_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT NOT NULL,
    description TEXT DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 9999,
    status TEXT DEFAULT 'active',
    deprecated_at TEXT,
    version INTEGER NOT NULL,
    prompt_hash TEXT NOT NULL,
    interests_file TEXT NOT NULL DEFAULT 'ai_interests.txt',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_filter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_hash TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    relevance_score REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    deprecated_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(title_hash, tag_id)
);

CREATE TABLE IF NOT EXISTS ai_filter_analyzed_news (
    title_hash TEXT NOT NULL,
    interests_file TEXT NOT NULL DEFAULT 'ai_interests.txt',
    prompt_hash TEXT NOT NULL,
    matched INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (title_hash, interests_file)
);

CREATE INDEX IF NOT EXISTS idx_ai_filter_tags_status ON ai_filter_tags(status);
CREATE INDEX IF NOT EXISTS idx_ai_filter_tags_priority ON ai_filter_tags(interests_file, status, priority);
CREATE INDEX IF NOT EXISTS idx_ai_filter_results_tag ON ai_filter_results(tag_id);
CREATE INDEX IF NOT EXISTS idx_analyzed_news_lookup ON ai_filter_analyzed_news(interests_file, prompt_hash);
```

### 组件设计

**`AIClient`**（`trendradar/ai/client.py`）：直接移植上游实现。封装 `litellm.completion()`，从 `CONFIG["AI"]` 读取 model/api_key/timeout/temperature/max_tokens/num_retries/fallback_models，`chat(messages) -> str`。`api_key` 优先读配置，回退读环境变量 `AI_API_KEY`。

**`prompt_loader.load_prompt_template()`**：直接移植上游实现。解析 `[system]`/`[user]` 分段的 prompt 文件，返回 `(system_prompt, user_prompt_template)`。

**`AIFilter`**（`trendradar/ai/filter.py`）：直接移植上游三个方法：
- `extract_tags(interests_content) -> List[{"tag", "description"}]`：阶段 A，首次运行/全量重分类时把兴趣描述转成结构化标签。
- `update_tags(old_tags, interests_content) -> {"keep", "add", "remove", "change_ratio"} | None`：阶段 A'，兴趣描述变更时判断增量更新方案。
- `classify_batch(titles, tags, interests_content) -> List[{"title", "tag", "tag_id", "relevance_score", ...}] | None`：阶段 B，批量分类，失败返回 `None`（调用方负责重试逻辑）。
- `compute_interests_hash(content, filename) -> str`：兴趣描述内容的 md5，格式 `filename:md5`，忽略空行和注释行。

**`AIFilterPipeline`**（`trendradar/ai/filter_pipeline.py`）：移植上游 `run()` 编排逻辑，去掉所有 RSS 相关分支（`_collect_pending_news`/`_classify_batches`/`convert_to_report_data` 都只保留 hotlist 路径）：

1. 读取 `config/ai_interests.txt`，计算 hash。
2. 跟存储里的 `prompt_hash` 比对：不存在→首次提取标签；变化→调用 `update_tags` 算 `change_ratio`，超过 `reclassify_threshold` 全量重提取，否则增量应用 keep/add/remove。
3. 从当天新闻标题（调用方传入，子项目 2 阶段对接 `main.py::read_all_today_titles` 的结果）里筛出"还没被分析过"的（按 `title_hash` 查 `ai_filter_analyzed_news`）。
4. 按 `batch_size` 分批调用 `AIFilter.classify_batch`，批次间按 `batch_interval` 秒等待。
5. 结果存入 `ai_filter_results`，标题记入 `ai_filter_analyzed_news`（无论匹配与否，避免重复分析）。
6. 查询当前 active 分类结果，按标签分组、按 `min_score` 过滤，转换成 `stats` 结构返回。

`convert_to_report_data()` 产出格式对齐 `main.py::count_word_frequency()` 现有的 `stats` 结构（`trendradar/utils.py`/`trendradar/notifier/__init__.py` 消费的那个），额外在每条新闻的 title 字典里加一个 `category` 字段（值等于命中的标签名）：

```python
[{"word": "科技", "count": 12, "position": 1, "titles": [
    {"title": ..., "source_name": ..., "url": ..., "mobile_url": ...,
     "ranks": [...], "rank_threshold": ..., "count": 1, "is_new": False,
     "time_display": ..., "category": "科技"},
    ...
]}, ...]
```

这个结构和字段对齐是为子项目 2 铺路：子项目 2 只需要把 `main.py` 里生成 `stats` 的那一步换成调用 `AIFilterPipeline`，下游 `flatten_and_sort_news`/报告渲染/Web UI 数据管线基本不用改。

**`AIFilterStore`**（`trendradar/storage/ai_filter_store.py`）：SQLite 存储封装，对外方法参照上游 `storage_manager` 里跟 AI 筛选相关的那部分接口（`get_latest_prompt_hash`、`get_active_ai_filter_tags`、`save_ai_filter_tags`、`deprecate_all_ai_filter_tags`/`deprecate_specific_ai_filter_tags`、`update_ai_filter_tag_priorities`/`update_ai_filter_tag_descriptions`、`get_analyzed_title_hashes`、`save_analyzed_titles`、`save_ai_filter_results`、`get_active_ai_filter_results`、`clear_unmatched_analyzed_news`），内部用 `title_hash` 替代上游的 `news_item_id`/`source_type` 组合键。首次调用时自动建表（`CREATE TABLE IF NOT EXISTS`），不需要独立的迁移脚本。

### 错误处理

- LLM 调用失败（超时/限流/网络错误/JSON 解析失败）：`classify_batch` 返回 `None`，该批次的标题不写入 `ai_filter_analyzed_news`，下次运行会重新尝试分类；不影响其他批次的结果保存。
- 兴趣描述文件缺失或为空：`AIFilterPipeline.run()` 返回 `AIFilterResult(success=False, error=...)`，不抛异常。降级策略（比如自动回退到关键词模式）留给子项目 2 决定，子项目 1 只保证失败原因清楚可读。
- 标签提取/更新阶段 JSON 解析失败：记录日志，`extract_tags`/`update_tags` 返回空列表/`None`，`filter_pipeline` 据此走"提取失败"分支，不写入任何标签变更。

### 测试计划

- `tests/test_ai_client.py`：mock `litellm.completion`，验证参数组装（model/api_key/timeout 等透传）、异常传播。
- `tests/test_ai_filter.py`：mock `AIClient.chat`，覆盖 `extract_tags`/`update_tags`/`classify_batch` 的正常解析、JSON 格式异常、空响应等分支。
- `tests/test_ai_filter_store.py`：用临时 SQLite 文件，覆盖标签版本管理（首次保存/废弃/增量更新）、分类结果的存取、已分析记录的去重查询。
- `tests/test_ai_filter_pipeline.py`：mock `AIFilter` 和 `AIFilterStore`，验证首次运行/增量更新/全量重分类三条分支的编排逻辑，以及 `convert_to_report_data` 的输出结构（字段齐全、`category` 字段正确）。

LLM 调用全程 mock，测试不会真实调用 DeepSeek API、不产生费用。
