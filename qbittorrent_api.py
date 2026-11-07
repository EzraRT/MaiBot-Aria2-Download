"""qBittorrent Web API v2 token authentication and batch magnet submission."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

import httpx

from .magnets import Magnet
from .submission import SubmissionSummary


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


def _parse_add_response(response: httpx.Response, count: int) -> SubmissionSummary | None:
    """Read the counts reported by WebAPI 2.14.0 and later."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    added = payload.get("success_count")
    pending = payload.get("pending_count")
    failed = payload.get("failure_count")
    counts = (added, pending, failed)
    if any(type(value) is not int or value < 0 for value in counts) or sum(counts) != count:
        return None
    return SubmissionSummary(added=added, accepted=pending, rejected=failed)


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
) -> SubmissionSummary:
    """Submit a batch once and report the download client's result counts."""
    if not magnets:
        return SubmissionSummary()

    count = len(magnets)
    base_url = url.rstrip("/")
    try:
        headers = request_headers(base_url, token)
    except ValueError as exc:
        return SubmissionSummary(rejected=count, errors=(str(exc),))
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
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return SubmissionSummary(rejected=count, errors=("无法连接 qBittorrent，请检查 WebUI 地址和服务状态。",))
    except (TimeoutError, httpx.HTTPError):
        return SubmissionSummary(unknown=count, errors=("qBittorrent 请求超时或通信异常，请先检查下载列表再重试。",))

    status = response.status_code
    if status in {401, 403}:
        return SubmissionSummary(
            rejected=count, errors=("qBittorrent 未授权，请检查 API Token、下载器版本和 WebUI 访问限制。",)
        )
    if status in {400, 404, 405, 415}:
        return SubmissionSummary(rejected=count, errors=(f"qBittorrent 拒绝请求（HTTP {status}），请检查地址和配置。",))
    if status == 409:
        return SubmissionSummary(
            rejected=count, errors=("qBittorrent 未添加本批任务（HTTP 409），请检查任务是否已存在以及磁链和下载配置。",)
        )
    if status == 200:
        body = response.text.strip()
        if body == "Ok.":
            return SubmissionSummary(accepted=count)
        if body == "Fails.":
            return SubmissionSummary(rejected=count, errors=("qBittorrent 拒绝本次添加请求，请检查磁链及下载配置。",))
    if status in {200, 202}:
        summary = _parse_add_response(response, count)
        if summary is not None:
            return summary

    # A reverse proxy can return 200 HTML or 5xx after forwarding the write.
    return SubmissionSummary(unknown=count, errors=("qBittorrent 未返回有效的接收确认，请先检查下载列表再重试。",))
