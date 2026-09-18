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
