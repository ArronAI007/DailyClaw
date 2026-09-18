# coding=utf-8

from pathlib import Path

from trendradar.ai.prompt_loader import load_prompt_template


class TestClassifyPromptFile:
    def test_loads_and_has_required_placeholders(self):
        system, user = load_prompt_template("prompt.txt", config_subdir="ai_filter")
        assert system
        assert "{interests_content}" in user
        assert "{tags_list}" in user
        assert "{news_count}" in user
        assert "{news_list}" in user


class TestExtractPromptFile:
    def test_loads_and_has_required_placeholders(self):
        system, user = load_prompt_template("extract_prompt.txt", config_subdir="ai_filter")
        assert system
        assert "{interests_content}" in user


class TestUpdateTagsPromptFile:
    def test_loads_and_has_required_placeholders(self):
        system, user = load_prompt_template("update_tags_prompt.txt", config_subdir="ai_filter")
        assert system
        assert "{old_tags_json}" in user
        assert "{interests_content}" in user


class TestAiInterestsFile:
    def test_exists_and_has_seed_categories(self):
        content = Path("config/ai_interests.txt").read_text(encoding="utf-8")
        assert "科技" in content
        assert "财经" in content
        assert "娱乐" in content
        assert "军事" in content
