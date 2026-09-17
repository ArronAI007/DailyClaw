"""
DailyClaw Web Server

基于 FastAPI 的 Web 控制界面，支持：
- Dashboard 概览
- 报告查看
- 配置管理
- 手动触发爬取
"""

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from mcp_server.services.data_service import DataService
from mcp_server.tools.system import SystemManagementTools
from trendradar.logging_config import get_logger
from trendradar.config import VERSION
from trendradar.utils import get_beijing_time
from web_server.config_manager import ConfigManager

logger = get_logger(__name__)

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 创建 FastAPI 应用
app = FastAPI(
    title="DailyClaw Web UI",
    description="DailyClaw 热点新闻聚合 Web 控制界面",
    version=VERSION,
)

# 静态文件
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "web_server" / "static")), name="static")

# output 目录可能不存在，先创建
output_dir = PROJECT_ROOT / "output"
output_dir.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=str(output_dir)), name="output")

# 模板
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "web_server" / "templates"))

# 服务实例
_data_service: Optional[DataService] = None
_system_tools: Optional[SystemManagementTools] = None
_config_manager: Optional[ConfigManager] = None


def get_data_service() -> DataService:
    global _data_service
    if _data_service is None:
        _data_service = DataService(str(PROJECT_ROOT))
    return _data_service


def get_system_tools() -> SystemManagementTools:
    global _system_tools
    if _system_tools is None:
        _system_tools = SystemManagementTools(str(PROJECT_ROOT))
    return _system_tools


def get_config_manager() -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(str(PROJECT_ROOT))
    return _config_manager


# ============== 辅助函数 ==============

def parse_date_folder_name(folder_name: str) -> Optional[datetime]:
    """解析日期文件夹名称，如 '2025年05月06日'"""
    match = re.match(r'(\d{4})年(\d{2})月(\d{2})日', folder_name)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return None


def format_time_label(filename_stem: str) -> str:
    """将 '23时46分' 这样的文件名转换为更易读的 '23:46'"""
    match = re.match(r'(\d{2})时(\d{2})分', filename_stem)
    if match:
        return f"{match.group(1)}:{match.group(2)}"
    return filename_stem


def get_report_list() -> List[Dict[str, Any]]:
    """获取所有可用报告列表"""
    reports = []
    output_dir = PROJECT_ROOT / "output"

    if not output_dir.exists():
        return reports

    for date_folder in sorted(output_dir.iterdir(), reverse=True):
        if not date_folder.is_dir() or date_folder.name.startswith('.'):
            continue

        date_obj = parse_date_folder_name(date_folder.name)
        if not date_obj:
            continue

        html_dir = date_folder / "html"
        if html_dir.exists():
            for html_file in sorted(html_dir.iterdir(), reverse=True):
                if html_file.suffix == ".html":
                    txt_file = date_folder / "txt" / f"{html_file.stem}.txt"
                    reports.append({
                        "date": date_folder.name,
                        "date_obj": date_obj,
                        "filename": html_file.name,
                        "time_label": format_time_label(html_file.stem),
                        "path": f"/output/{date_folder.name}/html/{html_file.name}",
                        "txt_path": txt_file,
                        "size": html_file.stat().st_size,
                    })

    return reports


def parse_news_txt(txt_path: Path) -> List[Dict[str, Any]]:
    """解析文本报告，按平台分组提取新闻标题与链接"""
    groups: List[Dict[str, Any]] = []

    if not txt_path.exists():
        return groups

    header_pattern = re.compile(r'^([^\s|]+)\s*\|\s*(.+)$')
    item_pattern = re.compile(r'^\d+\.\s*(.+?)\s*\[URL:([^\]]*)\]')

    current: Optional[Dict[str, Any]] = None
    for raw_line in txt_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        item_match = item_pattern.match(line) if current is not None else None
        if item_match:
            current["news_items"].append({
                "title": item_match.group(1).strip(),
                "url": item_match.group(2).strip(),
            })
            continue

        header_match = header_pattern.match(line)
        if header_match:
            current = {
                "platform_id": header_match.group(1).strip(),
                "platform_name": header_match.group(2).strip(),
                "news_items": [],
            }
            groups.append(current)

    return groups


def get_latest_report_path() -> Optional[str]:
    """获取最新报告的路径"""
    reports = get_report_list()
    if reports:
        return reports[0]["path"]
    return None


def get_platform_status() -> List[Dict[str, Any]]:
    """获取平台状态列表"""
    try:
        config_mgr = get_config_manager()
        config = config_mgr.load_config()
        platforms = config.get("platforms", [])

        # 尝试获取最近一次采集的数据来显示哪些平台有数据
        # 用最近一次报告而非严格的"今天"，避免跨天后短暂显示全部离线
        try:
            latest_reports = get_report_list()
            if latest_reports:
                latest_groups = parse_news_txt(latest_reports[0]["txt_path"])
                active_platforms = set(g["platform_id"] for g in latest_groups)
            else:
                active_platforms = set()
        except Exception:
            active_platforms = set()

        result = []
        for p in platforms:
            result.append({
                "id": p["id"],
                "name": p["name"],
                "active": p["id"] in active_platforms
            })

        return result
    except Exception as e:
        logger.exception(f"获取平台状态失败: {e}")
        return []


def get_trending_topics_from_latest_report(top_n: int = 5) -> List[Dict[str, Any]]:
    """基于最近一次采集报告统计个人关注词出现频率

    不依赖严格的"今天"日期过滤（与 DataService.get_trending_topics 不同），
    避免跨天后、当天还没有新采集数据时趋势话题错误地显示为空
    """
    latest_reports = get_report_list()
    if not latest_reports:
        return []

    groups = parse_news_txt(latest_reports[0]["txt_path"])
    titles = [item["title"] for g in groups for item in g["news_items"]]
    if not titles:
        return []

    word_groups = get_data_service().parser.parse_frequency_words()

    word_frequency: Counter = Counter()
    keyword_to_news: Dict[str, List[str]] = {}

    for title in titles:
        for group in word_groups:
            all_words = group.get("required", []) + group.get("normal", [])
            for word in all_words:
                if word and word in title:
                    word_frequency[word] += 1
                    keyword_to_news.setdefault(word, []).append(title)

    top_keywords = word_frequency.most_common(top_n)
    all_frequencies = list(word_frequency.values())
    avg_frequency = sum(all_frequencies) / len(all_frequencies) if all_frequencies else 1
    max_frequency = top_keywords[0][1] if top_keywords else 1

    topics = []
    for keyword, frequency in top_keywords:
        if frequency > avg_frequency * 1.3:
            trend = "rising"
        elif frequency < avg_frequency * 0.7:
            trend = "falling"
        else:
            trend = "stable"

        topics.append({
            "keyword": keyword,
            "frequency": frequency,
            "matched_news": len(set(keyword_to_news.get(keyword, []))),
            "trend": trend,
            "weight_score": round(frequency / max_frequency, 2) if max_frequency else 0.0,
        })

    return topics


# 阅读记录：记录用户在 Web UI 中点开过的新闻，供概览页展示
READ_HISTORY_FILE = PROJECT_ROOT / "output" / ".read_history" / "read_history.json"
READ_HISTORY_MAX = 50


def load_read_history() -> List[Dict[str, Any]]:
    """加载阅读记录（最新在前）"""
    if not READ_HISTORY_FILE.exists():
        return []
    try:
        with open(READ_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.exception(f"读取阅读记录失败: {e}")
        return []


def format_relative_time(iso_str: str) -> str:
    """将 ISO 时间字符串转换为『X 分钟前』这样的相对时间"""
    try:
        read_at = datetime.fromisoformat(iso_str)
        delta = get_beijing_time() - read_at
        seconds = int(delta.total_seconds())
    except Exception:
        return ""

    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    return f"{days} 天前"


def add_read_history(title: str, url: str, platform: str) -> None:
    """新增一条阅读记录，按 url 去重并置顶，超出上限时裁剪"""
    if not url or not url.lower().startswith(("http://", "https://")):
        # 只接受 http(s) 链接，避免 javascript: 等协议的链接被存入后
        # 在概览页渲染成可点击的超链接，造成存储型 XSS
        return

    history = load_read_history()
    history = [h for h in history if h.get("url") != url]
    history.insert(0, {
        "title": title.strip() if title else url,
        "url": url,
        "platform": platform.strip() if platform else "",
        "read_at": get_beijing_time().isoformat(),
    })
    history = history[:READ_HISTORY_MAX]

    try:
        READ_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(READ_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception(f"保存阅读记录失败: {e}")


# ============== 页面路由 ==============

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard 概览页"""
    try:
        system_tools = get_system_tools()
        status = system_tools.get_system_status()
    except Exception as e:
        logger.exception(f"获取系统状态失败: {e}")
        status = {"system": {"version": VERSION}, "data": {}, "health": "unknown"}

    # 获取最新新闻统计：取最近一次采集报告的实际条数，
    # 而不是严格按自然日"今天"过滤——避免刚过零点、
    # 当天还没有新采集数据时统计错误地显示为 0
    try:
        latest_reports = get_report_list()
        if latest_reports:
            latest_groups = parse_news_txt(latest_reports[0]["txt_path"])
            total_news_today = sum(len(g["news_items"]) for g in latest_groups)
        else:
            total_news_today = 0
    except Exception as e:
        logger.exception(f"获取最新新闻统计失败: {e}")
        total_news_today = 0

    # 获取趋势话题（基于最近一次采集报告，同样不卡"今天"这个硬边界）
    try:
        trending_topics = get_trending_topics_from_latest_report(top_n=5)
    except Exception as e:
        logger.exception(f"获取趋势话题失败: {e}")
        trending_topics = []

    # 平台状态
    platforms = get_platform_status()

    # 最近阅读过的新闻
    read_history = load_read_history()[:8]
    for item in read_history:
        item["time_ago"] = format_relative_time(item.get("read_at", ""))

    return templates.TemplateResponse(request, "dashboard.html", {
        "version": VERSION,
        "status": status,
        "total_news_today": total_news_today,
        "trending_topics": trending_topics,
        "platforms": platforms,
        "read_history": read_history,
    })


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    """报告列表页"""
    reports = get_report_list()

    # 解析每份报告的文本内容，提取新闻标题与链接
    for r in reports:
        try:
            r["groups"] = parse_news_txt(r["txt_path"])
            r["total_items"] = sum(len(g["news_items"]) for g in r["groups"])
        except Exception as e:
            logger.exception(f"解析报告内容失败: {r['txt_path']}: {e}")
            r["groups"] = []
            r["total_items"] = 0

    # 按日期分组
    grouped = {}
    for r in reports:
        date = r["date"]
        if date not in grouped:
            grouped[date] = []
        grouped[date].append(r)

    return templates.TemplateResponse(request, "reports.html", {
        "grouped_reports": grouped,
        "total_reports": len(reports),
    })


@app.get("/reports/view", response_class=HTMLResponse)
async def view_report(request: Request, path: str):
    """查看报告（iframe 嵌入）"""
    return templates.TemplateResponse(request, "report_view.html", {
        "report_path": path,
    })


@app.get("/reports/latest")
async def latest_report():
    """重定向到最新报告"""
    path = get_latest_report_path()
    if path:
        return RedirectResponse(url=f"/reports/view?path={path}")
    raise HTTPException(status_code=404, detail="暂无报告")


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """配置管理页"""
    config_mgr = get_config_manager()

    try:
        form_config = config_mgr.get_config_for_form()
        frequency_words = config_mgr.load_frequency_words()
    except Exception as e:
        logger.exception(f"加载配置失败: {e}")
        form_config = {}
        frequency_words = ""

    return templates.TemplateResponse(request, "config.html", {
        "config": form_config,
        "frequency_words": frequency_words,
    })


# ============== API 路由 ==============

class CrawlRequest(BaseModel):
    platforms: Optional[List[str]] = None
    save_to_local: bool = True


@app.post("/api/crawl")
async def api_crawl(request: CrawlRequest):
    """手动触发爬取"""
    system_tools = get_system_tools()
    result = system_tools.trigger_crawl(
        platforms=request.platforms,
        save_to_local=request.save_to_local
    )
    return result


@app.get("/api/status")
async def api_status():
    """获取系统状态"""
    system_tools = get_system_tools()
    return system_tools.get_system_status()


@app.get("/api/news/latest")
async def api_latest_news(limit: int = 50):
    """获取最新新闻"""
    data_service = get_data_service()
    return data_service.get_latest_news(limit=limit)


class ReadHistoryRequest(BaseModel):
    title: str
    url: str
    platform: str = ""


@app.post("/api/read-history")
async def api_add_read_history(request: ReadHistoryRequest):
    """记录一条被点击阅读的新闻，供概览页展示"""
    add_read_history(request.title, request.url, request.platform)
    return {"success": True}


@app.get("/api/trending")
async def api_trending(top_n: int = 10, mode: str = "daily"):
    """获取趋势话题"""
    data_service = get_data_service()
    return data_service.get_trending_topics(top_n=top_n, mode=mode)


@app.get("/api/platforms")
async def api_platforms():
    """获取平台列表"""
    return {"platforms": get_platform_status()}


@app.post("/api/config/save")
async def api_save_config(request: Request):
    """保存配置"""
    try:
        data = await request.json()
        config_mgr = get_config_manager()
        result = config_mgr.save_config_from_form(data)
        return result
    except Exception as e:
        logger.exception(f"API 保存配置失败: {e}")
        return {"success": False, "message": f"保存失败: {str(e)}"}


@app.post("/api/config/save-frequency-words")
async def api_save_frequency_words(request: Request):
    """保存频率词"""
    try:
        data = await request.json()
        content = data.get("content", "")
        config_mgr = get_config_manager()
        return config_mgr.save_frequency_words(content)
    except Exception as e:
        logger.exception(f"保存频率词失败: {e}")
        return {"success": False, "message": f"保存失败: {str(e)}"}


# ============== 启动入口 ==============

def run_web_server(host: str = "0.0.0.0", port: int = 18080, reload: bool = False):
    """启动 Web 服务器"""
    import uvicorn

    logger.info("=" * 60)
    logger.info("  DailyClaw Web UI Server")
    logger.info("=" * 60)
    logger.info(f"  访问地址: http://{host}:{port}")
    if reload:
        logger.info("  热重载: 已启用 (代码修改后自动生效)")
    logger.info("=" * 60)

    uvicorn.run(
        "web_server.server:app",
        host=host,
        port=port,
        log_level="info",
        reload=reload,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DailyClaw Web UI Server")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=18080, help="监听端口")
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="启用热重载 (代码修改后自动重启)",
    )

    args = parser.parse_args()
    run_web_server(host=args.host, port=args.port, reload=args.reload)
