# coding=utf-8
"""AI 客户端模块

基于 LiteLLM 的统一 AI 模型接口，支持 DeepSeek、OpenAI、Gemini、Claude 等
100+ AI 提供商，通过 model 字符串的 "provider/model_name" 格式切换。
"""

import os
from typing import Any, Dict, List

from litellm import completion


class AIClient:
    """统一的 AI 客户端（基于 LiteLLM）"""

    def __init__(self, config: Dict[str, Any]):
        self.model = config.get("MODEL", "deepseek/deepseek-v4-flash")
        self.api_key = config.get("API_KEY") or os.environ.get("AI_API_KEY", "")
        self.temperature = config.get("TEMPERATURE", 1.0)
        self.max_tokens = config.get("MAX_TOKENS", 5000)
        self.timeout = config.get("TIMEOUT", 120)
        self.num_retries = config.get("NUM_RETRIES", 1)
        self.fallback_models = config.get("FALLBACK_MODELS", [])

    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """调用 AI 模型进行对话，返回响应文本内容。

        Raises:
            Exception: API 调用失败时抛出（litellm 内部异常），调用方负责捕获。
        """
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "timeout": kwargs.get("timeout", self.timeout),
            "num_retries": kwargs.get("num_retries", self.num_retries),
        }

        if self.api_key:
            params["api_key"] = self.api_key

        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens and max_tokens > 0:
            params["max_tokens"] = max_tokens

        if self.fallback_models:
            params["fallbacks"] = self.fallback_models

        response = completion(**params)

        content = response.choices[0].message.content
        if isinstance(content, list):
            content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return content or ""
