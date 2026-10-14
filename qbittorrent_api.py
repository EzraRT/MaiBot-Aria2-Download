"""qBittorrent Web API v2 login and batch magnet submission."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Literal
from urllib.parse import urlsplit

import httpx

from .magnets import Magnet
from .submission import Submission


async def submit_magnets(
    client: httpx.AsyncClient,
    magnets: list[Magnet],
    *,
    url: str,
    username: str,
    password: str,
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

    if bool(username) != bool(password):
        return all_results("rejected", "请同时填写 qBittorrent 用户名和密码；仅在已配置免认证时同时留空。")

    base_url = url.rstrip("/")
    parsed = urlsplit(base_url)
    headers = {"Origin": f"{parsed.scheme}://{parsed.netloc}", "Referer": base_url + "/"}
    form = {"urls": "\n".join(magnet.uri for magnet in magnets)}
    if download_dir:
        form.update(savepath=download_dir, autoTMM="false")
    if category:
        form["category"] = category
    if tags:
        form["tags"] = ",".join(tags)

    logged_in = False
    submitting = False
    try:
        async with asyncio.timeout(rpc_timeout):
            if username:
                response = await client.post(
                    f"{base_url}/api/v2/auth/login",
                    data={"username": username, "password": password},
                    headers=headers,
                    timeout=rpc_timeout,
                )
                if response.status_code == 403:
                    return all_results("rejected", "qBittorrent 拒绝登录，请检查 IP 封禁、WebUI 访问限制和认证配置。")
                if response.status_code != 200 or response.text.strip() != "Ok.":
                    return all_results("rejected", "qBittorrent 登录失败，请检查 WebUI 地址、用户名和密码。")
                logged_in = True
            # Empty credentials rely on qBittorrent's own configured authentication bypass.
            submitting = True
            response = await client.post(
                f"{base_url}/api/v2/torrents/add",
                files={name: (None, value) for name, value in form.items()},
                headers=headers,
                timeout=rpc_timeout,
            )
            if response.status_code in {401, 403}:
                return all_results("rejected", "qBittorrent 未授权，请检查用户名、密码和 WebUI 访问限制。")
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
        if submitting:
            return all_results("unknown", "qBittorrent 请求超时或通信异常，请先检查下载列表再重试。")
        return all_results("rejected", "qBittorrent 登录超时或通信异常，尚未提交下载。")
    finally:
        if logged_in:
            # Do not keep server sessions alive or let a logout failure hide an accepted batch.
            with suppress(TimeoutError, httpx.HTTPError):
                async with asyncio.timeout(2):
                    await client.post(f"{base_url}/api/v2/auth/logout", headers=headers, timeout=2)
