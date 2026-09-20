# coding=utf-8

import threading
from typing import Any, Dict, List, Tuple

import main
from mcp_server.services.cache_service import get_cache
from trendradar import config as trendradar_config
from trendradar.html_report import flatten_and_sort_news
from trendradar.notifier import prepare_report_data
from trendradar.utils import load_frequency_words


CACHE_KEY = "homepage_news_cards"
CACHE_TTL = 300
_lock = threading.Lock()


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

    Raises:
        FileNotFoundError: config/config.yaml 或 config/frequency_words.txt 缺失时，
            分别由 trendradar.config.load_config() / load_frequency_words() 抛出。
            这里不捕获——调用方（web_server 的 dashboard 路由）负责捕获并降级。
    """
    cache = get_cache()
    cached = cache.get(CACHE_KEY, ttl=CACHE_TTL)
    if cached is not None:
        return cached

    # 缓存未命中：加锁重新计算，避免并发请求交错执行——既互相踩坏 main.CONFIG，
    # 又让某个请求的 count_word_frequency 读到另一个请求刚设置的 CONFIG。
    with _lock:
        fresh_config = trendradar_config.load_config()
        # count_word_frequency 内部直接读模块级全局变量 main.CONFIG（用于
        # SORT_BY_POSITION_FIRST / MAX_NEWS_PER_KEYWORD 兜底值，以及传给
        # calculate_news_weight 的 weight_config），而不是接收参数。main.CONFIG
        # 只在 main.py 首次 import 时赋值一次，但 web_server 是长驻进程，所以这里
        # 每次缓存未命中都显式重新赋值，让"配置管理"页面改的配置无需重启即可生效。
        main.CONFIG = fresh_config

        current_platform_ids = [p["id"] for p in fresh_config["PLATFORMS"]]

        all_results, id_to_name, title_info = main.read_all_today_titles(
            current_platform_ids
        )

        cards_per_batch = fresh_config.get("CARDS_PER_BATCH", 12)

        if not all_results:
            result: Tuple[List[Dict[str, Any]], int, int] = ([], cards_per_batch, 0)
            cache.set(CACHE_KEY, result)
            return result

        new_titles = main.detect_latest_new_titles(current_platform_ids)
        word_groups, filter_words, global_filters = load_frequency_words()

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

        report_data = prepare_report_data(fresh_config, stats, mode="daily")

        news_list = flatten_and_sort_news(
            report_data["stats"], fresh_config["WEIGHT_CONFIG"], fresh_config["RANK_THRESHOLD"]
        )

        total_batches = _compute_total_batches(len(news_list), cards_per_batch)

        result = (news_list, cards_per_batch, total_batches)
        cache.set(CACHE_KEY, result)
        return result
