# MaiBot Aria2 Download

从 `koishi-plugin-magnet-downloads` 迁移的 MaiBot 插件。**引用一条消息，发送“给我下”**，插件提取其中的磁力链接并添加到 aria2 下载队列。

插件入口、清单和项目配置均位于仓库根目录，无需进入额外的项目子目录。

## 适用版本

- MaiBot 新版 Host/Runner 插件系统，清单声明 Host `1.2.0–1.3.99`。
- `maibot-plugin-sdk >=2.8.2,<3`，Python 3.11 及以上。
- 已开启 HTTP/HTTPS JSON-RPC 的 aria2。
- QQ 转发详情查询默认使用 SnowLuma 适配器（也支持其 NapCat 客户端模式）的公开 API。

本插件使用当前官方 SDK，不兼容旧版 `src.plugin_system` / `BasePlugin` 插件接口。开发验证使用 SDK 2.8.2；真实 MaiBot、QQ 和 aria2 的联调需在安装后进行。

## 安装与配置

1. 将本仓库放入 **实际运行的新版 MaiBot** 的 `plugins/MaiBot-Aria2-Download/`，保持 `plugin.py` 和 `_manifest.json` 在该插件目录的第一层。也可以将当前项目目录链接到该位置。
2. 启动或重新加载 MaiBot 插件。Host 根据清单安装 `httpx` 依赖，SDK 由 MaiBot 运行环境提供。
3. 在 MaiBot WebUI 的插件配置中填写 `aria2_url` 和 `aria2_token`；也可修改安装目录中自动生成的 `config.toml`。配置变更由框架热更新。
4. 引用一条含有磁链的消息，发送 `给我下`。收到“已添加 N 个下载任务到 aria2”表示 aria2 已返回任务 ID，**不代表文件已下载完成**。

配置示例见 [`config.example.toml`](config.example.toml)。该示例不是运行时配置，真实密钥应写入被 Git 忽略的 `config.toml`。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `aria2_url` | `http://127.0.0.1:6800/jsonrpc` | 从 MaiBot Runner 所在环境访问 aria2 的地址 |
| `aria2_token` | 空 | 对应 aria2 的 `rpc-secret`；不需要加 `token:` 前缀 |
| `download_dir` | 空 | aria2 服务器上的下载目录，留空沿用 aria2 配置 |
| `rpc_timeout` | `15` | 整个批量提交请求的超时秒数，范围 1–30 |
| `max_links` | `50` | 去重后每次最多提交的磁链数，超限时整批不提交 |
| `allowed_user_ids` | `[]` | 允许使用的用户 ID 字符串列表，空列表表示不限 |
| `allowed_group_ids` | `[]` | 允许使用的群 ID 字符串列表，非空时禁止私聊 |
| `enabled` | `true` | 是否处理下载命令 |
| `adapter_api_prefix` | `maibot-team.snowluma-adapter.adapter.napcat.message` | QQ 消息查询 API 前缀，留空则不调用适配器 |
| `plugin.config_version` | `1.0.0` | SDK 必需的配置版本字段，位于 `[plugin]` 节 |

允许列表同时填写时，用户和群必须同时匹配。默认沿用原插件的使用范围，不额外限制用户或群。若 MaiBot 已配置命令权限，还需通过宿主对 `magnet_download` 命令的权限检查。

原 Koishi 配置迁移：`aria2Url` 对应 `aria2_url`，`aria2Token` 对应 `aria2_token`。不沿用源码中写死的示例密钥 `12345678`，请填写实际的 RPC 密钥。

MaiBot 与 aria2 分别运行在容器或不同机器时，`127.0.0.1` 指向 Runner 自己所在的网络环境，需要改为能访问 aria2 的地址。文件始终由 aria2 下载到其服务器，而非下载到聊天客户端。

## 使用行为

- 与原插件代码一致，以“给我下”结尾的消息触发，允许尾部空白，不要求斜杠或 @。原包描述中的“让我下”是文案差异，实际命令仍为“给我下”。
- 必须引用一条消息。仅发送磁链或“磁链＋给我下”不会自动下载，会提示引用消息。
- 支持普通文本、混合消息和嵌套合并转发；读取转发中的所有文本段，不限于第一个消息段。
- 支持 40 位十六进制及 32 位 Base32 的 BTIH 磁链，保留名称 `dn`、Tracker `tr` 等参数。仅支持 `btmh` 的 v2 磁链、裸哈希、种子附件和图片中的磁链不在处理范围内。
- 单条命令内按信息哈希去重，十六进制和 Base32 表达也会合并。同一命令消息重复投递时，运行中的请求及最近 10 分钟内最多 512 条已处理消息会被忽略。此缓存不跨插件重启，不对用户新发送的命令做全局去重。
- 多条磁链使用 aria2 JSON-RPC batch，各自调用一次 `aria2.addUri`，逐项核对返回 ID，反馈成功、失败和未确认数量。
- 超时或响应不完整时不自动重试，以免已被 aria2 接收的任务重复添加；请先检查下载列表，再决定是否发送新的命令。

## 引用与转发的读取方式

优先使用 `ctx.message.get_by_id` 在当前聊天流内读取原消息。若原消息不存在或只有转发预览，在 QQ 平台使用配置的适配器 API：

- `get_msg(message_id=...)`：读取引用目标或转发中的引用节点。
- `get_forward_msg(id=...)`：获取仅携带 ID 的合并转发。

适配器 API 是可选能力，不作为硬性插件依赖；其他平台在 MaiBot 已保存完整消息或带有文本引用预览时仍可使用。转发解析设有深度、节点数、详情查询次数和整体 35 秒读取限制，读取不完整会明确提示。若所有可读内容中都没有有效磁链，不会提交下载。

“无法读取被引用的消息”通常意味着 MaiBot 没有历史记录且适配器无法取回详情，或者仅提供了 `[forward]` 占位文本。可检查适配器是否启用、API 前缀是否匹配，或重新发送原文后引用。

## 开发与验证

在本项目根目录运行：

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

测试按照 MaiBot PluginLoader 的包加载方式导入插件，使用真实 SDK 验证组件注册、默认配置、WebUI Schema 和能力代理；使用模拟 Host 与 HTTP 传输验证消息解析、aria2 请求协议和错误反馈。测试不会向实际 aria2 创建下载任务，也不会向聊天平台发送消息。

源码分工：`plugin.py` 管理命令与配置，`messages.py` 读取消息，`magnets.py` 提取和去重，`aria2_rpc.py` 提交下载。

清单中的仓库链接按当前项目名称填写为 `https://github.com/EzraRT/MaiBot-Aria2-Download`；此迁移未创建或发布远程仓库。发布前请将它核对为实际仓库地址。

## 参考

- [MaiBot 插件开发指南](https://docs.mai-mai.org/plugin/)
- [MaiBot Command](https://docs.mai-mai.org/plugin/commands)
- [MaiBot 配置管理](https://docs.mai-mai.org/plugin/config)
- [MaiBot Plugin SDK](https://github.com/Mai-with-u/maibot-plugin-sdk)
- [aria2 JSON-RPC](https://aria2.github.io/manual/en/html/aria2c.html#rpc-interface)

许可证：MIT，沿用原插件许可。
