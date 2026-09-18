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
