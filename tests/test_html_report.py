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
