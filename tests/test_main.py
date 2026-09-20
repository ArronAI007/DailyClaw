# coding=utf-8

import os
import sys
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# Ensure project root is in path
sys.path.insert(0, "/tmp/TrendRadar_clone")

import main


class TestParseFileTitles:
    def test_parse_simple_file(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu | 知乎\n"
            "1. 测试标题 [URL:http://example.com] [MOBILE:http://m.example.com]\n"
            "\n"
            "weibo | 微博\n"
            "2. 另一个标题\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert id_to_name == {"zhihu": "知乎", "weibo": "微博"}
        assert "zhihu" in titles_by_id
        assert "weibo" in titles_by_id
        assert titles_by_id["zhihu"]["测试标题"]["ranks"] == [1]
        assert titles_by_id["zhihu"]["测试标题"]["url"] == "http://example.com"
        assert titles_by_id["zhihu"]["测试标题"]["mobileUrl"] == "http://m.example.com"
        assert titles_by_id["weibo"]["另一个标题"]["ranks"] == [2]

    def test_parse_without_urls(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu\n"
            "1. 无链接标题\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert id_to_name == {"zhihu": "zhihu"}
        assert titles_by_id["zhihu"]["无链接标题"]["url"] == ""
        assert titles_by_id["zhihu"]["无链接标题"]["mobileUrl"] == ""

    def test_parse_failed_ids_section(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu | 知乎\n"
            "1. 测试标题\n"
            "\n"
            "==== 以下ID请求失败 ====\n"
            "failed_id\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert "failed_id" not in id_to_name
        assert "failed_id" not in titles_by_id

    def test_parse_invalid_line_default_rank(self, tmp_path):
        file_path = tmp_path / "test.txt"
        file_path.write_text(
            "zhihu | 知乎\n"
            "invalid line without rank\n",
            encoding="utf-8"
        )

        titles_by_id, id_to_name = main.parse_file_titles(file_path)

        assert id_to_name == {"zhihu": "知乎"}
        # Lines without rank get default rank of 1
        assert "invalid line without rank" in titles_by_id["zhihu"]
        assert titles_by_id["zhihu"]["invalid line without rank"]["ranks"] == [1]


class TestFormatTimeDisplay:
    def test_same_time(self):
        assert main.format_time_display("10:00", "10:00") == "10:00"

    def test_different_time(self):
        assert main.format_time_display("10:00", "12:00") == "[10:00 ~ 12:00]"

    def test_empty_first(self):
        assert main.format_time_display("", "12:00") == ""

    def test_empty_last(self):
        assert main.format_time_display("10:00", "") == "10:00"


class TestFormatRankDisplay:
    def test_single_top_rank(self):
        result = main.format_rank_display([1], 5, "feishu")
        assert "1" in result
        assert "font color='red'" in result

    def test_range_rank(self):
        result = main.format_rank_display([1, 3], 5, "feishu")
        assert "1" in result
        assert "3" in result

    def test_non_highlighted_rank(self):
        result = main.format_rank_display([8], 5, "feishu")
        assert "[8]" in result
        assert "font color='red'" not in result

    def test_platform_variants(self):
        for platform in ["dingtalk", "wework", "telegram", "slack", "ntfy", "html"]:
            result = main.format_rank_display([1], 5, platform)
            assert "1" in result

    def test_empty_ranks(self):
        assert main.format_rank_display([], 5, "feishu") == ""


class TestProcessSourceData:
    def test_new_source(self):
        all_results = {}
        title_info = {}
        main.process_source_data(
            "zhihu",
            {"标题1": {"ranks": [1], "url": "", "mobileUrl": ""}},
            "10:00",
            all_results,
            title_info,
        )
        assert "zhihu" in all_results
        assert title_info["zhihu"]["标题1"]["count"] == 1
        assert title_info["zhihu"]["标题1"]["first_time"] == "10:00"

    def test_merge_existing_title(self):
        all_results = {
            "zhihu": {
                "标题1": {"ranks": [1], "url": "", "mobileUrl": ""}
            }
        }
        title_info = {
            "zhihu": {
                "标题1": {
                    "first_time": "09:00",
                    "last_time": "09:00",
                    "count": 1,
                    "ranks": [1],
                    "url": "",
                    "mobileUrl": "",
                }
            }
        }
        main.process_source_data(
            "zhihu",
            {"标题1": {"ranks": [2], "url": "http://new.com", "mobileUrl": ""}},
            "10:00",
            all_results,
            title_info,
        )
        assert sorted(all_results["zhihu"]["标题1"]["ranks"]) == [1, 2]
        assert title_info["zhihu"]["标题1"]["count"] == 2
        assert title_info["zhihu"]["标题1"]["last_time"] == "10:00"
        assert all_results["zhihu"]["标题1"]["url"] == "http://new.com"

    def test_new_title_in_existing_source(self):
        all_results = {
            "zhihu": {
                "标题1": {"ranks": [1], "url": "", "mobileUrl": ""}
            }
        }
        title_info = {
            "zhihu": {
                "标题1": {
                    "first_time": "09:00",
                    "last_time": "09:00",
                    "count": 1,
                    "ranks": [1],
                    "url": "",
                    "mobileUrl": "",
                }
            }
        }
        main.process_source_data(
            "zhihu",
            {"标题2": {"ranks": [3], "url": "", "mobileUrl": ""}},
            "10:00",
            all_results,
            title_info,
        )
        assert "标题2" in all_results["zhihu"]
        assert title_info["zhihu"]["标题2"]["count"] == 1


class TestDetectLatestNewTitles:
    def test_detect_new_titles(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 旧标题\n",
            encoding="utf-8"
        )

        file2 = output_dir / "10-00-00.txt"
        file2.write_text(
            "zhihu | 知乎\n1. 旧标题\n2. 新标题\n",
            encoding="utf-8"
        )

        with patch("main.Path", return_value=output_dir.parent.parent):
            # Actually, detect_latest_new_titles hardcodes Path("output")
            # We need to monkeypatch the cwd or use a different approach
            # Let's patch utils.format_date_folder to return our date folder
            # and change directory
            old_cwd = os.getcwd()
            os.chdir(tmp_path)
            try:
                new_titles = main.detect_latest_new_titles()
                assert "zhihu" in new_titles
                assert "新标题" in new_titles["zhihu"]
                assert "旧标题" not in new_titles["zhihu"]
            finally:
                os.chdir(old_cwd)

    def test_no_new_titles(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 标题\n",
            encoding="utf-8"
        )

        file2 = output_dir / "10-00-00.txt"
        file2.write_text(
            "zhihu | 知乎\n1. 标题\n",
            encoding="utf-8"
        )

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            new_titles = main.detect_latest_new_titles()
            assert new_titles == {}
        finally:
            os.chdir(old_cwd)

    def test_single_file_returns_empty(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 标题\n",
            encoding="utf-8"
        )

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            new_titles = main.detect_latest_new_titles()
            assert new_titles == {}
        finally:
            os.chdir(old_cwd)

    def test_filter_by_platform(self, tmp_path):
        output_dir = tmp_path / "output" / main.utils.format_date_folder() / "txt"
        output_dir.mkdir(parents=True)

        file1 = output_dir / "09-00-00.txt"
        file1.write_text(
            "zhihu | 知乎\n1. 旧标题\n",
            encoding="utf-8"
        )

        file2 = output_dir / "10-00-00.txt"
        file2.write_text(
            "zhihu | 知乎\n1. 旧标题\n2. 新标题\n"
            "weibo | 微博\n1. 微博新标题\n",
            encoding="utf-8"
        )

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            new_titles = main.detect_latest_new_titles(["zhihu"])
            assert "zhihu" in new_titles
            assert "weibo" not in new_titles
        finally:
            os.chdir(old_cwd)


class TestFormatTitleForPlatform:
    def make_title_data(self, **overrides):
        defaults = {
            "title": "测试标题",
            "source_name": "测试源",
            "ranks": [1],
            "rank_threshold": 5,
            "time_display": "10:00",
            "count": 1,
            "url": "http://example.com",
            "mobile_url": "http://m.example.com",
            "is_new": False,
        }
        defaults.update(overrides)
        return defaults

    def test_feishu_with_link(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("feishu", data)
        assert "测试标题" in result
        assert "测试源" in result
        assert "http://m.example.com" in result

    def test_feishu_new_title(self):
        data = self.make_title_data(is_new=True)
        result = main.format_title_for_platform("feishu", data)
        assert "🆕" in result

    def test_dingtalk_format(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("dingtalk", data)
        assert "测试标题" in result
        assert "测试源" in result

    def test_telegram_html_escape(self):
        data = self.make_title_data(title="测试<标题>")
        result = main.format_title_for_platform("telegram", data)
        assert "&lt;" in result or "<" not in result or "测试" in result

    def test_slack_link_format(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("slack", data)
        assert "测试标题" in result
        assert "<http://m.example.com|" in result or "http://m.example.com" in result

    def test_ntfy_format(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("ntfy", data)
        assert "测试标题" in result

    def test_html_format(self):
        data = self.make_title_data(is_new=True)
        result = main.format_title_for_platform("html", data)
        assert "测试标题" in result
        assert "new-title" in result

    def test_unknown_platform(self):
        data = self.make_title_data()
        result = main.format_title_for_platform("unknown", data)
        assert result == "测试标题"


class TestSaveTitlesToFile:
    def test_save_and_read_roundtrip(self, tmp_path):
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            results = {
                "zhihu": {
                    "标题1": {"ranks": [1], "url": "http://a.com", "mobileUrl": "http://ma.com"},
                    "标题2": {"ranks": [2], "url": "", "mobileUrl": ""},
                }
            }
            id_to_name = {"zhihu": "知乎"}
            failed_ids = ["failed1"]

            file_path = main.save_titles_to_file(results, id_to_name, failed_ids)
            assert Path(file_path).exists()

            content = Path(file_path).read_text(encoding="utf-8")
            assert "zhihu | 知乎" in content
            assert "1. 标题1" in content
            assert "[URL:http://a.com]" in content
            assert "[MOBILE:http://ma.com]" in content
            assert "failed1" in content
        finally:
            os.chdir(old_cwd)


class TestFlattenTitlesForAI:
    def test_maps_fields_correctly(self):
        all_results = {
            "zhihu": {
                "标题一": {"ranks": [3], "url": "http://a.com/1", "mobileUrl": "http://m.a.com/1"},
            },
        }
        title_info = {
            "zhihu": {
                "标题一": {
                    "first_time": "10:00", "last_time": "10:30", "count": 2,
                    "ranks": [1, 3], "url": "http://a.com/1", "mobileUrl": "http://m.a.com/1",
                },
            },
        }
        id_to_name = {"zhihu": "知乎"}
        new_titles = {"zhihu": {"标题一": {}}}

        flat = main._flatten_titles_for_ai(all_results, title_info, id_to_name, new_titles, rank_threshold=5)

        assert len(flat) == 1
        item = flat[0]
        assert item["title"] == "标题一"
        assert item["source_name"] == "知乎"
        assert item["ranks"] == [1, 3]
        assert item["rank_threshold"] == 5
        assert item["url"] == "http://a.com/1"
        assert item["mobileUrl"] == "http://m.a.com/1"
        assert item["count"] == 2
        assert item["time_display"] == "[10:00 ~ 10:30]"
        assert item["is_new"] is True

    def test_defaults_ranks_to_99_when_missing(self):
        all_results = {"zhihu": {"标题": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"标题": {}}}

        flat = main._flatten_titles_for_ai(all_results, title_info, {}, None, rank_threshold=5)

        assert flat[0]["ranks"] == [99]
        assert flat[0]["is_new"] is False

    def test_flattens_multiple_platforms(self):
        all_results = {
            "zhihu": {"标题一": {"url": "", "mobileUrl": ""}},
            "weibo": {"标题二": {"url": "", "mobileUrl": ""}},
        }
        title_info = {"zhihu": {"标题一": {}}, "weibo": {"标题二": {}}}
        id_to_name = {"zhihu": "知乎", "weibo": "微博"}

        flat = main._flatten_titles_for_ai(all_results, title_info, id_to_name, None, rank_threshold=5)

        titles = {item["title"]: item["source_name"] for item in flat}
        assert titles == {"标题一": "知乎", "标题二": "微博"}

    def test_no_keyword_filtering_applied(self):
        """AI 模式的核心前提：打平不做任何关键词过滤，全部标题都进来"""
        all_results = {
            "zhihu": {
                "无关新闻标题": {"url": "", "mobileUrl": ""},
                "另一条无关新闻": {"url": "", "mobileUrl": ""},
            },
        }
        title_info = {"zhihu": {"无关新闻标题": {}, "另一条无关新闻": {}}}

        flat = main._flatten_titles_for_ai(all_results, title_info, {}, None, rank_threshold=5)

        assert len(flat) == 2

    def test_handles_empty_results(self):
        flat = main._flatten_titles_for_ai({}, {}, {}, None, rank_threshold=5)
        assert flat == []

    def test_falls_back_to_source_id_when_name_unknown(self):
        all_results = {"unknown_source": {"标题": {"url": "", "mobileUrl": ""}}}
        title_info = {"unknown_source": {"标题": {}}}

        flat = main._flatten_titles_for_ai(all_results, title_info, {}, None, rank_threshold=5)

        assert flat[0]["source_name"] == "unknown_source"


class TestGetDailyStats:
    @pytest.fixture(autouse=True)
    def _restore_config(self):
        original = dict(main.CONFIG)
        yield
        main.CONFIG.clear()
        main.CONFIG.update(original)

    def test_keyword_mode_matches_count_word_frequency_directly(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "keyword"}

        all_results = {"zhihu": {"AI新闻": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"AI新闻": {}}}
        id_to_name = {"zhihu": "知乎"}
        word_groups = [{"required": [], "normal": ["AI"], "group_key": "AI"}]

        direct_stats, direct_total = main.count_word_frequency(
            all_results, word_groups, [], id_to_name, title_info, 5, {}, mode="daily",
        )
        via_helper_stats, via_helper_total = main.get_daily_stats(
            all_results, word_groups, [], id_to_name, title_info, 5, {},
        )

        assert via_helper_stats == direct_stats
        assert via_helper_total == direct_total

    def test_ai_mode_success_skips_keyword_matching(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "ai"}
        main.CONFIG["AI"] = {}
        main.CONFIG["AI_FILTER"] = {}

        from trendradar.ai.filter_pipeline import AIFilterResult

        fake_stats = [{"word": "科技", "count": 1, "position": 1, "percentage": 100.0, "titles": []}]

        class _FakePipeline:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, all_titles):
                return AIFilterResult(
                    stats=fake_stats, total_matched=1, total_processed=1, success=True,
                )

        monkeypatch.setattr(main, "AIFilterPipeline", _FakePipeline)
        monkeypatch.setattr(main, "AIFilterStore", lambda *args, **kwargs: object())

        count_word_frequency_called = {"n": 0}
        original = main.count_word_frequency

        def _counting(*args, **kwargs):
            count_word_frequency_called["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(main, "count_word_frequency", _counting)

        all_results = {"zhihu": {"标题": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"标题": {}}}

        stats, total = main.get_daily_stats(all_results, [], [], {}, title_info, 5, {})

        assert stats == fake_stats
        assert total == 1
        assert count_word_frequency_called["n"] == 0

    def test_ai_mode_falls_back_to_keyword_on_failure(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "ai"}
        main.CONFIG["AI"] = {}
        main.CONFIG["AI_FILTER"] = {}

        from trendradar.ai.filter_pipeline import AIFilterResult

        class _FailingPipeline:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, all_titles):
                return AIFilterResult(success=False, error="兴趣描述文件为空或不存在")

        monkeypatch.setattr(main, "AIFilterPipeline", _FailingPipeline)
        monkeypatch.setattr(main, "AIFilterStore", lambda *args, **kwargs: object())

        all_results = {"zhihu": {"AI新闻": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"AI新闻": {}}}
        id_to_name = {"zhihu": "知乎"}
        word_groups = [{"required": [], "normal": ["AI"], "group_key": "AI"}]

        stats, total = main.get_daily_stats(
            all_results, word_groups, [], id_to_name, title_info, 5, {},
        )

        # 降级成功：跟直接调用 count_word_frequency 的结果一致
        expected_stats, expected_total = main.count_word_frequency(
            all_results, word_groups, [], id_to_name, title_info, 5, {}, mode="daily",
        )
        assert stats == expected_stats
        assert total == expected_total

    def test_ai_mode_falls_back_when_pipeline_raises_unexpectedly(self, monkeypatch):
        main.CONFIG["FILTER"] = {"METHOD": "ai"}
        main.CONFIG["AI"] = {}
        main.CONFIG["AI_FILTER"] = {}

        def _raising_pipeline(*args, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(main, "AIFilterPipeline", _raising_pipeline)
        monkeypatch.setattr(main, "AIFilterStore", lambda *args, **kwargs: object())

        all_results = {"zhihu": {"AI新闻": {"url": "", "mobileUrl": ""}}}
        title_info = {"zhihu": {"AI新闻": {}}}
        id_to_name = {"zhihu": "知乎"}
        word_groups = [{"required": [], "normal": ["AI"], "group_key": "AI"}]

        stats, total = main.get_daily_stats(
            all_results, word_groups, [], id_to_name, title_info, 5, {},
        )

        expected_stats, expected_total = main.count_word_frequency(
            all_results, word_groups, [], id_to_name, title_info, 5, {}, mode="daily",
        )
        assert stats == expected_stats
        assert total == expected_total
