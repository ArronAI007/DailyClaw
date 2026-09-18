# coding=utf-8

from unittest.mock import MagicMock, patch

from trendradar.ai.client import AIClient


class TestAIClientConfig:
    def test_default_config_values(self):
        client = AIClient({})
        assert client.model == "deepseek/deepseek-v4-flash"
        assert client.temperature == 1.0
        assert client.max_tokens == 5000
        assert client.timeout == 120
        assert client.num_retries == 1

    def test_api_key_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("AI_API_KEY", "env-key")
        client = AIClient({})
        assert client.api_key == "env-key"

    def test_api_key_from_config_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("AI_API_KEY", "env-key")
        client = AIClient({"API_KEY": "config-key"})
        assert client.api_key == "config-key"


class TestAIClientChat:
    @patch("trendradar.ai.client.completion")
    def test_chat_returns_content_string(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="hello"))]
        mock_completion.return_value = mock_response

        client = AIClient({"MODEL": "deepseek/deepseek-v4-flash", "API_KEY": "k"})
        result = client.chat([{"role": "user", "content": "hi"}])

        assert result == "hello"
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["model"] == "deepseek/deepseek-v4-flash"
        assert call_kwargs["api_key"] == "k"

    @patch("trendradar.ai.client.completion")
    def test_chat_joins_list_content(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content=[{"text": "part1"}, {"text": "part2"}]))
        ]
        mock_completion.return_value = mock_response

        client = AIClient({})
        result = client.chat([{"role": "user", "content": "hi"}])
        assert result == "part1\npart2"

    @patch("trendradar.ai.client.completion")
    def test_chat_omits_max_tokens_when_zero(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_completion.return_value = mock_response

        client = AIClient({"MAX_TOKENS": 0})
        client.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_completion.call_args.kwargs
        assert "max_tokens" not in call_kwargs

    @patch("trendradar.ai.client.completion")
    def test_chat_includes_fallback_models(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_completion.return_value = mock_response

        client = AIClient({"FALLBACK_MODELS": ["openai/gpt-4o-mini"]})
        client.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["fallbacks"] == ["openai/gpt-4o-mini"]

    @patch("trendradar.ai.client.completion")
    def test_chat_kwargs_override_instance_defaults(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_completion.return_value = mock_response

        client = AIClient({"TEMPERATURE": 1.0})
        client.chat([{"role": "user", "content": "hi"}], temperature=0.2)
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["temperature"] == 0.2
