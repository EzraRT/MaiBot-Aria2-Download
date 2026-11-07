# MaiBot 磁链下载（aria2 / qBittorrent）

从 `koishi-plugin-magnet-downloads` 迁移的 MaiBot 插件。**引用一条消息，发送“给我下”**，插件提取其中的磁力链接并提交给配置的 aria2 或 qBittorrent 下载器。

当前版本为 **2.0.1**，修复 qBittorrent 添加结果误报和 Token 保存后被旧配置覆盖的问题。自 2.0.0 起，qBittorrent 使用 API Token 认证，保存配置后会校验连接。升级步骤见下文，完整变更见 [变更记录](CHANGELOG.md)。

插件入口、清单和项目配置均位于仓库根目录，无需进入额外的项目子目录。

## 适用版本

- MaiBot 新版 Host/Runner 插件系统，清单声明 Host `1.2.0–1.3.99`。
- `maibot-plugin-sdk >=2.8.2,<3`，Python 3.11 及以上。
- 下载器二选一：已开启 HTTP/HTTPS JSON-RPC 的 aria2，或已开启 WebUI 的 **qBittorrent 5.2.0+（WebAPI 2.14.1+）**。qBittorrent API Token 从该版本开始支持。
- QQ 转发详情查询默认使用 SnowLuma 适配器（也支持其 NapCat 客户端模式）的公开 API。

本插件使用当前官方 SDK，不兼容旧版 `src.plugin_system` / `BasePlugin` 插件接口。开发验证使用 SDK 2.8.2；真实 MaiBot、QQ 和下载器的联调需在安装后进行。

## 升级与认证迁移

1. 更新插件目录中的源码、清单和项目文件，保留现有 `config.toml`，不要用示例配置覆盖它。
2. 重新加载插件或重启 MaiBot，使新代码和配置界面生效。新增字段会补充默认值，`downloader` 默认为 `aria2`，沿用已有地址、密钥、下载目录和权限设置。
3. 如需使用 qBittorrent，在下载器的设置 → WebUI → API Key 中生成 Token，在插件配置中选择 `qbittorrent` 并填写 WebUI 地址和 `qbittorrent_token`。保存后的连接校验通过后，下一条命令使用新配置；已开始的命令继续使用原配置。

配置结构版本 `plugin.config_version` 保持 `1.0.0`，无需手动修改。旧版顶层字段仍会迁移到 `[general]`，以保留 aria2 等配置。此前使用 qBittorrent 用户名和密码的用户，需要升级下载器并手动生成 Token；`qbittorrent_username`、`qbittorrent_password` 已移除，旧密码不会自动转换为 Token，归一化配置时会移除这两个废弃字段。

## 安装与配置

1. 将本仓库放入 **实际运行的新版 MaiBot** 的 `plugins/MaiBot-Aria2-Download/`，保持 `plugin.py` 和 `_manifest.json` 在该插件目录的第一层。也可以将当前项目目录链接到该位置。
2. 启动或重新加载 MaiBot 插件。Host 根据清单安装 `httpx` 依赖，SDK 由 MaiBot 运行环境提供。
3. 在 MaiBot WebUI 的“磁链下载”配置分组中选择 `downloader`，并填写对应下载器的连接信息；也可修改安装目录中自动生成的 `config.toml`。默认使用 aria2，已有配置无需切换。配置变更由框架热更新。
4. 引用一条含有磁链的消息，发送 `给我下`。aria2 返回任务 ID 后回复“已添加”。qBittorrent 根据接口反馈已添加、已接收待处理和失败的数量。实际下载状态请查看下载列表。

配置示例见 [`config.example.toml`](config.example.toml)。该示例不是运行时配置，真实密钥应写入被 Git 忽略的 `config.toml`。

下载设置统一位于 `[general]` 节，与 WebUI 的“磁链下载”分组对应。例如地址完整路径为 `general.aria2_url`；下表除版本字段外均省略 `general.` 前缀。

旧版顶层配置仍可读取，保存时会规范化为 `[general]`，保留地址、密钥和权限等已有值。同一字段同时存在于顶层和 `[general]` 时，以 `[general]` 中的值为准。

`on_config_update` 仅处理 `self` 范围的通知。插件先测试当前所选下载器的连接与认证，通过后应用配置，下一条下载命令使用新值；已经开始的命令继续使用自己的配置快照。

保存后的校验只读取版本信息：qBittorrent 请求 `/api/v2/app/webapiVersion`，检查 Token 与 API 版本；aria2 请求 `aria2.getVersion`，检查地址与 RPC 密钥。校验最多等待 `min(rpc_timeout, 5)` 秒，不会创建下载任务。`enabled=false` 时跳过连接校验，允许在下载器不可用时停用插件。

**保存与校验的边界：** MaiBot 会先写入 `config.toml` 并注入候选配置，再触发异步更新事件。因此连接失败不能阻止配置文件保存；插件会在日志中记录具体原因、上报回调失败，并让当前进程的下载命令继续使用上一次已应用的配置。SDK 中供配置读取使用的数据始终保留最新保存值，包括尚未通过连接校验的 Token；校验失败、取消或旧请求完成都不会把它回退成旧值。校验期间收到的命令也使用旧配置。请修正后重新保存，直至日志出现“下载器连接校验通过”；重启会重新读取磁盘配置，内存中的旧运行配置不会跨重启保留。仅打开配置页面不会测试连接。

如果在 2.0.0 中首次填写 Token 后遇到连接失败，旧实现可能会把 SDK 配置中的 Token 回退为空；当前代码已修复此覆盖路径。升级后若 Token 已为空，需重新填写并保存一次。若配置页面仍出现空值，先刷新配置页，再在源码模式检查 `[general].qbittorrent_token` 是否为空，并对照保存时的连接校验日志排查；不要在反馈中粘贴 Token 本身。

在可视化模式保存后，切换到源码模式前建议先刷新页面。已核对的 MaiBot 配置编辑器会分别保留两种模式的草稿，可视化保存不会同步更新源码草稿；随后修改并保存旧源码可能把刚保存的 Token 覆盖为空。插件无法区分这种保存与用户有意清空 Token，因此不会擅自恢复旧密钥。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `downloader` | `aria2` | 选择 `aria2` 或 `qbittorrent`，每条命令仅使用所选下载器 |
| `aria2_url` | `http://127.0.0.1:6800/jsonrpc` | 从 MaiBot Runner 所在环境访问 aria2 的地址 |
| `aria2_token` | 空 | 对应 aria2 的 `rpc-secret`；不需要加 `token:` 前缀 |
| `qbittorrent_url` | `http://127.0.0.1:8080` | qBittorrent WebUI 根地址，可含反向代理路径，不要附加 `/api/v2` |
| `qbittorrent_token` | 空 | qBittorrent API Token；在 WebUI 的 API Key 设置中生成，仅填写 Token 本身，不加 `Bearer ` 前缀 |
| `qbittorrent_category` | 空 | qBittorrent 任务分类，留空不指定 |
| `qbittorrent_tags` | `[]` | qBittorrent 任务标签，如 `["chat", "linux"]`；单个标签不能含逗号或换行 |
| `download_dir` | 空 | 所选下载器服务器上的下载目录，留空沿用下载器配置 |
| `rpc_timeout` | `15` | 批量提交超时秒数，范围 1–30；保存后的连接校验使用该值且最多等待 5 秒 |
| `max_links` | `50` | 去重后每次最多提交的磁链数，超限时整批不提交 |
| `allowed_user_ids` | `[]` | 允许使用的用户 ID 字符串列表，空列表表示不限 |
| `allowed_group_ids` | `[]` | 允许使用的群 ID 字符串列表，非空时禁止私聊 |
| `enabled` | `true` | 是否处理下载命令 |
| `adapter_api_prefix` | `maibot-team.snowluma-adapter.adapter.napcat.message` | QQ 消息查询 API 前缀，留空则不调用适配器 |
| `plugin.config_version` | `1.0.0` | 配置结构版本，独立于插件版本，位于 `[plugin]` 节，请勿手动修改 |

允许列表同时填写时，用户和群必须同时匹配。默认沿用原插件的使用范围，不额外限制用户或群。若 MaiBot 已配置命令权限，还需通过宿主对 `magnet_download` 命令的权限检查。

原 Koishi 配置迁移：`aria2Url` 对应 `aria2_url`，`aria2Token` 对应 `aria2_token`。不沿用源码中写死的示例密钥 `12345678`，请填写实际的 RPC 密钥。

例如，在现有 `config.toml` 的 `[general]` 节内修改以下字段即可切换到 qBittorrent（不要重复创建 `[general]` 节）：

```toml
downloader = "qbittorrent"
qbittorrent_url = "http://192.168.1.10:8080"
qbittorrent_token = "填写实际生成的 API Token"
qbittorrent_category = ""
qbittorrent_tags = ["chat"]
download_dir = ""
```

qBittorrent 每次请求都通过 `Authorization: Bearer <Token>` 认证，不再使用用户名、密码或会话 Cookie。Token 必须填写，使用密码输入框显示，错误反馈不会包含 Token。设置 `download_dir` 时，qBittorrent 请求会关闭该批任务的自动种子管理（`autoTMM=false`），以使用指定目录；留空则不覆盖下载器的目录和自动管理设置。

MaiBot 与下载器分别运行在容器或不同机器时，`127.0.0.1` 指向 Runner 自己所在的网络环境，需要改为能访问下载器的地址。文件始终由所选下载器下载到其服务器，而非下载到聊天客户端。

## 使用行为

- 与原插件代码一致，以“给我下”结尾的消息触发，允许尾部空白，不要求斜杠或 @。原包描述中的“让我下”是文案差异，实际命令仍为“给我下”。
- 必须引用一条消息。仅发送磁链或“磁链＋给我下”不会自动下载，会提示引用消息。
- 支持普通文本、混合消息和嵌套合并转发；读取转发中的所有文本段，不限于第一个消息段。
- 支持 40 位十六进制及 32 位 Base32 的 BTIH 磁链，保留名称 `dn`、Tracker `tr` 等参数。仅支持 `btmh` 的 v2 磁链、裸哈希、种子附件和图片中的磁链不在处理范围内。
- 单条命令内按信息哈希去重，十六进制和 Base32 表达也会合并。同一命令消息重复投递时，运行中的请求及最近 10 分钟内最多 512 条已处理消息会被忽略。此缓存不跨插件重启，不对用户新发送的命令做全局去重。
- aria2：多条磁链使用 JSON-RPC batch，各自调用一次 `aria2.addUri`，逐项核对返回 ID，反馈成功、失败和未确认数量。
- qBittorrent 携带 API Token 向 `/api/v2/torrents/add` 一次提交多条磁链，按 Web API 返回的批次计数反馈结果。若收到旧版的 `Ok.`，则回复“已提交”。
- 超时或响应不完整时不自动重试，也不切换到另一下载器，以免已被接收的任务重复添加；请先检查下载列表，再决定是否发送新的命令。

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

本地验证使用 MaiBot PluginLoader 的包加载方式和真实 SDK，覆盖组件注册、默认配置、WebUI Schema、配置热更新、校验失败后的配置恢复与下载器切换；使用模拟 Host 与 HTTP 传输验证消息解析、aria2 批量提交、qBittorrent Token 认证、只读连接校验、分类与标签、中文目录、超时和错误反馈。验证不会向实际下载器创建任务，也不会向聊天平台发送消息。测试用例仅在本地保留，不随提交发布。

源码分工：`plugin.py` 管理命令与配置，`messages.py` 读取消息，`magnets.py` 提取和去重，`aria2_rpc.py` 与 `qbittorrent_api.py` 分别适配两种下载器，`submission.py` 定义提交结果。

清单中的仓库链接按当前项目名称填写为 `https://github.com/EzraRT/MaiBot-Aria2-Download`；此迁移未创建或发布远程仓库。发布前请将它核对为实际仓库地址。

## 参考

- [MaiBot 插件开发指南](https://docs.mai-mai.org/plugin/)
- [MaiBot Command](https://docs.mai-mai.org/plugin/commands)
- [MaiBot 配置管理](https://docs.mai-mai.org/plugin/config)
- [MaiBot 生命周期](https://docs.mai-mai.org/plugin/lifecycle)
- [MaiBot Plugin SDK](https://github.com/Mai-with-u/maibot-plugin-sdk)
- [aria2 JSON-RPC](https://aria2.github.io/manual/en/html/aria2c.html#rpc-interface)
- [qBittorrent Web API v2](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29)
- [qBittorrent API Key 认证（5.2.0+）](https://github.com/qbittorrent/wiki/blob/master/API-Key-Authentication-%28%E2%89%A5v5.2.0%29.md)

许可证：MIT，沿用原插件许可。
