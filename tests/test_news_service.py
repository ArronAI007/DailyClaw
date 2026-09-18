# coding=utf-8

from pathlib import Path

import pytest
import yaml

import main
from mcp_server.services.cache_service import get_cache
from trendradar.utils import format_date_folder
from web_server.news_service import get_today_news_cards


def _write_config(tmp_path: Path, cards_per_batch: int = 12) -> None:
    config_data = {
        "app": {"version_check_url": "", "show_version_update": False},
        "crawler": {
            "request_interval": 1000,
            "max_workers": 5,
            "use_proxy": False,
            "default_proxy": "",
            "enable_crawler": True,
        },
        "report": {
            "mode": "daily",
            "rank_threshold": 5,
            "sort_by_position_first": False,
            "max_news_per_keyword": 0,
            "cards_per_batch": cards_per_batch,
            "reverse_content_order": False,
        },
        "notification": {
            "enable_notification": False,
            "message_batch_size": 4000,
            "batch_send_interval": 3,
            "max_accounts_per_channel": 3,
            "feishu_message_separator": "---",
            "webhooks": {},
        },
        "weight": {"rank_weight": 0.6, "frequency_weight": 0.3, "hotness_weight": 0.1},
        "platforms": [
            {"id": "zhihu", "name": "知乎"},
            {"id": "weibo", "name": "微博"},
        ],
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(config_data, allow_unicode=True), encoding="utf-8"
    )
    (config_dir / "frequency_words.txt").write_text("", encoding="utf-8")


@pytest.fixture(autouse=True)
def _restore_main_config():
    """get_today_news_cards 会修改 main.CONFIG 这个全局变量，测试完必须还原，
    避免污染同一个 pytest 进程里跑的其它测试。"""
    original = main.CONFIG
    yield
    main.CONFIG = original


@pytest.fixture(autouse=True)
def _clear_news_cache():
    """get_today_news_cards 用的是 mcp_server 里的进程级共享缓存单例（同一个
    CACHE_KEY，TTL 300 秒），同一个 pytest 进程里跑多个测试会互相读到对方缓存的
    结果，必须在每个测试前后清空，保证测试隔离。"""
    get_cache().clear()
    yield
    get_cache().clear()


class TestGetTodayNewsCards:
    def test_no_data_today_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path)

        news_list, cards_per_batch, total_batches = get_today_news_cards()

        assert news_list == []
        assert cards_per_batch == 12
        assert total_batches == 0

    def test_returns_weight_sorted_news_with_keyword_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path)

        date_folder = format_date_folder()
        txt_dir = tmp_path / "output" / date_folder / "txt"
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / "12时00分.txt").write_text(
            "zhihu | 知乎\n"
            "1. AI大模型最新进展 [URL:http://a.com/1]\n"
            "2. 无关新闻标题 [URL:http://a.com/2]\n"
            "\n"
            "weibo | 微博\n"
            "1. 今日热搜AI话题 [URL:http://b.com/1]\n",
            encoding="utf-8",
        )

        # 只关注含"AI"的新闻，验证关键词过滤生效
        (tmp_path / "config" / "frequency_words.txt").write_text("AI", encoding="utf-8")

        news_list, cards_per_batch, total_batches = get_today_news_cards()

        titles = {item["title"] for item in news_list}
        assert titles == {"AI大模型最新进展", "今日热搜AI话题"}
        assert "无关新闻标题" not in titles
        assert cards_per_batch == 12
        assert total_batches == 1

        # zhihu 排名 1（min_rank=1）应该排在 weibo 排名 1 前面还是后面取决于权重计算，
        # 这里只断言两条都在，且返回的是一个扁平列表（不是按分组嵌套的结构）
        assert all("word" not in item for item in news_list)
        assert all("ranks" in item for item in news_list)

    def test_cache_hit_skips_recomputation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        _write_config(tmp_path)

        date_folder = format_date_folder()
        txt_dir = tmp_path / "output" / date_folder / "txt"
        txt_dir.mkdir(parents=True, exist_ok=True)
        (txt_dir / "12时00分.txt").write_text(
            "zhihu | 知乎\n1. 标题一 [URL:http://a.com/1]\n", encoding="utf-8"
        )

        first_result = get_today_news_cards()

        call_count = {"n": 0}
        original_read = main.read_all_today_titles

        def _counting_read(*args, **kwargs):
            call_count["n"] += 1
            return original_read(*args, **kwargs)

        monkeypatch.setattr(main, "read_all_today_titles", _counting_read)

        second_result = get_today_news_cards()

        assert call_count["n"] == 0  # 命中缓存，完全没再调用 read_all_today_titles
        assert second_result == first_result
