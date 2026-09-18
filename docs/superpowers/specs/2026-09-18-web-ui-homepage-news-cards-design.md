# 设计文档：Web UI 首页改为新闻卡片流

日期：2026-09-18

## 背景与目标

DailyClaw Web UI（`web_server`，`./start-web.sh` 启动，默认端口 18080）目前的首页
（`/`，`dashboard.html`）是一个纯运维仪表盘：系统状态、今日统计、平台状态、热门关注词、
最近阅读。用户要看新闻内容，得先看这个仪表盘，再点进"报告浏览"，再点开某一份具体报告，
才能看到（且 `/reports` 页面自己用另一套按平台分组的旧样式渲染新闻，跟这次报告改版的
"统一排序卡片流"方向不一致）。

目标：把首页改成"新闻查看平台"该有的样子——打开就是今天的新闻卡片流，排序/去重/关键词
过滤跟真实报告完全一致；顺带把 `/reports` 页面里跟这次改版风格冲突的部分（按平台分组的
内联预览）也去掉。

## 现状澄清（设计阶段确认的关键事实）

- `web_server` 目前是**完全独立于 `main.py`** 运行的：它自己有一套更简单的数据读取/解析
  逻辑（`mcp_server/services/data_service.py` 的 `get_latest_news`，`web_server/server.py`
  自己的 `parse_news_txt`），只按单条新闻的 `rank` 排序，不做关键词过滤、不去重合并出现
  次数、不算权重——跟 `main.py`/`trendradar/html_report.py` 那套"关键词过滤 → 摊平 → 按
  权重全局排序"的逻辑是两条不同的路径，产出的新闻顺序/内容会不一样。
- `web_server` 目前**从未 import 过 `main.py`**。`main.py` 顶层只做 `CONFIG = load_config()`
  和日志配置（无爬取/推送等副作用，`NewsAnalyzer().run()` 在 `if __name__ == "__main__":`
  保护之下），所以 import 它是安全的——这个 session 早些时候已经确认过、也修好了会让
  `load_config()` 崩溃的那个预存在 bug（`config/config.yaml` 缺 `feishu_message_separator`）。
- `web_server` 现有的配置读取（`web_server/config_manager.py: ConfigManager.load_config()`）
  返回的是**原始 yaml dict**（小写 key：`platforms`、`report`、`weight`...），每次调用都
  重新读文件，不缓存，所以 Web UI 改配置后其他页面能立刻看到最新值，不用重启。
  这跟 `main.py`/`trendradar.config.load_config()` 产出的**转换后大写 key 的 CONFIG dict**
  （`CONFIG["PLATFORMS"]`、`CONFIG["WEIGHT_CONFIG"]`、`CONFIG["RANK_THRESHOLD"]`、
  `CONFIG["CARDS_PER_BATCH"]`）是两种完全不同的表示，本来就并存在这个代码库里，不是本次
  引入的新问题。
- `main.py` 的 `count_word_frequency` 函数**不是纯函数**：除了显式参数外，它内部还直接读
  模块全局变量 `CONFIG`（用于 `SORT_BY_POSITION_FIRST`、`MAX_NEWS_PER_KEYWORD` 两个可选
  开关的回退值，以及通过 `calculate_news_weight(x, CONFIG["WEIGHT_CONFIG"], ...)` 读权重
  配置）。`main.py` 的 `CONFIG` 是模块级变量，只在第一次 `import main` 时赋值一次——如果
  `web_server`（常驻进程）只在启动时 import 一次，后续用户在"配置管理"页面改了权重/关键词
  相关配置，首页新闻计算不会跟着实时更新，除非重启 web_server。为了跟应用其它地方"改配置
  立即生效"的行为保持一致，`news_service.py` 每次重新计算（缓存未命中时）都会用
  `trendradar.config.load_config()` 读一份新鲜配置，并显式赋值给 `main.CONFIG`
  （`import main; main.CONFIG = fresh_config`），这样 `count_word_frequency` 内部的隐式
  读取也能拿到最新值，不需要重启进程。这是个明确、可解释的取舍，不是意外的全局变量污染。
- `main.py` 已有的模块级纯函数可以直接复用，不需要新写数据读取逻辑：
  - `read_all_today_titles(current_platform_ids) -> (all_results, id_to_name, title_info)`
  - `detect_latest_new_titles(current_platform_ids) -> new_titles`
  - `count_word_frequency(results, word_groups, filter_words, id_to_name, title_info, rank_threshold, new_titles, mode, global_filters) -> (stats, total_titles)`
  这三个函数组合起来，正是 `main.py` 自己的 `NewsAnalyzer._load_analysis_data` /
  `_run_analysis_pipeline` 内部用来准备报告数据的同一条链路（`main.py` 里这部分本次不改）。
- `trendradar.notifier.prepare_report_data(config, stats, ...)` 和
  `trendradar.html_report._flatten_and_sort_news(stats, weight_config, rank_threshold)`
  这两个函数是这次报告改版刚做的，本身就是纯函数、无副作用，直接复用。
- 新闻标题点击后记录"阅读历史"的能力已经存在且是**事件委托**实现的
  （`web_server/static/js/dashboard.js` 监听 `.report-news-link` 的点击，`sendBeacon` 到
  `/api/read-history`），新卡片只要用同样的 `class="report-news-link"` +
  `data-title`/`data-platform` 属性，不需要写新 JS 就能复用这个能力。
- `web_server/static/css/dashboard.css`（1223 行）和 `web_server/static/js/dashboard.js`
  是整个 web 应用共享的唯一一份 CSS/JS（`base.html` 里统一引入，不是按页面拆分的多文件），
  这是现有约定；新样式/脚本继续加进这两个文件里，不新开 `news-cards.css`/`news-cards.js`。

## 架构

```
web_server/server.py: dashboard() 路由 ("/")
  └─ web_server/news_service.py: get_today_news_cards(cards_per_batch=None)
       ├─ import main                          # 触发 main.CONFIG 存在（首次 import 时）
       ├─ fresh_config = trendradar.config.load_config()
       ├─ main.CONFIG = fresh_config            # 让 count_word_frequency 内部隐式读取同步更新
       ├─ current_platform_ids = [p["id"] for p in fresh_config["PLATFORMS"]]
       ├─ all_results, id_to_name, title_info = main.read_all_today_titles(current_platform_ids)
       ├─ new_titles = main.detect_latest_new_titles(current_platform_ids)
       ├─ word_groups, filter_words, global_filters = trendradar.utils.load_frequency_words()
       ├─ stats, total_titles = main.count_word_frequency(
       │        all_results, word_groups, filter_words, id_to_name, title_info,
       │        fresh_config["RANK_THRESHOLD"], new_titles, mode="daily", global_filters=global_filters)
       ├─ report_data = trendradar.notifier.prepare_report_data(fresh_config, stats, mode="daily")
       ├─ news_list = trendradar.html_report._flatten_and_sort_news(
       │        report_data["stats"], fresh_config["WEIGHT_CONFIG"], fresh_config["RANK_THRESHOLD"])
       └─ 缓存结果（沿用 mcp_server/services/cache_service.CacheService，TTL 300 秒）
  └─ 渲染 dashboard.html：news_list + cards_per_batch 传给模板，Jinja 循环渲染成
     dashboard.css 自己的卡片样式（不复用 trendradar/html_report.py 里给独立 HTML 文件用的
     内联 CSS/HTML 字符串——那段是给静态文件生成用的，跟 web 应用的设计系统是分开的两层
     表现层，共享的只是"排序好的数据"这一层）
```

`get_today_news_cards` 放在新文件 `web_server/news_service.py`（新增，职责单一：组装给
Web UI 用的"今日新闻卡片列表"），不是塞进 `main.py`（`main.py` 定位是 CLI 入口，不应该
反过来关心 Web UI 的展示需求）也不是塞进已经很大的 `web_server/server.py`。

## 数据流细节

- `mode="daily"`：跟"当日汇总"报告用同一个模式，语义上就是"给我今天完整的新闻全貌"，
  跟首页"打开就看到今天都有什么"的定位吻合。不用 `current`/`incremental`。
- 关键词过滤沿用现状：`frequency_words.txt` 为空时，`load_frequency_words()` 返回空
  `word_groups`，`count_word_frequency` 会用"全部新闻"虚拟分组，等价于不过滤——首页
  和报告页行为保持一致。
- `output/<今天>/txt/` 目录不存在或为空时（比如今天还没抓取过），
  `read_all_today_titles` 返回空字典，`news_list` 是空列表——首页展示一个空状态
  （复用现有 `empty-state` 样式），提示"今天还没有数据，点击右上角手动爬取"。
- 缓存 key 固定为 `"homepage_news_cards"`（不需要按参数区分，首页只有一种视图），
  TTL 300 秒，用现有 `get_data_service().cache`（`CacheService` 实例，`web_server` 已经
  在用它给 `get_latest_news` 缓存），不新增缓存基础设施。

## 页面改造

### 首页 `web_server/templates/dashboard.html` + `web_server/server.py: dashboard()`

- 移除：`total_news_today` 独立大卡片、`platforms` 数量提示卡片、`trending_topics`
  区块（连同 `server.py` 里的 `get_trending_topics_from_latest_report` 调用一起去掉，
  这个函数本身保留在 `server.py` 里不删除代码——它是独立可复用的工具函数，只是不再从
  dashboard 路由调用；如果以后要在别处用到"热门关注词"这个统计，不用重写）、
  `read_history` 列表区块（连同 `load_read_history`/`format_relative_time` 调用一起从
  dashboard 路由去掉，函数本身也保留，`add_read_history`/`/api/read-history` 写入能力
  完全不受影响）。
- 保留并精简：原来"系统状态"卡片里的健康状态、数据存储信息，跟"今日新闻总数"
  （现在从 `len(news_list)` 算，不用原来 `total_news_today` 那条路径）合并成顶部一条
  横向状态条，加上"手动爬取"按钮（沿用现有 `triggerCrawl()` JS，不改）。
  "平台状态"卡片保留（能一眼看出哪些平台在监控、哪些暂时没数据），放在状态条下方或
  折叠展示，具体视觉细节留给实现阶段判断，不在这份文档里精确定死。
- 新增：新闻卡片网格，字段跟报告页一致（序号、来源平台、排名徽章、时间跨度、出现次数、
  NEW 角标、标题可点击），响应式 2 列（桌面）/1 列（手机，≤480px，跟报告页用同一个断点），
  "换一批"客户端 JS 分批（复用 `trendradar/html_report.py` 里"一次性渲染全部 +
  `data-batch` 属性 + JS 切换 `display:none`"这个已经验证过的模式，用 Jinja 在
  `dashboard.html` 里重新实现一遍循环渲染逻辑，不是导入 `trendradar.html_report` 的
  HTML 字符串拼接函数）。`cards_per_batch` 从 `fresh_config["CARDS_PER_BATCH"]` 传入
  模板，默认 12，和报告页共用同一个配置项，不新增配置。
- 标题链接沿用 `class="report-news-link" data-title="..." data-platform="..."`，阅读
  记录能力不用改代码就能工作。

### 报告列表 `web_server/templates/reports.html`

- 移除每份报告 `<details>` 展开后的 `report-platform-grid`（按平台分组的内联预览）：
  `reports.html` 不再渲染 `r["groups"]`，`<details>`/`<summary>` 展开交互整个去掉，
  每份报告变成一行不可展开的列表项。`server.py: reports_page()` 里继续调用
  `parse_news_txt(r["txt_path"])` 来算 `r["total_items"]`（"X 条新闻"这个数字还留着，
  在列表项里展示，对判断一份报告大小有用），只是不再把 `groups` 传给模板、模板也不再
  渲染平台分组内容——省掉的是"按平台展开预览"这个 UI 和它对应的模板逻辑，不是这次
  统计条数用的解析调用本身。
- 保留：按日期分组的报告列表本身（日期、时间、条数、"查看完整报告 ↗"链接）。点进
  "查看完整报告"看到的就是 `trendradar/html_report.py` 生成的、已经是新设计的报告文件
  （通过 `/reports/view` 的 iframe 嵌入，这部分完全不用改）。
- 效果：`/reports` 从"列表 + 每份都内联展开预览新闻"简化成"纯列表 + 点进去看完整报告"，
  不再有第二套平台分组的新闻展示逻辑。

## 一并处理

- `web_server/server.py` 里 `reports_page()` 目前对每份历史报告调用 `parse_news_txt`
  只是为了给 `<details>` 内联预览用，改造后这部分调用直接删除。
- `main.CONFIG` 会在每次首页请求（缓存未命中时）被 `news_service.py` 重新赋值成最新配置——
  这只影响 `count_word_frequency` 内部隐式读取的那两个开关和权重配置，不影响 `main.py`
  自己独立运行时的行为（`main.py` 每次都是全新进程，本来就会重新 `load_config()`）。

## 不受影响的部分

- `main.py` 的 `count_word_frequency`、`read_all_today_titles`、`detect_latest_new_titles`
  本身的实现/签名不变。
- `trendradar/html_report.py`、`trendradar/notifier/`、推送通知路径完全不动。
- `/reports/view`、`/reports/latest`、`/config`、`/api/crawl`、`/api/status`、
  `/api/news/latest`、`/api/read-history` 这些路由的现有行为不变。
- `web_server/config_manager.py` 的原始 yaml 配置读写路径不变（"配置管理"页面继续用
  `ConfigManager`，不受这次改动影响）。

## 测试与验证

- `web_server/news_service.py: get_today_news_cards` 需要单元测试覆盖：
  - 今天没有任何抓取数据时返回空列表
  - 关键词过滤生效（`frequency_words.txt` 配了词组时，不匹配的新闻不出现）
  - 返回的新闻按权重降序排列（复用已经验证过的 `_flatten_and_sort_news` 排序逻辑，
    这里主要测"数据组装链路接对了"，不重复测排序算法本身）
  - 缓存命中时不重新读文件/重新计算（可以用 mock 验证 `read_all_today_titles`
    只被调用一次）
- `web_server/server.py: reports_page()` 改造后需要确认：报告列表页不再依赖
  `parse_news_txt`，现有关于 `/reports` 的测试（如果有）不因为移除 `groups` 字段而挂掉
  （需要先检查 `tests/` 下是否已有针对这个路由的测试，据此决定是修改还是新增）。
- 手动验证：本地跑一次 `./start-web.sh`，用浏览器打开 `http://localhost:18080/`，检查
  首页直接展示新闻卡片、"换一批"能用、点击标题会记录阅读历史（刷新后 `/api/read-history`
  数据不受影响，虽然首页不再展示这个列表）；打开 `/reports`，确认列表页不再有按平台分组
  的展开内容，点"查看完整报告"能正常在 iframe 里看到卡片流报告。
