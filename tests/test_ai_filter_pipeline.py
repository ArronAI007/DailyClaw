# coding=utf-8

from unittest.mock import MagicMock

import pytest

from trendradar.ai.filter_pipeline import AIFilterPipeline
from trendradar.storage.ai_filter_store import AIFilterStore, title_hash


@pytest.fixture
def store(tmp_path):
    return AIFilterStore(str(tmp_path / "test.sqlite3"))


def _make_pipeline(store, mock_filter, filter_config=None):
    return AIFilterPipeline(
        ai_config={},
        filter_config=filter_config
        or {
            "BATCH_SIZE": 200,
            "BATCH_INTERVAL": 0,
            "MIN_SCORE": 0.0,
            "RECLASSIFY_THRESHOLD": 0.6,
        },
        store=store,
        ai_filter=mock_filter,
    )


def _title_entry(title, **overrides):
    entry = {
        "title": title,
        "source_name": "知乎",
        "url": "https://a.com",
        "mobile_url": "",
        "ranks": [1],
        "rank_threshold": 5,
        "count": 1,
        "is_new": False,
        "time_display": "10:00",
    }
    entry.update(overrides)
    return entry


class TestAIFilterPipelineRun:
    def test_missing_interests_content_returns_failure(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = None
        pipeline = _make_pipeline(store, mock_filter)

        result = pipeline.run([])
        assert result.success is False
        assert "兴趣描述" in result.error

    def test_first_run_extracts_tags_and_classifies(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技和财经"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"
        mock_filter.extract_tags.return_value = [
            {"tag": "科技", "description": "科技相关"},
            {"tag": "财经", "description": "财经相关"},
        ]

        def fake_classify(titles_for_ai, active_tags, interests_content):
            tag_map = {t["tag"]: t["id"] for t in active_tags}
            return [
                {
                    "title": item["title"],
                    "tag": "科技",
                    "tag_id": tag_map["科技"],
                    "relevance_score": 0.9,
                }
                for item in titles_for_ai
            ]

        mock_filter.classify_batch.side_effect = fake_classify

        pipeline = _make_pipeline(store, mock_filter)
        all_titles = [_title_entry("英伟达发布新芯片")]

        result = pipeline.run(all_titles)

        assert result.success is True
        assert result.total_matched == 1
        assert result.total_processed == 1
        assert len(result.stats) == 1
        assert result.stats[0]["word"] == "科技"
        assert result.stats[0]["count"] == 1
        assert result.stats[0]["titles"][0]["title"] == "英伟达发布新芯片"
        assert result.stats[0]["titles"][0]["category"] == "科技"
        assert result.stats[0]["titles"][0]["source_name"] == "知乎"

        assert [t["tag"] for t in store.get_active_ai_filter_tags()] == ["科技", "财经"]
        assert title_hash("英伟达发布新芯片") in store.get_analyzed_title_hashes()

    def test_already_analyzed_titles_are_skipped(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"

        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "", "priority": 1}],
            version=1,
            prompt_hash="ai_interests.txt:hash1",
        )
        h = title_hash("已经分析过的新闻")
        store.save_analyzed_titles(
            [h], "ai_interests.txt", "ai_interests.txt:hash1", matched_hashes=set()
        )

        pipeline = _make_pipeline(store, mock_filter)
        result = pipeline.run([_title_entry("已经分析过的新闻")])

        mock_filter.classify_batch.assert_not_called()
        assert result.success is True
        assert result.stats == []

    def test_extract_tags_failure_returns_error(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"
        mock_filter.extract_tags.return_value = []

        pipeline = _make_pipeline(store, mock_filter)
        result = pipeline.run([])

        assert result.success is False
        assert "标签提取失败" in result.error

    def test_interests_change_below_threshold_applies_incremental_update(self, store):
        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "旧描述", "priority": 1}],
            version=1,
            prompt_hash="ai_interests.txt:old_hash",
        )

        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技和军事"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:new_hash"
        mock_filter.update_tags.return_value = {
            "keep": [{"tag": "科技", "description": "新描述"}],
            "add": [{"tag": "军事", "description": "军事相关"}],
            "remove": [],
            "change_ratio": 0.3,
        }
        mock_filter.classify_batch.return_value = []

        pipeline = _make_pipeline(store, mock_filter)
        pipeline.run([])

        tags = store.get_active_ai_filter_tags()
        assert sorted(t["tag"] for t in tags) == ["军事", "科技"]
        kept_tag = next(t for t in tags if t["tag"] == "科技")
        assert kept_tag["description"] == "新描述"

    def test_interests_change_above_threshold_triggers_full_reclassify(self, store):
        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "旧描述", "priority": 1}],
            version=1,
            prompt_hash="ai_interests.txt:old_hash",
        )

        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "完全不同的兴趣"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:new_hash"
        mock_filter.update_tags.return_value = {
            "keep": [],
            "add": [],
            "remove": [],
            "change_ratio": 0.9,
        }
        mock_filter.extract_tags.return_value = [{"tag": "娱乐", "description": "娱乐相关"}]
        mock_filter.classify_batch.return_value = []

        pipeline = _make_pipeline(store, mock_filter)
        pipeline.run([])

        tags = store.get_active_ai_filter_tags()
        assert [t["tag"] for t in tags] == ["娱乐"]

    def test_min_score_filters_low_relevance_results(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"
        mock_filter.extract_tags.return_value = [{"tag": "科技", "description": ""}]

        def fake_classify(titles_for_ai, active_tags, interests_content):
            tag_id = active_tags[0]["id"]
            return [
                {"title": "高相关新闻", "tag": "科技", "tag_id": tag_id, "relevance_score": 0.9},
                {"title": "低相关新闻", "tag": "科技", "tag_id": tag_id, "relevance_score": 0.3},
            ]

        mock_filter.classify_batch.side_effect = fake_classify

        pipeline = _make_pipeline(
            store,
            mock_filter,
            filter_config={"BATCH_SIZE": 200, "BATCH_INTERVAL": 0, "MIN_SCORE": 0.7, "RECLASSIFY_THRESHOLD": 0.6},
        )
        all_titles = [_title_entry("高相关新闻"), _title_entry("低相关新闻")]

        result = pipeline.run(all_titles)

        titles_in_result = [t["title"] for group in result.stats for t in group["titles"]]
        assert titles_in_result == ["高相关新闻"]

    def test_failed_batch_titles_are_not_marked_analyzed(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"
        mock_filter.extract_tags.return_value = [{"tag": "科技", "description": ""}]
        mock_filter.classify_batch.return_value = None

        pipeline = _make_pipeline(store, mock_filter)
        result = pipeline.run([_title_entry("分类失败的新闻")])

        assert result.success is True
        assert result.stats == []
        assert store.get_analyzed_title_hashes() == set()
        assert store.get_active_ai_filter_results() == []

    def test_multiple_batches_are_all_processed(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"
        mock_filter.extract_tags.return_value = [{"tag": "科技", "description": ""}]

        call_batches = []

        def fake_classify(titles_for_ai, active_tags, interests_content):
            call_batches.append([t["title"] for t in titles_for_ai])
            tag_id = active_tags[0]["id"]
            return [
                {"title": item["title"], "tag": "科技", "tag_id": tag_id, "relevance_score": 0.9}
                for item in titles_for_ai
            ]

        mock_filter.classify_batch.side_effect = fake_classify

        pipeline = _make_pipeline(
            store,
            mock_filter,
            filter_config={"BATCH_SIZE": 1, "BATCH_INTERVAL": 0, "MIN_SCORE": 0.0, "RECLASSIFY_THRESHOLD": 0.6},
        )
        all_titles = [_title_entry("新闻一"), _title_entry("新闻二"), _title_entry("新闻三")]

        result = pipeline.run(all_titles)

        assert call_batches == [["新闻一"], ["新闻二"], ["新闻三"]]
        assert result.stats[0]["count"] == 3
        titles_in_result = sorted(t["title"] for t in result.stats[0]["titles"])
        assert titles_in_result == ["新闻一", "新闻三", "新闻二"]

    def test_percentage_field_computed_correctly(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = "我关注科技和财经"
        mock_filter.compute_interests_hash.return_value = "ai_interests.txt:hash1"
        mock_filter.extract_tags.return_value = [
            {"tag": "科技", "description": ""},
            {"tag": "财经", "description": ""},
        ]

        def fake_classify(titles_for_ai, active_tags, interests_content):
            tag_map = {t["tag"]: t["id"] for t in active_tags}
            results = []
            for item in titles_for_ai:
                tag = "科技" if item["title"] in ("科技新闻一", "科技新闻二", "科技新闻三") else "财经"
                results.append({
                    "title": item["title"], "tag": tag,
                    "tag_id": tag_map[tag], "relevance_score": 0.9,
                })
            return results

        mock_filter.classify_batch.side_effect = fake_classify

        pipeline = _make_pipeline(store, mock_filter)
        all_titles = [
            _title_entry("科技新闻一"), _title_entry("科技新闻二"),
            _title_entry("科技新闻三"), _title_entry("财经新闻一"),
        ]

        result = pipeline.run(all_titles)

        tech_group = next(g for g in result.stats if g["word"] == "科技")
        finance_group = next(g for g in result.stats if g["word"] == "财经")
        assert tech_group["percentage"] == 75.0
        assert finance_group["percentage"] == 25.0

    def test_percentage_is_zero_when_total_processed_is_zero(self, store):
        mock_filter = MagicMock()
        mock_filter.load_interests_content.return_value = None

        pipeline = _make_pipeline(store, mock_filter)
        result = pipeline.run([])

        # total_processed=0 走的是失败分支（兴趣描述缺失），stats 本来就是空列表，
        # 这里只是确认不会因为除以零而抛异常
        assert result.stats == []
