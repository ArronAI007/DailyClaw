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
