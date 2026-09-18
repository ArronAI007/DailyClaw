# coding=utf-8

from pathlib import Path
from typing import Any, Dict, List

import pytest

from trendradar import utils
from trendradar.html_report import (
    flatten_and_sort_news,
    _render_news_cards_html,
    render_html_content,
    generate_html_report,
)


def _weight_config() -> Dict[str, float]:
    return {"RANK_WEIGHT": 0.6, "FREQUENCY_WEIGHT": 0.3, "HOTNESS_WEIGHT": 0.1}


def _title(
    title: str,
    ranks: List[int],
    count: int = 1,
    is_new: bool = False,
    url: str = "",
    mobile_url: str = "",
    time_display: str = "",
    rank_threshold: int = 5,
) -> Dict[str, Any]:
    return {
        "title": title,
        "source_name": "测试源",
        "time_display": time_display,
        "count": count,
        "ranks": ranks,
        "rank_threshold": rank_threshold,
        "url": url,
        "mobile_url": mobile_url,
        "is_new": is_new,
    }


class TestFlattenAndSortNews:
    def test_merges_all_groups_into_one_list(self):
        stats = [
            {"word": "关键词A", "count": 1, "titles": [_title("标题1", [1])]},
            {"word": "关键词B", "count": 1, "titles": [_title("标题2", [2])]},
        ]
        result = flatten_and_sort_news(stats, _weight_config(), rank_threshold=5)
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
        result = flatten_and_sort_news(stats, _weight_config(), rank_threshold=5)
        assert [item["title"] for item in result] == ["高排名", "低排名"]

    def test_empty_stats_returns_empty_list(self):
        result = flatten_and_sort_news([], _weight_config(), rank_threshold=5)
        assert result == []

    def test_group_with_no_titles_is_skipped(self):
        stats = [
            {"word": "空组", "count": 0, "titles": []},
            {"word": "有内容", "count": 1, "titles": [_title("标题1", [1])]},
        ]
        result = flatten_and_sort_news(stats, _weight_config(), rank_threshold=5)
        assert len(result) == 1
        assert result[0]["title"] == "标题1"


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

    def test_escapes_url_in_href(self):
        payload_url = '"><script>alert(1)</script>'
        news = [_title("标题1", [1], url=payload_url)]
        cards_html, _ = _render_news_cards_html(news, cards_per_batch=12)
        assert '"><script>' not in cards_html
        assert utils.html_escape(payload_url) in cards_html
        assert f'<a href="{utils.html_escape(payload_url)}"' in cards_html

    def test_count_badge_shown_when_count_greater_than_one(self):
        news = [_title("标题1", [1], count=3)]
        cards_html, _ = _render_news_cards_html(news, cards_per_batch=12)
        assert "3次" in cards_html

    def test_time_display_rendered_simplified(self):
        news = [_title("标题1", [1], time_display="[10:00 ~ 12:00]")]
        cards_html, _ = _render_news_cards_html(news, cards_per_batch=12)
        assert "10:00~12:00" in cards_html
        assert "[" not in cards_html
        assert "]" not in cards_html

    def test_rank_tier_high_between_top_and_threshold(self):
        news = [_title("标题1", [7], rank_threshold=10)]
        cards_html, _ = _render_news_cards_html(news, cards_per_batch=12)
        assert 'class="rank-num high"' in cards_html

    def test_rank_tier_none_when_above_threshold(self):
        news = [_title("标题1", [15], rank_threshold=10)]
        cards_html, _ = _render_news_cards_html(news, cards_per_batch=12)
        assert "rank-num top" not in cards_html
        assert "rank-num high" not in cards_html


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
