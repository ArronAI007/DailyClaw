# 设计文档：报告改为统一排序卡片流

日期：2026-09-18

## 背景与目标

现有 GitHub Pages / Web UI 热点报告（由 `main.py: generate_html_report` /
`render_html_content` 生成，写入 `output/<日期>/html/*.html` 并复制到根目录
`index.html`）把新闻按两个维度分组展示：

1. 主体区块按**关键词分组**（`frequency_words.txt` 里配置的兴趣词，如 AI、比亚迪）分段，
   每段内部按权重排序，条目内附带来源平台名。
2. "本次新增热点"区块按**平台**（知乎/微博/百度...）分组，重复展示当次新增的新闻。

用户认为"平台"只是数据入口，不应该作为报告的组织维度；报告应该把匹配到的新闻**整体按权重排序**，
以**卡片**形式展示，并提供"换一批"来分批浏览，而不是把所有匹配新闻一次性全部摊开。

## 现状澄清（实现前发现的关键事实）

- `templates/report.html`、`templates/stats.html`、`templates/new_titles.html` 是**死代码**：
  项目中没有任何地方通过 Jinja2 加载它们（`web_server/server.py` 的 `Jinja2Templates` 只指向
  `web_server/templates/`）。真正生成报告 HTML 的是 `main.py` 里用 f-string 拼接的
  `render_html_content`，逻辑与这三个模板高度重复但独立维护。本次改动只需要动
  `render_html_content`，同时删除这三个死文件。
- `prepare_report_data`（`trendradar/notifier/__init__.py`）同时被 HTML 报告生成
  （`main.py:generate_html_report`）和推送通知（`send_to_notifications` → 各
  `trendradar/notifier/*.py`）复用，输出的 `stats`/`new_titles` 结构是推送消息格式化
  （飞书/钉钉/企业微信/...）依赖的契约。**本次改动不修改 `count_word_frequency` 与
  `prepare_report_data` 的行为和返回结构**，只在 HTML 报告渲染阶段，对
  `prepare_report_data` 返回的 `report_data["stats"]` 做一次摊平+重排，用于展示；
  推送通知路径完全不受影响。
- "本次新增热点"区块在 daily/current 模式下展示的新闻，本来就已经包含在主区块的
  `stats[].titles` 里（每条都带 `is_new` 标记），该区块只是按平台重新分组、再展示一次
  同一批数据，属于冗余展示。摊平后用卡片上的 NEW 角标替代，不再需要单独区块。
  incremental 模式下本来就不显示该区块（`hide_new_section = True`），行为不变。

## 架构改动

1. 新建 `trendradar/html_report.py`，把 `main.py` 中 `generate_html_report` /
   `render_html_content` 相关的 HTML/CSS/JS 拼接逻辑整体迁移过去（连同其依赖的少量工具函数调用，
   如 `utils.html_escape`、`utils.get_beijing_time`、`utils.get_output_path`、
   `utils.ensure_directory_exists`）。`main.py` 只保留调用 `generate_html_report(...)` 的地方，
   改为 `from trendradar.html_report import generate_html_report`。
   - 理由：这段渲染逻辑本来就有 700+ 行，混在 main.py 里违反项目自身的文件组织约定
     （单文件 200-400 行为宜，最多 800 行；按功能/领域拆分而非堆在一起）；既然本次要重写这段逻辑，
     顺手拆分成独立模块，属于"正在改的代码本身该拆"，不是无关重构。
2. 删除 `templates/report.html`、`templates/stats.html`、`templates/new_titles.html`
   （确认无引用的死代码）。
3. `calculate_news_weight`（现在定义在 main.py，供 `count_word_frequency` 组内排序使用）
   迁移到 `trendradar/utils.py`，行为不变，只是换个文件。`main.py: count_word_frequency`
   和新的 `trendradar/html_report.py` 都从 `trendradar.utils` 导入它，避免
   `trendradar/html_report.py` 反向 import `main.py` 造成循环依赖。

## 数据流

```
main.py: _run_analysis_pipeline
  ├─ count_word_frequency(...)         # 不变：产出按关键词分组的 stats
  ├─ generate_html_report(stats, ...)  # 改为调用 trendradar/html_report.py
  │    ├─ prepare_report_data(...)     # 不变：产出 report_data["stats"] / ["new_titles"]
  │    └─ render_html_content(report_data, ...)   # 改动核心
  │         ├─ 摊平：合并 report_data["stats"] 里所有分组的 titles 为一个列表
  │         │        （不再使用 report_data["new_titles"]，避免重复展示）
  │         ├─ 排序：按 calculate_news_weight 全局重排
  │         ├─ 截断：应用 CONFIG["MAX_NEWS_PER_KEYWORD"]（>0 时限制总条数，语义从
  │         │        "每关键词组上限"变为"报告总条数上限"）
  │         └─ 渲染：卡片网格 + 分批 JS，写入 HTML 文件
  └─ send_to_notifications(stats, ...) # 不变：推送消息路径完全不受影响
```

## 卡片内容与布局

每张卡片沿用现有单条新闻展示的信息，只改变视觉容器（从细线分隔的列表行改为独立卡片盒子）：

- 序号（当前批次内的序号，从 1 开始，每批重新计数）
- 来源平台标签（`source_name`，样式沿用现有 `.source-name`）
- 排名徽章（复用现有 `.rank-num` / `.top` / `.high` 配色规则）
- 时间跨度（`time_display`，格式化规则不变）
- 出现次数（`count > 1` 时显示，样式不变）
- NEW 角标（`is_new` 为真时显示，复用现有 `.news-item.new::after` 效果）
- 标题（可点击跳转 `mobile_url` 优先于 `url`，规则不变）

布局：响应式网格，桌面 2 列、手机（≤480px）收窄为 1 列；容器 `max-width` 从固定
600px 放宽（例如 900px 左右，具体数值在实现时结合视觉效果微调），网格间距参考现有
`news-item` 的 `gap`/`padding` 节奏保持一致的呼吸感。

## "换一批"分批机制

- 静态页面没有后端，所有匹配到的新闻在**生成时**一次性摊平、排序、写入页面（作为已渲染好的
  卡片 DOM 节点，每个节点带 `data-batch` 序号，或者等价的 JS 数组索引方案，实现阶段二选一）。
- 每批展示数量：新增配置 `report.cards_per_batch`，默认 `12`；通过
  `trendradar/config.py` 暴露为 `CONFIG["CARDS_PER_BATCH"]`（支持 `CARDS_PER_BATCH`
  环境变量覆盖，与现有 `MAX_WORKERS` 等配置项的模式一致）；同时补进
  `web_server/config_manager.py` 的读取/保存路径和 `config.html` 的表单，模式与最近新增的
  `max_workers` 字段一致，避免 Web UI 保存配置时把这个新字段冲掉。
- "换一批"按钮为纯前端 JS：维护一个当前批次索引，点击后切换到下一批（隐藏当前批卡片、显示下一批），
  按钮文案可附带"第 X / 共 Y 批"提示。翻到最后一批后再次点击循环回第一批（不做禁用态）。
- 总条数不足一批（≤ `cards_per_batch`）时，不显示"换一批"按钮（只有一批，切换没有意义）。

## 一并移除的功能

- "分段保存为图片"按钮及其 JS（`saveAsMultipleImages` 及相关的按 `.word-group`/`.news-item`
  几何位置切割图片的逻辑）：与网格布局天然冲突（按行切割会把卡片切碎），且随着单批展示量
  收窄到 12 条，单张截图（"保存为图片"，`saveAsImage`，对 `.container` 整体截图，
  不依赖内部是否分组/分批）基本能覆盖需求，用户已确认接受移除。
- "本次新增热点"独立区块（原因见"现状澄清"）。
- 关键词分组的可视化呈现（分组标题、分组内计数徽章 `.word-count.hot/.warm`、
  分组顺序 `SORT_BY_POSITION_FIRST` 对 HTML 报告的影响）：关键词分组仍然是**筛选**逻辑
  （决定哪些新闻匹配、需要展示），但不再是 HTML 报告的**展示**维度。
  `SORT_BY_POSITION_FIRST` 配置以后只影响 `stats` 列表本身的顺序（进而影响推送消息里
  各关键词组的先后顺序），不再影响 HTML 报告展示顺序（因为 HTML 报告不再按分组展示）。

## 不受影响的部分

- `count_word_frequency`、`prepare_report_data`、`calculate_news_weight` 的行为/签名。
- 推送通知（飞书/钉钉/企业微信/Telegram/邮件/ntfy/Bark/Slack）的消息格式与内容，包括
  `reverse_content_order`、`SORT_BY_POSITION_FIRST` 对推送消息的影响。
- "失败平台"提示区块（⚠️ 请求失败的平台）。
- Web UI 的 `report_view.html`（仍然是 iframe 嵌入同一份生成好的 HTML，天然复用新版报告）。
- 报告文件的输出路径与文件名规则（`output/<日期>/html/*.html`、根目录 `index.html`、
  `output/index.html` 双路径生成）。

## 配置变更小结

| 配置项 | 位置 | 变化 |
|---|---|---|
| `report.cards_per_batch` | `config/config.yaml` | 新增，默认 12 |
| `CARDS_PER_BATCH` | 环境变量 | 新增，覆盖上面的配置 |
| `MAX_NEWS_PER_KEYWORD` | 既有 | 语义从"每关键词组上限"变为"报告总条数上限" |
| `REVERSE_CONTENT_ORDER` | 既有 | 不再影响 HTML 报告展示顺序，仅影响推送消息 |

## 测试与验证

- 现有单元测试中，与 `count_word_frequency`、`prepare_report_data`、通知格式相关的测试不应受影响。
- 需要为新的摊平排序 + 分批渲染逻辑补充测试（覆盖：多分组摊平后的排序结果、
  `MAX_NEWS_PER_KEYWORD` 截断、`cards_per_batch` 配置读取、不足一批时不生成"换一批"按钮的分支）。
- 手动验证：本地跑一次 `main.py`（或用已有测试数据）生成 `output/<日期>/html/*.html`，
  用浏览器打开检查卡片网格在桌面宽度和手机宽度（≤480px）下的呈现、"换一批"分批与循环、
  "保存为图片"仍可用。
