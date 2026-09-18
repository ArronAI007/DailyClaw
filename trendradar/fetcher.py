import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from trendradar import utils
from trendradar.logging_config import get_logger


logger = get_logger(__name__)


IdInfo = Union[str, Tuple[str, str], Tuple[str, str, str]]


class DataFetcher:
    """数据获取器"""

    def __init__(
        self,
        request_interval: int = 1000,
        proxy_url: Optional[str] = None,
        max_workers: int = 5,
    ):
        self.request_interval = request_interval
        self.proxy_url = proxy_url
        self.max_workers = max_workers

    def fetch_data(
        self,
        id_info: Union[str, Tuple[str, str], Tuple[str, str, str]],
        max_retries: int = 2,
        min_retry_wait: int = 3,
        max_retry_wait: int = 5,
    ) -> Tuple[Optional[str], str, str]:
        """获取指定ID数据，支持重试"""
        custom_url = ""
        if isinstance(id_info, tuple):
            id_value = id_info[0]
            alias = id_info[1] if len(id_info) > 1 else id_value
            custom_url = id_info[2] if len(id_info) > 2 else ""
        else:
            id_value = id_info
            alias = id_value

        if custom_url:
            url = custom_url.replace("{id}", id_value)
        else:
            url = f"https://newsnow.busiyi.world/api/s?id={id_value}&latest"

        proxies = None
        if self.proxy_url:
            proxies = {"http": self.proxy_url, "https": self.proxy_url}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
        }

        retries = 0
        while retries <= max_retries:
            try:
                response = requests.get(
                    url, proxies=proxies, headers=headers, timeout=10
                )
                response.raise_for_status()

                data_text = response.text
                data_json = json.loads(data_text)

                status = data_json.get("status", "未知")
                if status not in ["success", "cache"]:
                    raise ValueError(f"响应状态异常: {status}")

                status_info = "最新数据" if status == "success" else "缓存数据"
                logger.info(f"获取 {id_value} 成功（{status_info}）")
                return data_text, id_value, alias

            except Exception as e:
                retries += 1
                if retries <= max_retries:
                    base_wait = random.uniform(min_retry_wait, max_retry_wait)
                    additional_wait = (retries - 1) * random.uniform(1, 2)
                    wait_time = base_wait + additional_wait
                    logger.exception(f"请求 {id_value} 失败: {e}. {wait_time:.2f}秒后重试...")
                    time.sleep(wait_time)
                else:
                    logger.exception(f"请求 {id_value} 失败: {e}")
                    return None, id_value, alias
        return None, id_value, alias

    @staticmethod
    def _extract_id_value(id_info: IdInfo) -> str:
        return id_info[0] if isinstance(id_info, tuple) else id_info

    def _fetch_with_stagger(self, id_info: IdInfo, start_delay: float) -> Tuple[Optional[str], str, str]:
        """按错峰延迟等待后再发起请求，避免并发瞬间打满目标接口"""
        if start_delay > 0:
            time.sleep(start_delay)
        return self.fetch_data(id_info)

    def crawl_websites(
        self,
        ids_list: List[IdInfo],
        request_interval: Optional[int] = None,
        max_workers: Optional[int] = None,
    ) -> Tuple[Dict, Dict, List]:
        """并发爬取多个网站数据

        使用线程池并发请求各平台接口；同一批并发 worker 内部按
        request_interval 错峰启动，既提升整体抓取速度，又避免瞬间
        对目标接口发起过多并发请求。
        """
        results: Dict[str, Any] = {}
        id_to_name: Dict[str, str] = {}
        failed_ids: List[str] = []

        if not ids_list:
            return results, id_to_name, failed_ids

        interval = request_interval if request_interval is not None else self.request_interval
        workers = max(1, max_workers if max_workers is not None else self.max_workers)

        for id_info in ids_list:
            id_value = self._extract_id_value(id_info)
            name = id_info[1] if isinstance(id_info, tuple) and len(id_info) > 1 else id_value
            id_to_name[id_value] = name

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_id: Dict[Any, str] = {}
            for i, id_info in enumerate(ids_list):
                batch_position = i % workers
                base_delay = (interval + random.randint(-10, 20)) / 1000
                start_delay = max(0.0, batch_position * base_delay)
                future = executor.submit(self._fetch_with_stagger, id_info, start_delay)
                future_to_id[future] = self._extract_id_value(id_info)

            for future in as_completed(future_to_id):
                id_value = future_to_id[future]
                try:
                    response, _, _ = future.result()
                except Exception as e:
                    logger.exception(f"请求 {id_value} 执行异常: {e}")
                    response = None

                if response:
                    try:
                        data = json.loads(response)
                        results[id_value] = {}
                        for index, item in enumerate(data.get("items", []), 1):
                            title = item.get("title")
                            # 跳过无效标题（None、float、空字符串）
                            if title is None or isinstance(title, float) or not str(title).strip():
                                continue
                            title = str(title).strip()
                            url = item.get("url", "")
                            mobile_url = item.get("mobileUrl", "")

                            if title in results[id_value]:
                                results[id_value][title]["ranks"].append(index)
                            else:
                                results[id_value][title] = {
                                    "ranks": [index],
                                    "url": url,
                                    "mobileUrl": mobile_url,
                                }
                    except json.JSONDecodeError:
                        logger.error(f"解析 {id_value} 响应失败")
                        failed_ids.append(id_value)
                    except Exception as e:
                        logger.exception(f"处理 {id_value} 数据出错: {e}")
                        failed_ids.append(id_value)
                else:
                    failed_ids.append(id_value)

        logger.error(f"成功: {list(results.keys())}, 失败: {failed_ids}")
        return results, id_to_name, failed_ids