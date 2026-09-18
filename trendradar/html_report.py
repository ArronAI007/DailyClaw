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
