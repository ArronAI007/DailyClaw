# coding=utf-8

import tempfile
from pathlib import Path

from trendradar.ai.prompt_loader import load_prompt_template


class TestLoadPromptTemplate:
    def test_missing_file_returns_empty_strings(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr("trendradar.ai.prompt_loader._CONFIG_ROOT", Path(tmpdir))
            system, user = load_prompt_template("missing.txt")
            assert system == ""
            assert user == ""

    def test_parses_system_and_user_sections(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            monkeypatch.setattr("trendradar.ai.prompt_loader._CONFIG_ROOT", config_dir)
            (config_dir / "test_prompt.txt").write_text(
                "[system]\nYou are helpful.\n\n[user]\nHello {name}",
                encoding="utf-8",
            )
            system, user = load_prompt_template("test_prompt.txt")
            assert system == "You are helpful."
            assert user == "Hello {name}"

    def test_loads_from_subdir(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            monkeypatch.setattr("trendradar.ai.prompt_loader._CONFIG_ROOT", config_dir)
            subdir = config_dir / "ai_filter"
            subdir.mkdir()
            (subdir / "prompt.txt").write_text(
                "[system]\nSys\n\n[user]\nUser", encoding="utf-8"
            )
            system, user = load_prompt_template("prompt.txt", config_subdir="ai_filter")
            assert system == "Sys"
            assert user == "User"

    def test_no_section_markers_treats_whole_file_as_user(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            monkeypatch.setattr("trendradar.ai.prompt_loader._CONFIG_ROOT", config_dir)
            (config_dir / "plain.txt").write_text("Just user content", encoding="utf-8")
            system, user = load_prompt_template("plain.txt")
            assert system == ""
            assert user == "Just user content"
