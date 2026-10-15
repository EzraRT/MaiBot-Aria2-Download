"""qBittorrent Web API v2 token authentication and batch magnet submission."""

from __future__ import annotations

import asyncio
import re
from typing import Literal
from urllib.parse import urlsplit

import httpx

from .magnets import Magnet
from .submission import Submission


def request_headers(url: str, token: str) -> dict[str, str]:
    """Keep the API key in a header, never in a URL, cookie or login request."""
    if not token or any(not 33 <= ord(char) <= 126 for char in token):
        raise ValueError("请填写有效的 qBittorrent API Token（在 5.2.0+ 的 WebUI 设置中生成）。")
    parsed = urlsplit(url)
    return {
        "Authorization": f"Bearer {token}",
        "Origin": f"{parsed.scheme}://{parsed.netloc}",
        "Referer": url.rstrip("/") + "/",
    }


async def check_connection(client: httpx.AsyncClient, *, url: str, token: str, rpc_timeout: float) -> str:
    """Check authentication and API compatibility without creating a download."""
    headers = request_headers(url, token)
    try:
        async with asyncio.timeout(rpc_timeout):
            response = await client.get(
                f"{url.rstrip('/')}/api/v2/app/webapiVersion", headers=headers, timeout=rpc_timeout
            )
    except (TimeoutError, httpx.HTTPError):
        raise ValueError("无法连接 qBittorrent 或请求超时，请检查 WebUI 地址和服务状态。") from None
    if response.status_code in {401, 403}:
        raise ValueError("qBittorrent API Token 校验失败，请检查 Token 和 WebUI 访问限制。")
    if response.status_code != 200:
        raise ValueError(f"qBittorrent 连通性校验失败（HTTP {response.status_code}），请检查 WebUI 地址。")
    version = response.text.strip()
    if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", version):
        raise ValueError("qBittorrent 未返回有效的 WebAPI 版本，请检查 WebUI 根地址和反向代理配置。")
    if tuple(map(int, version.split("."))) < (2, 14, 1):
        raise ValueError("qBittorrent API Token 需要 qBittorrent 5.2.0+（WebAPI 2.14.1+），请升级下载器。")
    return version


async def submit_magnets(
    client: httpx.AsyncClient,
    magnets: list[Magnet],
    *,
    url: str,
    token: str,
    rpc_timeout: float,
    download_dir: str = "",
    category: str = "",
    tags: list[str] | None = None,
) -> list[Submission]:
    """Accept a batch once; an HTTP acknowledgement does not prove each task was added."""
    if not magnets:
        return []

    def all_results(status: Literal["accepted", "rejected", "unknown"], error: str = "") -> list[Submission]:
        return [Submission(magnet, status, error=error) for magnet in magnets]

    base_url = url.rstrip("/")
    try:
        headers = request_headers(base_url, token)
    except ValueError as exc:
        return all_results("rejected", str(exc))
    form = {"urls": "\n".join(magnet.uri for magnet in magnets)}
    if download_dir:
        form.update(savepath=download_dir, autoTMM="false")
    if category:
        form["category"] = category
    if tags:
        form["tags"] = ",".join(tags)

    try:
        async with asyncio.timeout(rpc_timeout):
            response = await client.post(
                f"{base_url}/api/v2/torrents/add",
                files={name: (None, value) for name, value in form.items()},
                headers=headers,
                timeout=rpc_timeout,
            )
            if response.status_code in {401, 403}:
                return all_results("rejected", "qBittorrent 未授权，请检查 API Token、下载器版本和 WebUI 访问限制。")
            if response.status_code in {400, 404, 405, 415}:
                return all_results(
                    "rejected", f"qBittorrent 拒绝请求（HTTP {response.status_code}），请检查地址和配置。"
                )
            if response.status_code == 200 and response.text.strip() == "Ok.":
                return all_results("accepted")
            if response.status_code == 200 and response.text.strip() == "Fails.":
                return all_results("rejected", "qBittorrent 拒绝本次添加请求，请检查磁链及下载配置。")
            # A reverse proxy can return 200 HTML or 5xx after forwarding the write.
            return all_results("unknown", "qBittorrent 未返回有效的接收确认，请先检查下载列表再重试。")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return all_results("rejected", "无法连接 qBittorrent，请检查 WebUI 地址和服务状态。")
    except (TimeoutError, httpx.HTTPError):
        return all_results("unknown", "qBittorrent 请求超时或通信异常，请先检查下载列表再重试。")
