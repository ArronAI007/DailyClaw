# AI 分类引擎（子项目 1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移植 TrendRadar 上游的 LLM 批量新闻分类引擎（`trendradar/ai/` + `trendradar/storage/`），产出跟 `main.py::count_word_frequency()` 兼容的 `stats` 结构，供子项目 2（接入爬取管线）和子项目 3（Web UI 分类 Tab）使用。

**Architecture:** `AIClient` 通过 LiteLLM 统一接入 DeepSeek/OpenAI/Gemini 等模型；`AIFilter` 负责三阶段 LLM 调用（提取标签/更新标签/批量分类）；`AIFilterStore` 用 SQLite 管理标签版本和已分类新闻去重（用 `title_hash` 代替上游的 `news_item_id`，因为 DailyClaw 没有全量新闻数据库）；`AIFilterPipeline` 编排完整流程并把结果转换成跟现有关键词分组结构一致的 `stats`。

**Tech Stack:** Python 3.10+, litellm, 标准库 sqlite3, pytest（LLM 调用全 mock）

**参考设计文档：** `docs/superpowers/specs/2026-09-18-news-category-classification-design.md`

**范围边界：** 本计划只做 AI 分类引擎本身。不改动 `main.py`、`web_server/`、任何现有文件。新代码完全独立存在，没有任何调用方，靠自己的测试验证行为——这是有意为之，接入工作是子项目 2 的范围。

---

### Task 1: 新增 litellm 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 加依赖**

在 `pyproject.toml` 的 `dependencies` 列表里加一行：

```toml
dependencies = [
    "requests>=2.32.5,<3.0.0",
    "pytz>=2025.2,<2026.0",
    "PyYAML>=6.0.3,<7.0.0",
    "pydantic>=2.0,<3.0.0",
    "pydantic-settings>=2.0,<3.0.0",
    "Jinja2>=3.1.0,<4.0.0",
    "fastmcp>=2.12.0,<2.14.0",
    "websockets>=13.0,<14.0",
    "structlog>=24.1.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "litellm>=1.50.0,<2.0.0",
]
```

- [ ] **Step 2: 安装依赖**

Run: `env -u PYTHONPATH uv sync`
Expected: 安装成功，`litellm` 出现在 `uv.lock` 里

- [ ] **Step 3: 验证可导入**

Run: `env -u PYTHONPATH uv run python -c "import litellm; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add litellm dependency for AI classification"
```

---

### Task 2: prompt_loader.py

**Files:**
- Create: `trendradar/ai/__init__.py`
- Create: `trendradar/ai/prompt_loader.py`
- Test: `tests/test_prompt_loader.py`

- [ ] **Step 1: 创建空包文件**

`trendradar/ai/__init__.py`:

```python
# coding=utf-8
```

- [ ] **Step 2: 写失败的测试**

`tests/test_prompt_loader.py`:

```python
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
```

- [ ] **Step 3: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_prompt_loader.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'trendradar.ai.prompt_loader'`

- [ ] **Step 4: 实现**

`trendradar/ai/prompt_loader.py`:

```python
# coding=utf-8
"""提示词模板加载工具

从 config 目录中加载 [system] / [user] 格式的提示词文件。
"""

from pathlib import Path
from typing import Tuple

_CONFIG_ROOT = Path(__file__).parent.parent.parent / "config"


def load_prompt_template(
    prompt_file: str,
    config_subdir: str = "",
    label: str = "AI",
) -> Tuple[str, str]:
    """加载提示词模板文件，解析 [system] 和 [user] 部分。

    Args:
        prompt_file: 提示词文件名
        config_subdir: config 下的子目录（如 "ai_filter"），为空则直接在 config/ 下查找
        label: 日志标签，用于提示文件缺失时的打印

    Returns:
        (system_prompt, user_prompt_template) 元组；文件不存在时返回 ("", "")
    """
    config_dir = _CONFIG_ROOT / config_subdir if config_subdir else _CONFIG_ROOT
    prompt_path = config_dir / prompt_file

    if not prompt_path.exists():
        print(f"[{label}] 提示词文件不存在: {prompt_path}")
        return "", ""

    content = prompt_path.read_text(encoding="utf-8")

    system_prompt = ""
    user_prompt = ""

    if "[system]" in content and "[user]" in content:
        parts = content.split("[user]")
        system_part = parts[0]
        user_part = parts[1] if len(parts) > 1 else ""

        if "[system]" in system_part:
            system_prompt = system_part.split("[system]")[1].strip()

        user_prompt = user_part.strip()
    else:
        user_prompt = content.strip()

    return system_prompt, user_prompt
```

- [ ] **Step 5: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_prompt_loader.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add trendradar/ai/__init__.py trendradar/ai/prompt_loader.py tests/test_prompt_loader.py
git commit -m "feat: add prompt template loader for AI classification"
```

---

### Task 3: Prompt 文件 + 兴趣描述文件

**Files:**
- Create: `config/ai_filter/prompt.txt`
- Create: `config/ai_filter/extract_prompt.txt`
- Create: `config/ai_filter/update_tags_prompt.txt`
- Create: `config/ai_interests.txt`
- Test: `tests/test_ai_filter_prompt_files.py`

这些是纯配置文件，没有代码逻辑好测；测试改成"用真实的 `load_prompt_template`（Task 2 写的）加载这些真实文件，确认能正确解析、占位符齐全"。

- [ ] **Step 1: 写测试（先跑会失败，因为文件还不存在）**

`tests/test_ai_filter_prompt_files.py`:

```python
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
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_prompt_files.py -v`
Expected: FAIL（`[AI] 提示词文件不存在` 打印 + `assert system` 断言失败，因为 system 是空字符串）

- [ ] **Step 3: 创建分类 prompt**

`config/ai_filter/prompt.txt`:

```
[system]
你是一个高效的新闻分类专家。根据给定的标签列表，快速判断每条新闻标题最适合哪个标签。

分类规则：
1. 每条新闻只归入一个最相关的标签（选相关度最高的那个）
2. 不匹配任何标签的新闻不要输出（不要返回空 tags）
3. 给出 0.0-1.0 的相关度分数（1.0=完全相关，0.5=部分相关）
4. 只根据标题判断，不要过度推测
5. 严格遵循用户偏好中的额外过滤要求（如有）
6. 如果两类标签相关度接近，优先选择排序更靠前的标签（前面的标签优先级更高）

[user]
## 用户偏好

{interests_content}

## 分类标签

{tags_list}

## 新闻列表（共 {news_count} 条）

{news_list}

请对每条新闻进行分类。返回严格的 JSON 数组（不要添加任何其他内容）：
```json
[
  {"id": 1, "tag_id": 1, "score": 0.9},
  {"id": 5, "tag_id": 2, "score": 0.8}
]
```
只返回有匹配的新闻，无匹配的不要包含在结果中。
```

- [ ] **Step 4: 创建标签提取 prompt**

`config/ai_filter/extract_prompt.txt`:

```
[system]
你是一个兴趣标签提取专家。你的任务是从用户的兴趣描述中提取出结构化的新闻分类标签。

提取规则：
1. 每个标签简洁（2-6个字），同时配一句描述说明该标签涵盖哪些话题和关键词
2. 标签之间尽量不重叠
3. 标签数量控制在 5~20 个，优先保留细分标签，只有语义高度重叠时才合并
4. 描述要具体，包含具体的人名、公司名、产品名等关键词，方便后续分类
5. 返回顺序必须尽量遵循用户兴趣描述中的先后顺序，越靠前代表优先级越高

[user]
用户的兴趣描述如下：

{interests_content}

请从中提取出新闻分类标签。

返回严格的 JSON 格式（不要添加任何其他内容）：
```json
{
  "tags": [
    {"tag": "标签名", "description": "该标签涵盖的话题、关键词描述"}
  ]
}
```
```

- [ ] **Step 5: 创建标签更新 prompt**

`config/ai_filter/update_tags_prompt.txt`:

```
[system]
你是一个标签管理专家。用户修改了兴趣描述后，你需要对比旧标签集和新的兴趣描述，给出标签更新方案。

核心原则：
1. 语义等价的标签视为同一个标签（如"AI/大模型"和"AI与大模型"是同一个标签），优先保留旧标签名
2. 只有用户明确不再关注的方向才标记移除
3. 新增的兴趣方向才需要新增标签
4. 标签名简洁（2-10个字），描述要具体，包含关键词、人名、公司名、产品名
5. 标签总数控制在 20 个以内，优先保留细分标签，只有语义高度重叠时再合并
6. keep 和 add 的输出顺序应尽量遵循用户兴趣描述中的先后顺序（越靠前优先级越高）

change_ratio 评估标准：
- 0.0 = 兴趣几乎没变（只是措辞调整、补充细节）
- 0.1~0.3 = 小幅调整（新增或移除了 1-2 个方向）
- 0.4~0.6 = 中等变化（多个方向有调整）
- 0.7~1.0 = 大幅改变（兴趣方向基本重写）

[user]
## 当前标签集

{old_tags_json}

## 新的兴趣描述

{interests_content}

## 任务

对比当前标签集和新的兴趣描述，判断每个旧标签是保留还是移除，以及是否需要新增标签。

返回严格的 JSON 格式（不要添加任何其他内容）：
```json
{
  "keep": [
    {"tag": "旧标签名", "description": "根据新兴趣更新后的描述"}
  ],
  "add": [
    {"tag": "新标签名", "description": "该标签涵盖的话题、关键词描述"}
  ],
  "remove": ["要废弃的旧标签名"],
  "change_ratio": 0.2
}
```
```

- [ ] **Step 6: 创建兴趣描述种子文件**

`config/ai_interests.txt`:

```
# ═══════════════════════════════════════════════════════════════
#                DailyClaw AI 兴趣描述文件
# ═══════════════════════════════════════════════════════════════
# 用自然语言描述你关注的话题，AI 会自动提取标签并对新闻进行分类。
# 修改此文件后，下次运行 AI 分类时自动生效（旧分类会被标记废弃，重新分类）。

下面是我要关注的内容：
# 重要性排序说明：从上到下优先级递减，越靠前越重要。
# 如果一条新闻同时可能匹配多个方向，请优先归入更靠前的方向。

1. 科技：关注人工智能、大模型、芯片、半导体、互联网大厂、消费电子等科技产业动态。
2. 财经：关注股市、汇率、利率、宏观经济政策、企业财报、并购等金融与商业新闻。
3. 娱乐：关注影视、音乐、游戏、明星动态、文化 IP 等娱乐产业内容。
4. 军事：关注国防、武器装备、军事冲突、地缘安全等军事相关新闻。

# 标题质量要求（即使匹配了上面的标签，符合以下特征的标题也请跳过）
# 可自由增删改，按你的偏好来
- 不要标题党/震惊体（如"震惊！"、"太可怕了！"、"竟然..."、"刚刚！"）
- 不要营销软文、广告推广类标题
```

- [ ] **Step 7: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_prompt_files.py -v`
Expected: 4 passed

- [ ] **Step 8: Commit**

```bash
git add config/ai_filter/ config/ai_interests.txt tests/test_ai_filter_prompt_files.py
git commit -m "feat: add AI classification prompt templates and default interests file"
```

---

### Task 4: AIClient

**Files:**
- Create: `trendradar/ai/client.py`
- Test: `tests/test_ai_client.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_ai_client.py`:

```python
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
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_client.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'trendradar.ai.client'`

- [ ] **Step 3: 实现**

`trendradar/ai/client.py`:

```python
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
```

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_client.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add trendradar/ai/client.py tests/test_ai_client.py
git commit -m "feat: add AIClient (litellm-based multi-provider LLM client)"
```

---

### Task 5: config.yaml + config.py 新增 FILTER/AI/AI_FILTER 配置

**Files:**
- Modify: `config/config.yaml`
- Modify: `trendradar/config.py`
- Modify: `tests/test_config_load.py`

- [ ] **Step 1: 写失败的测试（追加到现有文件）**

在 `tests/test_config_load.py` 的 `TestLoadConfig` 类里追加以下方法（放在已有的 `test_env_override_webhook_url` 方法后面）：

```python
    def test_filter_method_defaults_to_keyword(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            config = load_config()
            assert config["FILTER"]["METHOD"] == "keyword"

    def test_env_override_filter_method(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("FILTER_METHOD", "ai")
            config = load_config()
            assert config["FILTER"]["METHOD"] == "ai"

    def test_ai_config_defaults(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.delenv("AI_API_KEY", raising=False)
            config = load_config()
            assert config["AI"]["MODEL"] == "deepseek/deepseek-v4-flash"
            assert config["AI"]["API_KEY"] == ""
            assert config["AI"]["TIMEOUT"] == 120
            assert config["AI"]["TEMPERATURE"] == 1.0
            assert config["AI"]["MAX_TOKENS"] == 5000
            assert config["AI"]["NUM_RETRIES"] == 1
            assert config["AI"]["FALLBACK_MODELS"] == []

    def test_env_override_ai_api_key(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            monkeypatch.setenv("AI_API_KEY", "env-secret")
            config = load_config()
            assert config["AI"]["API_KEY"] == "env-secret"

    def test_ai_filter_config_defaults(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(self._make_config_data()), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            config = load_config()
            assert config["AI_FILTER"]["BATCH_SIZE"] == 200
            assert config["AI_FILTER"]["BATCH_INTERVAL"] == 2
            assert config["AI_FILTER"]["MIN_SCORE"] == 0.7
            assert config["AI_FILTER"]["RECLASSIFY_THRESHOLD"] == 0.6
            assert config["AI_FILTER"]["PROMPT_FILE"] == "prompt.txt"
            assert config["AI_FILTER"]["EXTRACT_PROMPT_FILE"] == "extract_prompt.txt"
            assert config["AI_FILTER"]["UPDATE_TAGS_PROMPT_FILE"] == "update_tags_prompt.txt"

    def test_custom_filter_ai_sections_override_defaults(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = self._make_config_data()
            data["filter"] = {"method": "ai"}
            data["ai"] = {"model": "openai/gpt-4o-mini", "api_key": "k"}
            data["ai_filter"] = {"batch_size": 50, "min_score": 0.5}
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
            monkeypatch.setenv("CONFIG_PATH", str(config_path))
            config = load_config()
            assert config["FILTER"]["METHOD"] == "ai"
            assert config["AI"]["MODEL"] == "openai/gpt-4o-mini"
            assert config["AI_FILTER"]["BATCH_SIZE"] == 50
            assert config["AI_FILTER"]["MIN_SCORE"] == 0.5
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_config_load.py -v -k "filter_method or ai_config or ai_filter_config or override_ai_api_key or custom_filter_ai"`
Expected: FAIL，`KeyError: 'FILTER'`

- [ ] **Step 3: 实现 config.py 改动**

在 `trendradar/config.py` 的 `load_config()` 函数里，把 `"PLATFORMS": config_data["platforms"],` 这一行（第 255 行）改成：

```python
        "PLATFORMS": config_data["platforms"],
        "FILTER": {
            "METHOD": os.environ.get("FILTER_METHOD", "").strip()
            or config_data.get("filter", {}).get("method", "keyword"),
        },
        "AI": {
            "MODEL": os.environ.get("AI_MODEL", "").strip()
            or config_data.get("ai", {}).get("model", "deepseek/deepseek-v4-flash"),
            "API_KEY": os.environ.get("AI_API_KEY", "").strip()
            or config_data.get("ai", {}).get("api_key", ""),
            "TIMEOUT": int(os.environ.get("AI_TIMEOUT", "").strip() or "0")
            or config_data.get("ai", {}).get("timeout", 120),
            "TEMPERATURE": config_data.get("ai", {}).get("temperature", 1.0),
            "MAX_TOKENS": config_data.get("ai", {}).get("max_tokens", 5000),
            "NUM_RETRIES": config_data.get("ai", {}).get("num_retries", 1),
            "FALLBACK_MODELS": config_data.get("ai", {}).get("fallback_models", []),
        },
        "AI_FILTER": {
            "BATCH_SIZE": config_data.get("ai_filter", {}).get("batch_size", 200),
            "BATCH_INTERVAL": config_data.get("ai_filter", {}).get("batch_interval", 2),
            "MIN_SCORE": config_data.get("ai_filter", {}).get("min_score", 0.7),
            "RECLASSIFY_THRESHOLD": config_data.get("ai_filter", {}).get(
                "reclassify_threshold", 0.6
            ),
            "PROMPT_FILE": config_data.get("ai_filter", {}).get("prompt_file", "prompt.txt"),
            "EXTRACT_PROMPT_FILE": config_data.get("ai_filter", {}).get(
                "extract_prompt_file", "extract_prompt.txt"
            ),
            "UPDATE_TAGS_PROMPT_FILE": config_data.get("ai_filter", {}).get(
                "update_tags_prompt_file", "update_tags_prompt.txt"
            ),
        },
    }
```

（注意：这替换的是原来紧跟在 `"PLATFORMS": config_data["platforms"],` 后面的字典收尾 `}`，新增的三段配置插在这一行和原来的收尾 `}` 之间。）

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_config_load.py -v`
Expected: 全部 passed（新增 6 个 + 原有的全部）

- [ ] **Step 5: 更新真实的 config/config.yaml**

在 `config/config.yaml` 里找到 `platforms:` 段之前的合适位置（建议紧跟在现有筛选相关配置附近，如果没有可以加在文件末尾），追加：

```yaml
# ===============================================================
# AI 智能分类（新闻分类浏览功能）
# ===============================================================
filter:
  method: "keyword"          # keyword（现状，关键词过滤） | ai（AI 智能分类）

ai:
  # LiteLLM 模型格式: 提供商/模型名，完整列表见 https://docs.litellm.ai/docs/providers
  model: "deepseek/deepseek-v4-flash"
  api_key: ""                # 建议用环境变量 AI_API_KEY，不要写进这个文件
  timeout: 120
  temperature: 1.0
  max_tokens: 5000
  num_retries: 1
  fallback_models: []

ai_filter:
  batch_size: 200             # 每批发送给 AI 的标题数
  batch_interval: 2           # 分批间隔（秒），避免触发 API 限流
  min_score: 0.7              # 展示最低相关度分数阈值（0.0~1.0）
  reclassify_threshold: 0.6   # 兴趣描述变更时，change_ratio 超过此值触发全量重分类
  prompt_file: "prompt.txt"
  extract_prompt_file: "extract_prompt.txt"
  update_tags_prompt_file: "update_tags_prompt.txt"
```

- [ ] **Step 6: 验证真实配置文件能被 load_config() 正常加载**

Run: `env -u PYTHONPATH uv run python -c "from trendradar.config import load_config; c = load_config(); print(c['FILTER']['METHOD'], c['AI']['MODEL'], c['AI_FILTER']['BATCH_SIZE'])"`
Expected: 输出 `keyword deepseek/deepseek-v4-flash 200`

- [ ] **Step 7: Commit**

```bash
git add config/config.yaml trendradar/config.py tests/test_config_load.py
git commit -m "feat: add filter/ai/ai_filter config sections"
```

---

### Task 6: AIFilterStore（SQLite 存储层）

**Files:**
- Create: `trendradar/storage/__init__.py`
- Create: `trendradar/storage/ai_filter_store.py`
- Test: `tests/test_ai_filter_store.py`
- Modify: `.gitignore`

- [ ] **Step 1: 创建空包文件**

`trendradar/storage/__init__.py`:

```python
# coding=utf-8
```

- [ ] **Step 2: 写失败的测试**

`tests/test_ai_filter_store.py`:

```python
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
```

- [ ] **Step 3: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_store.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'trendradar.storage.ai_filter_store'`

- [ ] **Step 4: 实现**

`trendradar/storage/ai_filter_store.py`:

```python
# coding=utf-8
"""AI 筛选结果的 SQLite 存储层

管理三张表：
- ai_filter_tags: 标签版本管理（从兴趣描述提取出的分类标签，按 interests_file 隔离）
- ai_filter_results: 新闻 × 标签 的分类结果
- ai_filter_analyzed_news: 已分析过的新闻标题记录（按 title_hash 去重，避免重复消耗 token）

用 title_hash（标题的 md5）代替上游 TrendRadar 的 news_item_id 外键，因为
DailyClaw 没有一个全量新闻 SQLite 表——新闻身份从头到尾都是标题文本本身。
"""

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_filter_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT NOT NULL,
    description TEXT DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 9999,
    status TEXT DEFAULT 'active',
    deprecated_at TEXT,
    version INTEGER NOT NULL,
    prompt_hash TEXT NOT NULL,
    interests_file TEXT NOT NULL DEFAULT 'ai_interests.txt',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_filter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_hash TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    relevance_score REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    deprecated_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(title_hash, tag_id)
);

CREATE TABLE IF NOT EXISTS ai_filter_analyzed_news (
    title_hash TEXT NOT NULL,
    interests_file TEXT NOT NULL DEFAULT 'ai_interests.txt',
    prompt_hash TEXT NOT NULL,
    matched INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (title_hash, interests_file)
);

CREATE INDEX IF NOT EXISTS idx_ai_filter_tags_status ON ai_filter_tags(status);
CREATE INDEX IF NOT EXISTS idx_ai_filter_tags_priority ON ai_filter_tags(interests_file, status, priority);
CREATE INDEX IF NOT EXISTS idx_ai_filter_results_tag ON ai_filter_results(tag_id);
CREATE INDEX IF NOT EXISTS idx_analyzed_news_lookup ON ai_filter_analyzed_news(interests_file, prompt_hash);
"""


def title_hash(title: str) -> str:
    """标题的 md5，用作新闻身份标识（替代上游的 news_item_id 外键）"""
    return hashlib.md5(title.strip().encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIFilterStore:
    """AI 筛选结果的 SQLite 存储"""

    def __init__(self, db_path: str = "data/ai_filter.sqlite3"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_latest_prompt_hash(self, interests_file: str = "ai_interests.txt") -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT prompt_hash FROM ai_filter_tags "
                "WHERE interests_file = ? ORDER BY version DESC, id DESC LIMIT 1",
                (interests_file,),
            ).fetchone()
        return row["prompt_hash"] if row else None

    def get_latest_ai_filter_tag_version(self, interests_file: str = "ai_interests.txt") -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM ai_filter_tags WHERE interests_file = ?",
                (interests_file,),
            ).fetchone()
        return row["v"] if row and row["v"] is not None else 0

    def get_active_ai_filter_tags(self, interests_file: str = "ai_interests.txt") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, tag, description, priority FROM ai_filter_tags "
                "WHERE interests_file = ? AND status = 'active' ORDER BY priority ASC",
                (interests_file,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_ai_filter_tags(
        self,
        tags_data: List[Dict[str, Any]],
        version: int,
        prompt_hash: str,
        interests_file: str = "ai_interests.txt",
    ) -> int:
        now = _now()
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO ai_filter_tags "
                "(tag, description, priority, status, version, prompt_hash, interests_file, created_at) "
                "VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
                [
                    (
                        t["tag"],
                        t.get("description", ""),
                        t.get("priority", 9999),
                        version,
                        prompt_hash,
                        interests_file,
                        now,
                    )
                    for t in tags_data
                ],
            )
        return len(tags_data)

    def deprecate_all_ai_filter_tags(self, interests_file: str = "ai_interests.txt") -> int:
        now = _now()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE ai_filter_tags SET status = 'deprecated', deprecated_at = ? "
                "WHERE interests_file = ? AND status = 'active'",
                (now, interests_file),
            )
        return cursor.rowcount

    def deprecate_specific_ai_filter_tags(self, tag_ids: List[int]) -> None:
        if not tag_ids:
            return
        now = _now()
        placeholders = ",".join("?" * len(tag_ids))
        with self._connect() as conn:
            conn.execute(
                f"UPDATE ai_filter_tags SET status = 'deprecated', deprecated_at = ? "
                f"WHERE id IN ({placeholders})",
                (now, *tag_ids),
            )

    def update_ai_filter_tag_priorities(
        self, tags: List[Dict[str, Any]], interests_file: str = "ai_interests.txt"
    ) -> None:
        with self._connect() as conn:
            for t in tags:
                conn.execute(
                    "UPDATE ai_filter_tags SET priority = ? "
                    "WHERE tag = ? AND interests_file = ? AND status = 'active'",
                    (t["priority"], t["tag"], interests_file),
                )

    def update_ai_filter_tag_descriptions(
        self, tags: List[Dict[str, Any]], interests_file: str = "ai_interests.txt"
    ) -> None:
        with self._connect() as conn:
            for t in tags:
                conn.execute(
                    "UPDATE ai_filter_tags SET description = ? "
                    "WHERE tag = ? AND interests_file = ? AND status = 'active'",
                    (t.get("description", ""), t["tag"], interests_file),
                )

    def get_analyzed_title_hashes(self, interests_file: str = "ai_interests.txt") -> Set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT title_hash FROM ai_filter_analyzed_news WHERE interests_file = ?",
                (interests_file,),
            ).fetchall()
        return {row["title_hash"] for row in rows}

    def save_analyzed_titles(
        self,
        title_hashes: List[str],
        interests_file: str,
        prompt_hash: str,
        matched_hashes: Set[str],
    ) -> None:
        now = _now()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ai_filter_analyzed_news "
                "(title_hash, interests_file, prompt_hash, matched, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (h, interests_file, prompt_hash, 1 if h in matched_hashes else 0, now)
                    for h in title_hashes
                ],
            )

    def save_ai_filter_results(self, results: List[Dict[str, Any]]) -> int:
        now = _now()
        saved = 0
        with self._connect() as conn:
            for r in results:
                cursor = conn.execute(
                    "INSERT OR REPLACE INTO ai_filter_results "
                    "(title_hash, tag_id, relevance_score, status, created_at) "
                    "VALUES (?, ?, ?, 'active', ?)",
                    (r["title_hash"], r["tag_id"], r.get("relevance_score", 0.0), now),
                )
                saved += cursor.rowcount
        return saved

    def get_active_ai_filter_results(self, interests_file: str = "ai_interests.txt") -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.title_hash, r.relevance_score, t.tag, t.description AS tag_description, "
                "t.priority AS tag_priority "
                "FROM ai_filter_results r "
                "JOIN ai_filter_tags t ON r.tag_id = t.id "
                "WHERE r.status = 'active' AND t.status = 'active' AND t.interests_file = ? "
                "ORDER BY t.priority ASC",
                (interests_file,),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_unmatched_analyzed_news(self, interests_file: str = "ai_interests.txt") -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM ai_filter_analyzed_news WHERE interests_file = ? AND matched = 0",
                (interests_file,),
            )
        return cursor.rowcount
```

- [ ] **Step 5: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_store.py -v`
Expected: 14 passed

- [ ] **Step 6: 把 data/ 加进 .gitignore**

在 `.gitignore` 里追加（跟随现有 `output/` 那条注释的风格）：

```
# AI 分类引擎的本地 SQLite 数据库
data/
```

- [ ] **Step 7: Commit**

```bash
git add trendradar/storage/__init__.py trendradar/storage/ai_filter_store.py tests/test_ai_filter_store.py .gitignore
git commit -m "feat: add AIFilterStore SQLite storage layer"
```

---

### Task 7: AIFilter（提取标签 / 更新标签 / 批量分类）

**Files:**
- Create: `trendradar/ai/filter.py`
- Test: `tests/test_ai_filter.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_ai_filter.py`:

```python
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


class TestLoadInterestsContentFromRealFile:
    def test_reads_real_project_file(self):
        # 不用 prompt_files fixture，直接读 Task 3 里创建的真实 config/ai_interests.txt
        content = AIFilter({}, {}).load_interests_content()
        assert content
        assert "科技" in content
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'trendradar.ai.filter'`

- [ ] **Step 3: 实现**

`trendradar/ai/filter.py`:

```python
# coding=utf-8
"""AI 智能筛选模块

通过 AI 对新闻进行标签分类：
1. 阶段 A：从用户兴趣描述中提取结构化标签
2. 阶段 A'：兴趣描述变更时，对比新旧标签给出更新方案
3. 阶段 B：对新闻标题按标签进行批量分类
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from trendradar.ai.client import AIClient
from trendradar.ai.prompt_loader import load_prompt_template


class AIFilter:
    """AI 智能筛选器"""

    def __init__(self, ai_config: Dict[str, Any], filter_config: Dict[str, Any]):
        self.client = AIClient(ai_config)
        self.filter_config = filter_config

        self.classify_system, self.classify_user = load_prompt_template(
            filter_config.get("PROMPT_FILE", "prompt.txt"),
            config_subdir="ai_filter", label="AI筛选",
        )
        self.extract_system, self.extract_user = load_prompt_template(
            filter_config.get("EXTRACT_PROMPT_FILE", "extract_prompt.txt"),
            config_subdir="ai_filter", label="AI筛选",
        )
        self.update_tags_system, self.update_tags_user = load_prompt_template(
            filter_config.get("UPDATE_TAGS_PROMPT_FILE", "update_tags_prompt.txt"),
            config_subdir="ai_filter", label="AI筛选",
        )

    def compute_interests_hash(self, interests_content: str, filename: str = "ai_interests.txt") -> str:
        """计算兴趣描述的 hash，格式为 filename:md5，忽略空行和注释行"""
        lines = []
        for line in interests_content.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
        normalized = "\n".join(lines)
        content_hash = hashlib.md5(normalized.encode("utf-8")).hexdigest()
        return f"{filename}:{content_hash}"

    def load_interests_content(self) -> Optional[str]:
        """加载 config/ai_interests.txt 的内容"""
        config_dir = Path(__file__).parent.parent.parent / "config"
        interests_path = config_dir / "ai_interests.txt"
        if not interests_path.exists():
            return None
        content = interests_path.read_text(encoding="utf-8").strip()
        return content or None

    def extract_tags(self, interests_content: str) -> List[Dict[str, str]]:
        """阶段 A：从兴趣描述中提取结构化标签"""
        if not self.extract_user:
            return []

        user_prompt = self.extract_user.replace("{interests_content}", interests_content)
        messages = []
        if self.extract_system:
            messages.append({"role": "system", "content": self.extract_system})
        messages.append({"role": "user", "content": user_prompt})

        try:
            response = self.client.chat(messages)
        except Exception:
            return []

        return self._parse_tags_response(response)

    def update_tags(self, old_tags: List[Dict[str, Any]], interests_content: str) -> Optional[Dict[str, Any]]:
        """阶段 A'：对比旧标签集和新兴趣描述，给出更新方案"""
        if not self.update_tags_user:
            return None

        old_tags_json = json.dumps(
            [{"tag": t["tag"], "description": t.get("description", "")} for t in old_tags],
            ensure_ascii=False,
        )
        user_prompt = self.update_tags_user.replace(
            "{old_tags_json}", old_tags_json
        ).replace("{interests_content}", interests_content)

        messages = []
        if self.update_tags_system:
            messages.append({"role": "system", "content": self.update_tags_system})
        messages.append({"role": "user", "content": user_prompt})

        try:
            response = self.client.chat(messages)
        except Exception:
            return None

        json_str = self._extract_json(response)
        if not json_str:
            return None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict) or "keep" not in data:
            return None

        return {
            "keep": [t for t in data.get("keep", []) if isinstance(t, dict) and t.get("tag")],
            "add": [t for t in data.get("add", []) if isinstance(t, dict) and t.get("tag")],
            "remove": [t for t in data.get("remove", []) if isinstance(t, str)],
            "change_ratio": float(data.get("change_ratio", 0.0)),
        }

    def classify_batch(
        self,
        titles: List[Dict[str, Any]],
        tags: List[Dict[str, Any]],
        interests_content: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """阶段 B：批量分类新闻标题，失败返回 None

        Args:
            titles: [{"id": 批次内序号（1-based）, "title": 标题文本}, ...]
            tags: 候选标签列表（来自 AIFilterStore 的 active_tags，含真实 DB id）

        Returns:
            [{"title", "tag", "tag_id"（真实 DB id）, "relevance_score"}, ...] 或 None（失败）
        """
        if not self.classify_user or not titles or not tags:
            return None

        tags_list = "\n".join(
            f"{idx}. {t['tag']}: {t.get('description', '')}"
            for idx, t in enumerate(tags, start=1)
        )
        news_list = "\n".join(
            f"{item['id']}. {item['title']}" for item in titles
        )
        user_prompt = (
            self.classify_user.replace("{interests_content}", interests_content)
            .replace("{tags_list}", tags_list)
            .replace("{news_count}", str(len(titles)))
            .replace("{news_list}", news_list)
        )

        messages = []
        if self.classify_system:
            messages.append({"role": "system", "content": self.classify_system})
        messages.append({"role": "user", "content": user_prompt})

        try:
            response = self.client.chat(messages)
        except Exception:
            return None

        json_str = self._extract_json(response)
        if not json_str:
            return None

        try:
            raw_results = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        if not isinstance(raw_results, list):
            return None

        id_to_title = {item["id"]: item for item in titles}
        # tag_id 这里指的是 LLM 在 tags_list 里看到的 1-based 序号，不是数据库真实 id
        id_to_tag = {idx: t for idx, t in enumerate(tags, start=1)}

        results = []
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            news_id = entry.get("id")
            llm_tag_ref = entry.get("tag_id")
            if news_id not in id_to_title or llm_tag_ref not in id_to_tag:
                continue
            title_data = id_to_title[news_id]
            tag_data = id_to_tag[llm_tag_ref]
            results.append({
                "title": title_data["title"],
                "tag": tag_data["tag"],
                "tag_id": tag_data.get("id"),  # 换成真实的 DB id
                "relevance_score": float(entry.get("score", 0.0)),
            })

        return results

    @staticmethod
    def _extract_json(response: str) -> str:
        """从 AI 响应中提取 JSON 内容（去掉 markdown code fence）"""
        if not response:
            return ""
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", response, re.DOTALL)
        if match:
            return match.group(1).strip()
        return response.strip()

    def _parse_tags_response(self, response: str) -> List[Dict[str, str]]:
        json_str = self._extract_json(response)
        if not json_str:
            return []
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return []

        raw_tags = data.get("tags", []) if isinstance(data, dict) else []
        tags = []
        for t in raw_tags:
            if isinstance(t, dict) and t.get("tag"):
                tags.append({
                    "tag": str(t["tag"]).strip(),
                    "description": str(t.get("description", "")).strip(),
                })
        return tags
```

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add trendradar/ai/filter.py tests/test_ai_filter.py
git commit -m "feat: add AIFilter (tag extraction, tag update, batch classification)"
```

---

### Task 8: AIFilterPipeline（编排完整流程）

**Files:**
- Create: `trendradar/ai/filter_pipeline.py`
- Test: `tests/test_ai_filter_pipeline.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_ai_filter_pipeline.py`:

```python
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
```

- [ ] **Step 2: 确认测试失败**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_pipeline.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'trendradar.ai.filter_pipeline'`

- [ ] **Step 3: 实现**

`trendradar/ai/filter_pipeline.py`:

```python
# coding=utf-8
"""AI 筛选流水线：编排标签提取/更新、批量分类、结果查询的完整流程"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from trendradar.ai.filter import AIFilter
from trendradar.storage.ai_filter_store import AIFilterStore, title_hash


@dataclass
class AIFilterResult:
    """AI 筛选结果，跟 main.py::count_word_frequency() 的 stats 结构兼容"""
    stats: List[Dict[str, Any]] = field(default_factory=list)
    total_matched: int = 0
    total_processed: int = 0
    success: bool = False
    error: str = ""


class AIFilterPipeline:
    """AI 筛选流水线"""

    def __init__(
        self,
        ai_config: Dict[str, Any],
        filter_config: Dict[str, Any],
        store: AIFilterStore,
        ai_filter: Optional[AIFilter] = None,
    ):
        self.filter_config = filter_config
        self.store = store
        self.ai_filter = ai_filter or AIFilter(ai_config, filter_config)
        self.interests_file = "ai_interests.txt"

    def run(self, all_titles: List[Dict[str, Any]]) -> AIFilterResult:
        """执行完整的 AI 筛选流程

        Args:
            all_titles: 当天全部新闻标题（去重后的平铺列表），每条至少包含
                "title" 字段，其余字段（source_name/url/mobile_url/ranks/
                rank_threshold/count/is_new/time_display）会原样透传到输出，
                额外挂上一个 "category" 字段。
        """
        interests_content = self.ai_filter.load_interests_content()
        if not interests_content:
            return AIFilterResult(success=False, error="兴趣描述文件为空或不存在")

        current_hash = self.ai_filter.compute_interests_hash(interests_content, self.interests_file)
        stored_hash = self.store.get_latest_prompt_hash(self.interests_file)

        if stored_hash != current_hash:
            error = self._handle_tag_update(interests_content, current_hash, stored_hash)
            if error:
                return AIFilterResult(success=False, error=error)

        active_tags = self.store.get_active_ai_filter_tags(self.interests_file)
        if not active_tags:
            return AIFilterResult(success=False, error="没有可用的标签")

        analyzed_hashes = self.store.get_analyzed_title_hashes(self.interests_file)
        pending = [t for t in all_titles if title_hash(t["title"]) not in analyzed_hashes]

        self._classify_and_save(pending, active_tags, interests_content, current_hash)

        raw_results = self.store.get_active_ai_filter_results(self.interests_file)
        return self._build_result(raw_results, all_titles, len(all_titles))

    def _handle_tag_update(
        self, interests_content: str, current_hash: str, stored_hash: Optional[str]
    ) -> Optional[str]:
        """处理标签提取/更新，返回错误信息（None 表示成功）"""
        new_version = self.store.get_latest_ai_filter_tag_version(self.interests_file) + 1
        threshold = self.filter_config.get("RECLASSIFY_THRESHOLD", 0.6)

        if stored_hash is None:
            tags_data = self.ai_filter.extract_tags(interests_content)
            if not tags_data:
                return "标签提取失败"
            self._save_new_tags(tags_data, new_version, current_hash)
            return None

        old_tags = self.store.get_active_ai_filter_tags(self.interests_file)
        update_result = self.ai_filter.update_tags(old_tags, interests_content)

        if update_result is None or update_result["change_ratio"] >= threshold:
            tags_data = self.ai_filter.extract_tags(interests_content)
            if not tags_data:
                return "标签提取失败"
            self.store.deprecate_all_ai_filter_tags(self.interests_file)
            self._save_new_tags(tags_data, new_version, current_hash)
            return None

        self._apply_incremental_update(old_tags, update_result, new_version, current_hash)
        return None

    def _save_new_tags(self, tags_data: List[Dict[str, Any]], version: int, prompt_hash: str) -> None:
        with_priority = [{**t, "priority": idx} for idx, t in enumerate(tags_data, start=1)]
        self.store.save_ai_filter_tags(with_priority, version, prompt_hash, self.interests_file)

    def _apply_incremental_update(
        self,
        old_tags: List[Dict[str, Any]],
        update_result: Dict[str, Any],
        new_version: int,
        current_hash: str,
    ) -> None:
        keep_tags = update_result["keep"]
        add_tags = update_result["add"]
        remove_tags = update_result["remove"]

        if remove_tags:
            remove_set = set(remove_tags)
            removed_ids = [t["id"] for t in old_tags if t["tag"] in remove_set]
            if removed_ids:
                self.store.deprecate_specific_ai_filter_tags(removed_ids)

        keep_with_priority: List[Dict[str, Any]] = []
        if keep_tags:
            self.store.update_ai_filter_tag_descriptions(keep_tags, self.interests_file)
            keep_with_priority = [{**t, "priority": idx} for idx, t in enumerate(keep_tags, start=1)]
            self.store.update_ai_filter_tag_priorities(keep_with_priority, self.interests_file)

        if add_tags:
            start = keep_with_priority[-1]["priority"] + 1 if keep_with_priority else 1
            add_with_priority = [{**t, "priority": start + idx} for idx, t in enumerate(add_tags)]
            self.store.save_ai_filter_tags(add_with_priority, new_version, current_hash, self.interests_file)
            self.store.clear_unmatched_analyzed_news(self.interests_file)

    def _classify_and_save(
        self,
        pending: List[Dict[str, Any]],
        active_tags: List[Dict[str, Any]],
        interests_content: str,
        current_hash: str,
    ) -> None:
        batch_size = self.filter_config.get("BATCH_SIZE", 200)
        batch_interval = self.filter_config.get("BATCH_INTERVAL", 2)

        for i in range(0, len(pending), batch_size):
            if i > 0 and batch_interval > 0:
                time.sleep(batch_interval)

            batch = pending[i : i + batch_size]
            titles_for_ai = [{"id": idx, "title": t["title"]} for idx, t in enumerate(batch, start=1)]
            batch_results = self.ai_filter.classify_batch(titles_for_ai, active_tags, interests_content)

            batch_hashes = [title_hash(t["title"]) for t in batch]
            if batch_results is None:
                continue

            title_to_hash = {t["title"]: title_hash(t["title"]) for t in batch}
            matched_titles = {r["title"] for r in batch_results}
            matched_hashes = {title_to_hash[t] for t in matched_titles if t in title_to_hash}

            db_results = [
                {
                    "title_hash": title_to_hash[r["title"]],
                    "tag_id": r["tag_id"],
                    "relevance_score": r["relevance_score"],
                }
                for r in batch_results
                if r["title"] in title_to_hash
            ]
            if db_results:
                self.store.save_ai_filter_results(db_results)

            self.store.save_analyzed_titles(batch_hashes, self.interests_file, current_hash, matched_hashes)

    def _build_result(
        self,
        raw_results: List[Dict[str, Any]],
        all_titles: List[Dict[str, Any]],
        total_processed: int,
    ) -> AIFilterResult:
        """把 DB 查询到的分类结果跟传入的今日标题元数据 join 起来，
        转换成跟 main.py::count_word_frequency() 兼容的 stats 结构"""
        hash_to_title = {title_hash(t["title"]): t for t in all_titles}
        min_score = self.filter_config.get("MIN_SCORE", 0.0)

        tag_groups: Dict[str, Dict[str, Any]] = {}
        for r in raw_results:
            if r["relevance_score"] < min_score:
                continue
            title_data = hash_to_title.get(r["title_hash"])
            if title_data is None:
                continue

            tag_name = r["tag"]
            if tag_name not in tag_groups:
                tag_groups[tag_name] = {
                    "word": tag_name,
                    "count": 0,
                    "position": r.get("tag_priority", 9999),
                    "titles": [],
                }

            entry = dict(title_data)
            entry["category"] = tag_name
            tag_groups[tag_name]["titles"].append(entry)
            tag_groups[tag_name]["count"] += 1

        stats = sorted(tag_groups.values(), key=lambda g: g["position"])
        total_matched = sum(g["count"] for g in stats)

        return AIFilterResult(
            stats=stats,
            total_matched=total_matched,
            total_processed=total_processed,
            success=True,
        )
```

- [ ] **Step 4: 确认测试通过**

Run: `env -u PYTHONPATH uv run pytest tests/test_ai_filter_pipeline.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add trendradar/ai/filter_pipeline.py tests/test_ai_filter_pipeline.py
git commit -m "feat: add AIFilterPipeline orchestrating the full classification flow"
```

---

### Task 9: 全量验证

**Files:** 无新增/修改，只跑检查

- [ ] **Step 1: 跑完整测试套件**

Run: `env -u PYTHONPATH uv run pytest tests/ -q`
Expected: 全部 passed（新增约 45 个测试：4 + 4 + 7 + 6 + 14 + 13 + 7 = 55，实际以运行结果为准），覆盖率不低于之前的基线（86%）；`test_records.py::test_normalize_time` 如果因为跑的时间点在 18:00 之后而失败，是已知的、跟本次改动无关的预存在问题（时间窗口相关，不是本计划引入的）

- [ ] **Step 2: mypy 检查新增代码**

Run: `env -u PYTHONPATH uv run mypy trendradar/ai/ trendradar/storage/ trendradar/config.py --ignore-missing-imports`
Expected: 无新增错误（如果 litellm 没有类型存根导致 import 报错，属于已知的第三方库限制，不需要修复）

- [ ] **Step 3: 手动验证一次完整的分类流程（用假的 AIFilter，不花真实 API 费用）**

Run:
```bash
env -u PYTHONPATH uv run python3 -c "
from unittest.mock import MagicMock
from trendradar.ai.filter_pipeline import AIFilterPipeline
from trendradar.storage.ai_filter_store import AIFilterStore

import tempfile, os
db = tempfile.mktemp(suffix='.sqlite3')
store = AIFilterStore(db)

mock_filter = MagicMock()
mock_filter.load_interests_content.return_value = '我关注科技'
mock_filter.compute_interests_hash.return_value = 'ai_interests.txt:h1'
mock_filter.extract_tags.return_value = [{'tag': '科技', 'description': '科技相关'}]
mock_filter.classify_batch.side_effect = lambda titles, tags, interests: [
    {'title': t['title'], 'tag': '科技', 'tag_id': tags[0]['id'], 'relevance_score': 0.9}
    for t in titles
]

pipeline = AIFilterPipeline(
    ai_config={}, filter_config={'BATCH_SIZE': 200, 'BATCH_INTERVAL': 0, 'MIN_SCORE': 0.0, 'RECLASSIFY_THRESHOLD': 0.6},
    store=store, ai_filter=mock_filter,
)
result = pipeline.run([{'title': '测试新闻标题', 'source_name': '', 'url': '', 'mobile_url': '',
                         'ranks': [1], 'rank_threshold': 5, 'count': 1, 'is_new': False, 'time_display': ''}])
print('success:', result.success)
print('stats:', result.stats)
os.remove(db)
"
```
Expected: `success: True`，`stats` 里能看到一个 `word=科技` 的分组，里面的新闻带着 `category: 科技`

- [ ] **Step 4: 确认没有遗留未提交的改动**

Run: `git status --short`
Expected: 干净（除了可能的 `data/` 空目录，已在 .gitignore 里）

---

## 完成后

全部 9 个 Task 完成后，AI 分类引擎本身就绪、有完整测试覆盖、没有接入任何现有代码路径。子项目 2（接入 main.py 爬取管线，让 `filter.method: ai` 真正生效）和子项目 3（Web UI 分类 Tab）会在各自的 spec/plan 里继续。

按照 subagent-driven-development 流程，全部 Task 完成后使用 superpowers:finishing-a-development-branch 收尾。
