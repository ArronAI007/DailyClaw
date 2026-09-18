# coding=utf-8

import json
from unittest.mock import patch

import pytest

from trendradar.ai.filter import AIFilter


@pytest.fixture
def prompt_files(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    ai_filter_dir = config_dir / "ai_filter"
    ai_filter_dir.mkdir(parents=True)

    (ai_filter_dir / "prompt.txt").write_text(
        "[system]\n你是分类专家\n\n"
        "[user]\n{interests_content}\n{tags_list}\n{news_count}\n{news_list}",
        encoding="utf-8",
    )
    (ai_filter_dir / "extract_prompt.txt").write_text(
        "[system]\n你是标签提取专家\n\n[user]\n{interests_content}",
        encoding="utf-8",
    )
    (ai_filter_dir / "update_tags_prompt.txt").write_text(
        "[system]\n你是标签管理专家\n\n[user]\n{old_tags_json}\n{interests_content}",
        encoding="utf-8",
    )

    monkeypatch.setattr("trendradar.ai.prompt_loader._CONFIG_ROOT", config_dir)
    return config_dir


@pytest.fixture
def ai_filter(prompt_files):
    filter_config = {
        "PROMPT_FILE": "prompt.txt",
        "EXTRACT_PROMPT_FILE": "extract_prompt.txt",
        "UPDATE_TAGS_PROMPT_FILE": "update_tags_prompt.txt",
    }
    return AIFilter({}, filter_config)


class TestComputeInterestsHash:
    def test_same_content_same_hash(self, ai_filter):
        h1 = ai_filter.compute_interests_hash("科技新闻", "ai_interests.txt")
        h2 = ai_filter.compute_interests_hash("科技新闻", "ai_interests.txt")
        assert h1 == h2

    def test_ignores_comments_and_blank_lines(self, ai_filter):
        h1 = ai_filter.compute_interests_hash("科技新闻", "ai_interests.txt")
        h2 = ai_filter.compute_interests_hash("# 注释\n\n科技新闻\n\n", "ai_interests.txt")
        assert h1 == h2

    def test_filename_included_in_hash(self, ai_filter):
        h1 = ai_filter.compute_interests_hash("科技新闻", "a.txt")
        h2 = ai_filter.compute_interests_hash("科技新闻", "b.txt")
        assert h1 != h2


class TestExtractTags:
    def test_parses_valid_response(self, ai_filter):
        response = '```json\n{"tags": [{"tag": "科技", "description": "科技相关"}]}\n```'
        with patch.object(ai_filter.client, "chat", return_value=response):
            tags = ai_filter.extract_tags("我关注科技新闻")
        assert tags == [{"tag": "科技", "description": "科技相关"}]

    def test_invalid_json_returns_empty_list(self, ai_filter):
        with patch.object(ai_filter.client, "chat", return_value="not json"):
            tags = ai_filter.extract_tags("我关注科技新闻")
        assert tags == []

    def test_client_exception_returns_empty_list(self, ai_filter):
        with patch.object(ai_filter.client, "chat", side_effect=RuntimeError("timeout")):
            tags = ai_filter.extract_tags("我关注科技新闻")
        assert tags == []

    def test_filters_tags_missing_tag_field(self, ai_filter):
        response = '```json\n{"tags": [{"description": "no tag field"}, {"tag": "valid", "description": "ok"}]}\n```'
        with patch.object(ai_filter.client, "chat", return_value=response):
            tags = ai_filter.extract_tags("兴趣描述")
        assert tags == [{"tag": "valid", "description": "ok"}]


class TestUpdateTags:
    def test_parses_valid_response(self, ai_filter):
        response = json.dumps(
            {
                "keep": [{"tag": "科技", "description": "更新后的描述"}],
                "add": [{"tag": "军事", "description": "军事相关"}],
                "remove": ["娱乐"],
                "change_ratio": 0.3,
            },
            ensure_ascii=False,
        )
        old_tags = [{"id": 1, "tag": "科技", "description": "旧描述"}]
        with patch.object(ai_filter.client, "chat", return_value=response):
            result = ai_filter.update_tags(old_tags, "我关注科技和军事")

        assert result["keep"] == [{"tag": "科技", "description": "更新后的描述"}]
        assert result["add"] == [{"tag": "军事", "description": "军事相关"}]
        assert result["remove"] == ["娱乐"]
        assert result["change_ratio"] == 0.3

    def test_invalid_json_returns_none(self, ai_filter):
        with patch.object(ai_filter.client, "chat", return_value="not json"):
            result = ai_filter.update_tags([], "兴趣描述")
        assert result is None

    def test_client_exception_returns_none(self, ai_filter):
        with patch.object(ai_filter.client, "chat", side_effect=RuntimeError("timeout")):
            result = ai_filter.update_tags([], "兴趣描述")
        assert result is None


class TestClassifyBatch:
    def test_maps_llm_tag_id_to_real_tag_id(self, ai_filter):
        titles = [{"id": 1, "title": "英伟达发布新芯片"}, {"id": 2, "title": "股市大涨"}]
        tags = [
            {"id": 101, "tag": "科技", "description": "科技相关"},
            {"id": 102, "tag": "财经", "description": "财经相关"},
        ]
        response = json.dumps(
            [{"id": 1, "tag_id": 1, "score": 0.9}, {"id": 2, "tag_id": 2, "score": 0.8}]
        )
        with patch.object(ai_filter.client, "chat", return_value=response):
            results = ai_filter.classify_batch(titles, tags, "兴趣描述")

        assert results == [
            {"title": "英伟达发布新芯片", "tag": "科技", "tag_id": 101, "relevance_score": 0.9},
            {"title": "股市大涨", "tag": "财经", "tag_id": 102, "relevance_score": 0.8},
        ]

    def test_unmatched_titles_omitted_from_response(self, ai_filter):
        titles = [{"id": 1, "title": "无关新闻"}]
        tags = [{"id": 101, "tag": "科技", "description": ""}]
        with patch.object(ai_filter.client, "chat", return_value="[]"):
            results = ai_filter.classify_batch(titles, tags, "兴趣描述")
        assert results == []

    def test_invalid_json_returns_none(self, ai_filter):
        titles = [{"id": 1, "title": "标题"}]
        tags = [{"id": 101, "tag": "科技", "description": ""}]
        with patch.object(ai_filter.client, "chat", return_value="not json"):
            results = ai_filter.classify_batch(titles, tags, "兴趣描述")
        assert results is None

    def test_empty_titles_returns_none(self, ai_filter):
        tags = [{"id": 101, "tag": "科技", "description": ""}]
        results = ai_filter.classify_batch([], tags, "兴趣描述")
        assert results is None

    def test_client_exception_returns_none(self, ai_filter):
        titles = [{"id": 1, "title": "标题"}]
        tags = [{"id": 101, "tag": "科技", "description": ""}]
        with patch.object(ai_filter.client, "chat", side_effect=RuntimeError("timeout")):
            results = ai_filter.classify_batch(titles, tags, "兴趣描述")
        assert results is None


class TestLoadInterestsContentFromRealFile:
    def test_reads_real_project_file(self):
        # 不用 prompt_files fixture，直接读 Task 3 里创建的真实 config/ai_interests.txt
        content = AIFilter({}, {}).load_interests_content()
        assert content
        assert "科技" in content


class TestExtractJson:
    def test_extracts_from_json_fence(self):
        response = '```json\n{"key": "value"}\n```'
        assert AIFilter._extract_json(response) == '{"key": "value"}'

    def test_extracts_from_bare_fence(self):
        response = '```\n{"key": "value"}\n```'
        assert AIFilter._extract_json(response) == '{"key": "value"}'

    def test_fallback_to_raw_when_no_fence(self):
        response = '{"key": "value"}'
        assert AIFilter._extract_json(response) == '{"key": "value"}'

    def test_empty_response_returns_empty_string(self):
        assert AIFilter._extract_json("") == ""
