# coding=utf-8

import tempfile
from pathlib import Path

import pytest

from trendradar.storage.ai_filter_store import AIFilterStore, title_hash


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.sqlite3")
        yield AIFilterStore(db_path)


class TestTitleHash:
    def test_same_title_same_hash(self):
        assert title_hash("科技新闻") == title_hash("科技新闻")

    def test_different_titles_different_hash(self):
        assert title_hash("科技新闻") != title_hash("财经新闻")

    def test_strips_whitespace(self):
        assert title_hash("  标题  ") == title_hash("标题")


class TestAIFilterStoreTags:
    def test_no_tags_initially(self, store):
        assert store.get_active_ai_filter_tags() == []
        assert store.get_latest_prompt_hash() is None
        assert store.get_latest_ai_filter_tag_version() == 0

    def test_save_and_get_active_tags(self, store):
        saved = store.save_ai_filter_tags(
            [
                {"tag": "科技", "description": "科技相关", "priority": 1},
                {"tag": "财经", "description": "财经相关", "priority": 2},
            ],
            version=1,
            prompt_hash="file:abc",
        )
        assert saved == 2

        tags = store.get_active_ai_filter_tags()
        assert [t["tag"] for t in tags] == ["科技", "财经"]
        assert store.get_latest_prompt_hash() == "file:abc"
        assert store.get_latest_ai_filter_tag_version() == 1

    def test_deprecate_all_tags(self, store):
        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "", "priority": 1}],
            version=1,
            prompt_hash="file:abc",
        )
        deprecated_count = store.deprecate_all_ai_filter_tags()
        assert deprecated_count == 1
        assert store.get_active_ai_filter_tags() == []

    def test_deprecate_specific_tags(self, store):
        store.save_ai_filter_tags(
            [
                {"tag": "科技", "description": "", "priority": 1},
                {"tag": "财经", "description": "", "priority": 2},
            ],
            version=1,
            prompt_hash="file:abc",
        )
        tags = store.get_active_ai_filter_tags()
        tech_id = next(t["id"] for t in tags if t["tag"] == "科技")

        store.deprecate_specific_ai_filter_tags([tech_id])
        remaining = store.get_active_ai_filter_tags()
        assert [t["tag"] for t in remaining] == ["财经"]

    def test_update_tag_priorities(self, store):
        store.save_ai_filter_tags(
            [
                {"tag": "科技", "description": "", "priority": 1},
                {"tag": "财经", "description": "", "priority": 2},
            ],
            version=1,
            prompt_hash="file:abc",
        )
        store.update_ai_filter_tag_priorities(
            [{"tag": "财经", "priority": 1}, {"tag": "科技", "priority": 2}]
        )
        tags = store.get_active_ai_filter_tags()
        assert [t["tag"] for t in tags] == ["财经", "科技"]

    def test_update_tag_descriptions(self, store):
        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "old", "priority": 1}],
            version=1,
            prompt_hash="file:abc",
        )
        store.update_ai_filter_tag_descriptions([{"tag": "科技", "description": "new"}])
        tags = store.get_active_ai_filter_tags()
        assert tags[0]["description"] == "new"

    def test_interests_file_isolation(self, store):
        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "", "priority": 1}],
            version=1,
            prompt_hash="a:1",
            interests_file="a.txt",
        )
        store.save_ai_filter_tags(
            [{"tag": "军事", "description": "", "priority": 1}],
            version=1,
            prompt_hash="b:1",
            interests_file="b.txt",
        )
        assert [t["tag"] for t in store.get_active_ai_filter_tags("a.txt")] == ["科技"]
        assert [t["tag"] for t in store.get_active_ai_filter_tags("b.txt")] == ["军事"]


class TestAIFilterStoreAnalyzedNews:
    def test_no_analyzed_hashes_initially(self, store):
        assert store.get_analyzed_title_hashes() == set()

    def test_save_and_get_analyzed_hashes(self, store):
        h1 = title_hash("新闻一")
        h2 = title_hash("新闻二")
        store.save_analyzed_titles([h1, h2], "ai_interests.txt", "file:abc", matched_hashes={h1})
        assert store.get_analyzed_title_hashes() == {h1, h2}

    def test_clear_unmatched_analyzed_news(self, store):
        h1 = title_hash("新闻一")
        h2 = title_hash("新闻二")
        store.save_analyzed_titles([h1, h2], "ai_interests.txt", "file:abc", matched_hashes={h1})
        cleared = store.clear_unmatched_analyzed_news()
        assert cleared == 1
        assert store.get_analyzed_title_hashes() == {h1}


class TestAIFilterStoreResults:
    def test_save_and_get_active_results(self, store):
        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "desc", "priority": 1}],
            version=1,
            prompt_hash="file:abc",
        )
        tag_id = store.get_active_ai_filter_tags()[0]["id"]
        h1 = title_hash("新闻一")

        saved = store.save_ai_filter_results(
            [{"title_hash": h1, "tag_id": tag_id, "relevance_score": 0.9}]
        )
        assert saved == 1

        results = store.get_active_ai_filter_results()
        assert len(results) == 1
        assert results[0]["title_hash"] == h1
        assert results[0]["tag"] == "科技"
        assert results[0]["relevance_score"] == 0.9

    def test_results_excluded_when_tag_deprecated(self, store):
        store.save_ai_filter_tags(
            [{"tag": "科技", "description": "", "priority": 1}],
            version=1,
            prompt_hash="file:abc",
        )
        tag_id = store.get_active_ai_filter_tags()[0]["id"]
        h1 = title_hash("新闻一")
        store.save_ai_filter_results([{"title_hash": h1, "tag_id": tag_id, "relevance_score": 0.9}])

        store.deprecate_all_ai_filter_tags()
        assert store.get_active_ai_filter_results() == []
