"""Async aria2 JSON-RPC batch submission; never retry an ambiguous write."""

from __future__ import annotations

import asyncio
import re
from typing import Literal
from uuid import uuid4

import httpx

from .magnets import Magnet
from .submission import Submission


async def check_connection(client: httpx.AsyncClient, *, url: str, token: str, rpc_timeout: float) -> str:
    """Read aria2's version to check the endpoint and RPC secret without adding a task."""
    request_id = uuid4().hex
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "aria2.getVersion",
        "params": [f"token:{token}"] if token else [],
    }
    try:
        async with asyncio.timeout(rpc_timeout):
            response = await client.post(url, json=request, timeout=rpc_timeout)
    except (TimeoutError, httpx.HTTPError):
        raise ValueError("无法连接 aria2 或请求超时，请检查 RPC 地址和服务状态。") from None
    if response.status_code != 200:
        raise ValueError(f"aria2 连通性校验失败（HTTP {response.status_code}），请检查 RPC 地址和密钥。")
    try:
        payload = response.json()
    except ValueError:
        raise ValueError("aria2 未返回有效的 JSON-RPC 响应，请检查 RPC 地址。") from None
    if not isinstance(payload, dict) or payload.get("id") != request_id or payload.get("jsonrpc") != "2.0":
        raise ValueError("aria2 未返回匹配的 JSON-RPC 响应，请检查 RPC 地址。")
    if "error" in payload:
        raise ValueError("aria2 拒绝连通性校验请求，请检查 RPC 密钥和服务配置。")
    result = payload.get("result")
    version = result.get("version") if isinstance(result, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", version):
        raise ValueError("aria2 未返回有效版本信息，请检查 RPC 地址。")
    return version


async def submit_magnets(
    client: httpx.AsyncClient,
    magnets: list[Magnet],
    *,
    url: str,
    token: str,
    rpc_timeout: float,
    download_dir: str = "",
) -> list[Submission]:
    """Use one batch so a large forward does not multiply the command timeout."""
    if not magnets:
        return []
    requests = []
    for magnet in magnets:
        params: list = [f"token:{token}"] if token else []
        params.append([magnet.uri])
        if download_dir:
            params.append({"dir": download_dir})
        requests.append({"jsonrpc": "2.0", "id": uuid4().hex, "method": "aria2.addUri", "params": params})

    def all_results(status: Literal["rejected", "unknown"], error: str) -> list[Submission]:
        return [Submission(magnet, status, error=error) for magnet in magnets]

    try:
        async with asyncio.timeout(rpc_timeout):
            response = await client.post(url, json=requests, timeout=rpc_timeout)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return all_results("rejected", "无法连接 aria2，请检查 RPC 地址和服务状态")
    except (TimeoutError, httpx.TimeoutException):
        return all_results("unknown", "aria2 请求超时，请先检查下载列表再重试")
    except httpx.HTTPError:
        return all_results("unknown", "aria2 通信异常，请先检查下载列表再重试")

    if not response.is_success:
        return all_results("unknown", f"aria2 返回 HTTP {response.status_code}，请检查下载列表和 RPC 配置")
    try:
        payload = response.json()
    except ValueError:
        return all_results("unknown", "aria2 返回的内容不是 JSON，请检查 RPC 地址和下载列表")
    if not isinstance(payload, list):
        return all_results("unknown", "aria2 返回了无效的批量响应，请检查下载列表")

    by_id: dict[str, list[dict]] = {}
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            by_id.setdefault(item["id"], []).append(item)
    results = []
    for request, magnet in zip(requests, magnets, strict=True):
        matches = by_id.get(request["id"], [])
        item = matches[0] if len(matches) == 1 else {}
        gid = item.get("result")
        error = item.get("error")
        if (
            item.get("jsonrpc") == "2.0"
            and isinstance(gid, str)
            and re.fullmatch(r"[0-9a-fA-F]{16}", gid)
            and "error" not in item
        ):
            results.append(Submission(magnet, "added", gid=gid))
        elif item.get("jsonrpc") == "2.0" and isinstance(error, dict) and "result" not in item:
            # Do not echo arbitrary remote error text: it may contain the RPC secret or URI.
            code = error.get("code")
            detail = f"（错误码 {code}）" if type(code) is int else ""
            results.append(Submission(magnet, "rejected", error=f"aria2 拒绝添加任务{detail}，请检查密钥和配置"))
        else:
            results.append(Submission(magnet, "unknown", error="未收到有效任务 ID，请先检查 aria2 下载列表"))
    return results
