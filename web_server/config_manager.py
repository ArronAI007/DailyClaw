"""
配置管理模块

提供配置的读取、验证、保存功能。
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from trendradar.logging_config import get_logger

logger = get_logger(__name__)


class ConfigManager:
    """配置管理器"""

    def __init__(self, project_root: Optional[str] = None):
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent

        self.config_path = self.project_root / "config" / "config.yaml"
        self.frequency_words_path = self.project_root / "config" / "frequency_words.txt"

    def load_config(self) -> Dict[str, Any]:
        """加载完整配置"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def load_frequency_words(self) -> str:
        """加载频率词文件内容"""
        if not self.frequency_words_path.exists():
            return ""

        with open(self.frequency_words_path, "r", encoding="utf-8") as f:
            return f.read()

    def save_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        保存配置到文件

        Args:
            config_data: 配置数据字典

        Returns:
            操作结果
        """
        try:
            # 验证 YAML 可序列化
            yaml_str = yaml.safe_dump(config_data, allow_unicode=True, sort_keys=False)

            # 备份旧配置
            self._backup_config()

            # 写入新配置
            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(yaml_str)

            logger.info(f"配置已保存: {self.config_path}")

            return {
                "success": True,
                "message": "配置保存成功",
                "path": str(self.config_path)
            }

        except Exception as e:
            logger.exception(f"保存配置失败: {e}")
            return {
                "success": False,
                "message": f"保存失败: {str(e)}"
            }

    def save_frequency_words(self, content: str) -> Dict[str, Any]:
        """保存频率词文件"""
        try:
            with open(self.frequency_words_path, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "success": True,
                "message": "频率词保存成功"
            }
        except Exception as e:
            logger.exception(f"保存频率词失败: {e}")
            return {
                "success": False,
                "message": f"保存失败: {str(e)}"
            }

    def _backup_config(self) -> None:
        """备份当前配置文件"""
        if self.config_path.exists():
            backup_dir = self.project_root / "config" / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = backup_dir / f"config_{timestamp}.yaml"

            shutil.copy2(self.config_path, backup_path)
            logger.info(f"配置已备份: {backup_path}")

    def validate_config(self, config_data: Dict[str, Any]) -> List[str]:
        """
        验证配置数据

        Returns:
            错误信息列表，为空表示验证通过
        """
        errors = []

        # 检查必要字段
        if "app" not in config_data:
            errors.append("缺少 app 配置节")

        if "crawler" not in config_data:
            errors.append("缺少 crawler 配置节")
        else:
            crawler = config_data["crawler"]
            if "request_interval" not in crawler:
                errors.append("crawler.request_interval 必填")

        if "report" not in config_data:
            errors.append("缺少 report 配置节")
        else:
            report = config_data["report"]
            mode = report.get("mode", "")
            if mode not in ["daily", "incremental", "current"]:
                errors.append(f'report.mode 必须是 daily/incremental/current 之一，当前: {mode}')

        if "platforms" not in config_data or not config_data["platforms"]:
            errors.append("platforms 列表不能为空")
        else:
            for i, platform in enumerate(config_data["platforms"]):
                if "id" not in platform:
                    errors.append(f"platforms[{i}] 缺少 id 字段")
                if "name" not in platform:
                    errors.append(f"platforms[{i}] 缺少 name 字段")

        if "weight" not in config_data:
            errors.append("缺少 weight 配置节")
        else:
            weight = config_data["weight"]
            total = weight.get("rank_weight", 0) + weight.get("frequency_weight", 0) + weight.get("hotness_weight", 0)
            if abs(total - 1.0) > 0.01:
                errors.append(f"权重之和必须等于 1，当前: {total}")

        return errors

    def get_config_for_form(self) -> Dict[str, Any]:
        """
        获取适合表单编辑的配置格式

        Returns:
            扁平化的配置字典
        """
        config = self.load_config()

        return {
            "version_check_url": config.get("app", {}).get("version_check_url", ""),
            "show_version_update": config.get("app", {}).get("show_version_update", True),
            "request_interval": config.get("crawler", {}).get("request_interval", 1000),
            "max_workers": config.get("crawler", {}).get("max_workers", 5),
            "enable_crawler": config.get("crawler", {}).get("enable_crawler", True),
            "use_proxy": config.get("crawler", {}).get("use_proxy", False),
            "default_proxy": config.get("crawler", {}).get("default_proxy", ""),
            "report_mode": config.get("report", {}).get("mode", "daily"),
            "rank_threshold": config.get("report", {}).get("rank_threshold", 5),
            "sort_by_position_first": config.get("report", {}).get("sort_by_position_first", False),
            "max_news_per_keyword": config.get("report", {}).get("max_news_per_keyword", 0),
            "cards_per_batch": config.get("report", {}).get("cards_per_batch", 12),
            "reverse_content_order": config.get("report", {}).get("reverse_content_order", False),
            "enable_notification": config.get("notification", {}).get("enable_notification", True),
            "message_batch_size": config.get("notification", {}).get("message_batch_size", 4000),
            "batch_send_interval": config.get("notification", {}).get("batch_send_interval", 3),
            "max_accounts_per_channel": config.get("notification", {}).get("max_accounts_per_channel", 3),
            "push_window_enabled": config.get("notification", {}).get("push_window", {}).get("enabled", False),
            "push_window_start": config.get("notification", {}).get("push_window", {}).get("time_range", {}).get("start", "08:00"),
            "push_window_end": config.get("notification", {}).get("push_window", {}).get("time_range", {}).get("end", "22:00"),
            "push_window_once_per_day": config.get("notification", {}).get("push_window", {}).get("once_per_day", True),
            "rank_weight": config.get("weight", {}).get("rank_weight", 0.6),
            "frequency_weight": config.get("weight", {}).get("frequency_weight", 0.3),
            "hotness_weight": config.get("weight", {}).get("hotness_weight", 0.1),
            "platforms": config.get("platforms", []),
            "webhooks": config.get("notification", {}).get("webhooks", {}),
        }

    def save_config_from_form(self, form_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        从表单数据保存配置

        Args:
            form_data: 表单提交的数据

        Returns:
            操作结果
        """
        try:
            # 构建标准配置结构
            config = {
                "app": {
                    "version_check_url": form_data.get("version_check_url", ""),
                    "show_version_update": form_data.get("show_version_update", True),
                },
                "crawler": {
                    "request_interval": int(form_data.get("request_interval", 1000)),
                    "max_workers": int(form_data.get("max_workers", 5)),
                    "enable_crawler": form_data.get("enable_crawler", True),
                    "use_proxy": form_data.get("use_proxy", False),
                    "default_proxy": form_data.get("default_proxy", ""),
                },
                "report": {
                    "mode": form_data.get("report_mode", "daily"),
                    "rank_threshold": int(form_data.get("rank_threshold", 5)),
                    "sort_by_position_first": form_data.get("sort_by_position_first", False),
                    "max_news_per_keyword": int(form_data.get("max_news_per_keyword", 0)),
                    "cards_per_batch": int(form_data.get("cards_per_batch", 12)),
                    "reverse_content_order": form_data.get("reverse_content_order", False),
                },
                "notification": {
                    "enable_notification": form_data.get("enable_notification", True),
                    "message_batch_size": int(form_data.get("message_batch_size", 4000)),
                    "batch_send_interval": int(form_data.get("batch_send_interval", 3)),
                    "max_accounts_per_channel": int(form_data.get("max_accounts_per_channel", 3)),
                    "push_window": {
                        "enabled": form_data.get("push_window_enabled", False),
                        "time_range": {
                            "start": form_data.get("push_window_start", "08:00"),
                            "end": form_data.get("push_window_end", "22:00"),
                        },
                        "once_per_day": form_data.get("push_window_once_per_day", True),
                    },
                    "webhooks": form_data.get("webhooks", {}),
                },
                "weight": {
                    "rank_weight": float(form_data.get("rank_weight", 0.6)),
                    "frequency_weight": float(form_data.get("frequency_weight", 0.3)),
                    "hotness_weight": float(form_data.get("hotness_weight", 0.1)),
                },
                "platforms": form_data.get("platforms", []),
            }

            # 验证
            errors = self.validate_config(config)
            if errors:
                return {
                    "success": False,
                    "message": "配置验证失败",
                    "errors": errors
                }

            return self.save_config(config)

        except Exception as e:
            logger.exception(f"从表单保存配置失败: {e}")
            return {
                "success": False,
                "message": f"保存失败: {str(e)}"
            }
