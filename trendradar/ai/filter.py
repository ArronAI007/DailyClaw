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
