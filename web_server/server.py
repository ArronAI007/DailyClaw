"""
DailyClaw Web Server

基于 FastAPI 的 Web 控制界面，支持：
- Dashboard 概览
- 报告查看
- 配置管理
- 手动触发爬取
"""

import os
import re
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
                    reports.append({
                        "date": date_folder.name,
                        "date_obj": date_obj,
                        "filename": html_file.name,
                        "path": f"/output/{date_folder.name}/html/{html_file.name}",
                        "size": html_file.stat().st_size,
                    })

    return reports


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

        # 尝试获取最新数据来显示哪些平台有数据
        try:
            data_service = get_data_service()
            latest_news = data_service.get_latest_news(limit=1000)
            active_platforms = set(n["platform"] for n in latest_news)
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

    # 获取最新新闻统计
    try:
        data_service = get_data_service()
        latest_news = data_service.get_latest_news(limit=50)
        total_news_today = len(latest_news)
    except Exception:
        total_news_today = 0

    # 获取趋势话题
    try:
        trending = data_service.get_trending_topics(top_n=5, mode="daily")
        trending_topics = trending.get("topics", [])
    except Exception:
        trending_topics = []

    # 平台状态
    platforms = get_platform_status()

    # 报告列表
    reports = get_report_list()[:5]

    return templates.TemplateResponse(request, "dashboard.html", {
        "version": VERSION,
        "status": status,
        "total_news_today": total_news_today,
        "trending_topics": trending_topics,
        "platforms": platforms,
        "reports": reports,
    })


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    """报告列表页"""
    reports = get_report_list()

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
