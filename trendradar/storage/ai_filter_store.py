# coding=utf-8
"""AI 筛选结果的 SQLite 存储层

管理三张表：
- ai_filter_tags: 标签版本管理（从兴趣描述提取出的分类标签，按 interests_file 隔离）
- ai_filter_results: 新闻 × 标签 的分类结果
- ai_filter_analyzed_news: 已分析过的新闻标题记录（按 title_hash 去重，避免重复消耗 token）

用 title_hash（标题的 md5）代替上游 TrendRadar 的 news_item_id 外键，因为
DailyClaw 没有一个全量新闻 SQLite 表——新闻身份从头到尾都是标题文本本身。

注意：废弃一个标签是软删除（标记 status='deprecated'），而非硬删除行。
该标签的已保存结果仍留在 ai_filter_results 表中，但在 get_active_ai_filter_results
中通过 JOIN 的 status='active' 条件被过滤掉，不是真正删除。
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
            conn.executemany(
                "UPDATE ai_filter_tags SET priority = ? "
                "WHERE tag = ? AND interests_file = ? AND status = 'active'",
                [(t["priority"], t["tag"], interests_file) for t in tags],
            )

    def update_ai_filter_tag_descriptions(
        self, tags: List[Dict[str, Any]], interests_file: str = "ai_interests.txt"
    ) -> None:
        with self._connect() as conn:
            conn.executemany(
                "UPDATE ai_filter_tags SET description = ? "
                "WHERE tag = ? AND interests_file = ? AND status = 'active'",
                [(t.get("description", ""), t["tag"], interests_file) for t in tags],
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
