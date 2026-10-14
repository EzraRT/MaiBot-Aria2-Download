"""MaiBot entry point for the migrated magnet-downloads plugin."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import httpx
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF, Command, Field, MaiBotPlugin, PluginConfigBase
from pydantic import field_validator

from .aria2_rpc import submit_magnets as submit_to_aria2
from .magnets import extract_magnets
from .messages import MessageResolver, SourceError
from .qbittorrent_api import submit_magnets as submit_to_qbittorrent

COMMAND_PATTERN = r"(?s)^.*给我下\s*$"


class PluginMetadata(PluginConfigBase):
    __ui_label__ = "配置版本"

    config_version: str = Field(default="1.0.0", description="插件配置版本，请勿修改")


class DownloadSettings(PluginConfigBase):
    __ui_label__ = "磁链下载"

    enabled: bool = Field(default=True, description="启用磁链下载")
    downloader: Literal["aria2", "qbittorrent"] = Field(
        default="aria2", description="下载器", json_schema_extra={"x-widget": "select"}
    )
    aria2_url: str = Field(default="http://127.0.0.1:6800/jsonrpc", description="aria2 JSON-RPC 地址")
    aria2_token: str = Field(
        default="",
        description="aria2 RPC 密钥；未启用认证时留空",
        repr=False,
        json_schema_extra={"input_type": "password"},
    )
    qbittorrent_url: str = Field(
        default="http://127.0.0.1:8080", description="qBittorrent WebUI 根地址，可含反向代理路径"
    )
    qbittorrent_username: str = Field(default="", description="qBittorrent WebUI 用户名；免认证时与密码同时留空")
    qbittorrent_password: str = Field(
        default="",
        description="qBittorrent WebUI 密码",
        repr=False,
        json_schema_extra={"input_type": "password"},
    )
    qbittorrent_category: str = Field(default="", description="qBittorrent 任务分类；留空沿用下载器默认值")
    qbittorrent_tags: list[str] = Field(default_factory=list, description="qBittorrent 任务标签列表")
    download_dir: str = Field(default="", description="所选下载器服务器上的下载目录；留空使用下载器默认设置")
    rpc_timeout: float = Field(default=15, ge=1, le=30, description="下载器请求超时（秒）；qBittorrent 包含登录与提交")
    max_links: int = Field(default=50, ge=1, le=500, description="单条命令最多添加的不同磁链数量")
    allowed_user_ids: list[str] = Field(default_factory=list, description="允许使用的用户 ID；留空不限")
    allowed_group_ids: list[str] = Field(default_factory=list, description="允许使用的群 ID；留空不限，填写后禁用私聊")
    adapter_api_prefix: str = Field(
        default="maibot-team.snowluma-adapter.adapter.napcat.message",
        description="QQ 适配器消息 API 前缀；留空仅使用 MaiBot 历史消息",
    )

    @field_validator("aria2_url")
    @classmethod
    def valid_rpc_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.fragment:
            raise ValueError("aria2_url 必须是有效的 HTTP/HTTPS JSON-RPC 地址")
        _ = parsed.port  # Reject invalid port numbers at configuration time.
        return value

    @field_validator("qbittorrent_url")
    @classmethod
    def valid_qbittorrent_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or any(char.isspace() for char in value)
            or parsed.path.rstrip("/").endswith("/api/v2")
        ):
            raise ValueError("qbittorrent_url 必须是 WebUI 根地址，不包含认证信息、查询参数或 /api/v2")
        _ = parsed.port
        return value

    @field_validator("qbittorrent_tags")
    @classmethod
    def normalize_qbittorrent_tags(cls, values: list[str]) -> list[str]:
        if any("," in value or "\n" in value or "\r" in value for value in values):
            raise ValueError("qBittorrent 单个标签不能包含逗号或换行，请使用列表中的多个元素")
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("adapter_api_prefix")
    @classmethod
    def normalize_api_prefix(cls, value: str) -> str:
        return value.strip().rstrip(".")


class DownloadConfig(PluginConfigBase):
    plugin: PluginMetadata = Field(default_factory=PluginMetadata)
    general: DownloadSettings = Field(default_factory=DownloadSettings)


class MagnetDownloadsPlugin(MaiBotPlugin):
    config_model = DownloadConfig

    def __init__(self) -> None:
        super().__init__()
        self._inflight: set[tuple[str, str]] = set()
        self._handled: OrderedDict[tuple[str, str], float] = OrderedDict()

    def normalize_plugin_config(self, config_data: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]:
        """Migrate flat 1.0.0 settings to the same section that WebUI reads and saves."""
        raw = dict(config_data or {})
        legacy = {name: raw.pop(name) for name in DownloadSettings.model_fields if name in raw}
        if legacy:
            general = raw.get("general", {})
            if isinstance(general, Mapping):
                # Explicit WebUI edits take priority over values left at the old root paths.
                raw["general"] = {**legacy, **general}
        # Keep config_version at 1.0.0: Runner rebuilds newer-version configs before this
        # migration hook, which would otherwise discard the old root-level settings.
        normalized, changed = super().normalize_plugin_config(raw)
        return normalized, changed or normalized != dict(config_data or {})

    async def on_load(self) -> None:
        self.ctx.logger.info("磁链下载插件已加载：引用消息并发送“给我下”")

    async def on_unload(self) -> None:
        self._handled.clear()
        self.ctx.logger.info("磁链下载插件已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """Apply this plugin's latest settings; Host/Runner owns persistence to config.toml."""
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        # Idempotent when Runner has already injected the new configuration.
        self.set_plugin_config(config_data)
        self.ctx.logger.info("磁链下载配置已更新: version=%s", version)

    async def _reply(self, text: str, stream_id: str, success: bool = False) -> tuple[bool, str, int]:
        if not await self.ctx.send.text(text, stream_id):
            self.ctx.logger.warning("下载结果消息发送失败")
        return success, text, 2

    @Command(
        "magnet_download",
        description="引用一条含有磁链的消息，发送“给我下”提交到所选下载器",
        pattern=COMMAND_PATTERN,
        timeout_ms=90000,
    )
    async def handle_download(self, **kwargs: Any) -> tuple[bool, str, int]:
        message = kwargs.get("message")
        if not isinstance(message, dict):
            return False, "命令缺少完整消息对象", 2
        stream_id = str(kwargs.get("stream_id") or message.get("session_id") or "")
        if not stream_id:
            return False, "命令缺少聊天流 ID", 2
        config = cast(DownloadConfig, self.config).general.model_copy(deep=True)
        if not config.enabled:
            return False, "磁链下载已禁用", 0
        info = message.get("message_info") or {}
        user_id = str((info.get("user_info") or {}).get("user_id") or "")
        group_id = str((info.get("group_info") or {}).get("group_id") or "")
        if config.allowed_user_ids and user_id not in config.allowed_user_ids:
            return await self._reply("你没有使用磁链下载的权限。", stream_id)
        if config.allowed_group_ids and group_id not in config.allowed_group_ids:
            return await self._reply("当前会话未启用磁链下载。", stream_id)

        key = (stream_id, str(message.get("message_id") or ""))
        now = time.monotonic()
        while self._handled and next(iter(self._handled.values())) < now - 600:
            self._handled.popitem(last=False)
        if key[1] and (key in self._inflight or key in self._handled):
            return True, "该命令消息已处理，忽略重复投递", 2
        self._inflight.add(key)
        try:
            resolver = MessageResolver(
                self.ctx,
                stream_id=stream_id,
                platform=str(message.get("platform") or ""),
                adapter_api_prefix=config.adapter_api_prefix,
            )
            try:
                source = await asyncio.wait_for(resolver.resolve_reply(message), timeout=35)
            except SourceError as exc:
                return await self._reply(str(exc), stream_id)
            except TimeoutError:
                return await self._reply("读取引用或转发消息超时，尚未提交下载，请稍后重试。", stream_id)
            magnets = extract_magnets(source.texts)
            warnings = "\n".join(sorted(source.warnings))
            if not magnets:
                response = "没有找到有效的 BTIH 磁力链接。"
                return await self._reply(response + ("\n" + warnings if warnings else ""), stream_id)
            if len(magnets) > config.max_links:
                return await self._reply(
                    f"找到 {len(magnets)} 条不同磁链，超过单次上限 {config.max_links} 条；"
                    "本次未提交，请拆分消息或调整配置。",
                    stream_id,
                )
            # Keep credentials local to this command and ignore environment HTTP proxies.
            downloader_name = "qBittorrent" if config.downloader == "qbittorrent" else "aria2"
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                if config.downloader == "qbittorrent":
                    results = await submit_to_qbittorrent(
                        client,
                        magnets,
                        url=config.qbittorrent_url,
                        username=config.qbittorrent_username,
                        password=config.qbittorrent_password,
                        category=config.qbittorrent_category,
                        tags=config.qbittorrent_tags,
                        rpc_timeout=config.rpc_timeout,
                        download_dir=config.download_dir,
                    )
                else:
                    results = await submit_to_aria2(
                        client,
                        magnets,
                        url=config.aria2_url,
                        token=config.aria2_token,
                        rpc_timeout=config.rpc_timeout,
                        download_dir=config.download_dir,
                    )
            added = sum(item.status == "added" for item in results)
            accepted = sum(item.status == "accepted" for item in results)
            rejected = sum(item.status == "rejected" for item in results)
            unknown = sum(item.status == "unknown" for item in results)
            if accepted:
                lines = [f"收到🫡 已向 {downloader_name} 提交 {accepted} 条磁链，下载器已接收请求。"]
                lines.append("任务是否新增及实际下载状态请查看下载列表。")
            else:
                lines = [
                    f"收到🫡 已添加 {added} 个下载任务到 {downloader_name}。" if added else "未确认有任务添加成功。"
                ]
            if rejected:
                lines.append(f"添加失败：{rejected} 个。")
            if unknown:
                lines.append(f"结果未确认：{unknown} 个。请先检查 {downloader_name} 下载列表，避免重复提交。")
            lines.extend(dict.fromkeys(item.error for item in results if item.error))
            if warnings:
                lines.append(warnings)
            self.ctx.logger.info(
                "磁链提交结果：下载器=%s 已添加=%d 已接收=%d 失败=%d 未确认=%d",
                downloader_name,
                added,
                accepted,
                rejected,
                unknown,
            )
            return await self._reply("\n".join(lines), stream_id, success=added + accepted == len(results))
        finally:
            self._inflight.discard(key)
            if key[1]:
                self._handled[key] = time.monotonic()
                while len(self._handled) > 512:
                    self._handled.popitem(last=False)


def create_plugin() -> MagnetDownloadsPlugin:
    return MagnetDownloadsPlugin()
