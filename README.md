# 客户定制协议 Mock Server MCP

将水表/终端远传协议的 Mock Server 封装为 MCP 工具，供 AI（Claude Code、TRAE 等支持 MCP 的客户端）在对话中直接调用。采用插件化架构，支持任意客户定制协议。

## 功能

- 启动/停止 UDP 监听，接收水表终端上传的数据帧
- 自动回复 ACK，支持 ACK 规则配置
- ACK 抑制测试（前 N 次不回 ACK，测试设备重传机制）
- 手动发送 UDP 数据帧
- 预设指令管理（重启、阀门控制、参数查询、预置量）
- 实时日志查询与过滤
- 可选启动 Web 浏览器界面，与 AI 操作实时同步
- **插件化协议架构**：添加新协议只需写一个 Python 文件 + 改一行配置

## 快速开始

### 1. 安装依赖

```bash
pip install mcp
```

### 2. 配置 MCP

有两种方式：

**方式 A：自动配置（推荐）**

在你希望使用 MCP 的项目目录下运行：

```bash
python /path/to/protocol-mock-mcp/install_mcp.py
```

脚本会自动检测 Python 路径和 `mcp_server.py` 的绝对路径，在当前目录生成 `.mcp.json`。也可以指定目标目录：

```bash
python /path/to/protocol-mock-mcp/install_mcp.py /your/project/dir
```

**方式 B：手动配置**

在你的项目根目录创建 `.mcp.json`，填入 `mcp_server.py` 的实际路径：

```json
{
  "mcpServers": {
    "protocol_mock": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"]
    }
  }
}
```

> 仓库自带的 `.mcp.json` 使用相对路径 `./mcp_server.py`，如果你的 AI 客户端支持相对路径且工作目录就是仓库目录，可以直接复制使用。

### 3. 使用

用支持 MCP 的 AI 客户端（Claude Code、TRAE 等）打开配置了 `.mcp.json` 的项目目录，AI 会自动发现工具。对话中直接说：

- "启动 Web 界面和 UDP 监听"（浏览器实时查看 + AI 同时操作）
- "查看最近的通信日志"
- "开启 ACK 抑制，抑制 3 次"
- "添加一条重启指令的预设"

**Web 界面与 AI 同步：** `mock_start_web_ui` 启动后，浏览器显示的内容和 AI 通过 `mock_get_logs` 读到的是同一份内存数据，SSE 实时推送无需刷新。

## 可用工具（16 个）

| 工具 | 功能 |
|------|------|
| mock_get_protocol_info | 获取协议信息和指令定义 |
| mock_get_state | 获取运行状态 |
| mock_start_listen | 启动 UDP 监听 |
| mock_stop_listen | 停止 UDP 监听 |
| mock_send_udp | 手动发送 UDP 数据 |
| mock_get_logs | 获取通信日志 |
| mock_clear_logs | 清空日志 |
| mock_get_rules | 获取 ACK 规则 |
| mock_toggle_rule | 启用/禁用规则 |
| mock_save_rules | 保存规则 |
| mock_set_ack_suppress | 设置 ACK 抑制测试 |
| mock_add_pending_command | 添加预设指令 |
| mock_get_pending_commands | 获取预设指令列表 |
| mock_clear_pending_commands | 清空预设指令 |
| mock_start_web_ui | 启动 Web 界面 |
| mock_stop_web_ui | 停止 Web 界面 |

## 扩展：添加新协议插件

本项目采用插件化架构，添加新协议只需写一个 Python 文件 + 改一行配置。详见 [插件开发指南](PLUGIN_GUIDE.md)。

## 文件说明

```
├── .mcp.json              # MCP 配置（相对路径，可按需修改）
├── install_mcp.py         # 自动配置脚本（推荐使用）
├── mcp_server.py          # MCP Server 主体（16 个工具）
├── server.py              # Mock Server 核心逻辑
├── protocol_base.py       # 协议抽象基类
├── config.json            # 配置文件
├── mock_server.html       # Web 界面
├── PLUGIN_GUIDE.md        # 插件开发指南
└── protocols/
    ├── __init__.py
    └── zibo.py            # 淄博协议实现（参考示例）
```

## 架构

MCP Server 内嵌 Mock Server 核心逻辑，AI 直接操作内存状态，无需先启动独立的 HTTP 服务。Web 界面作为可选功能通过 mock_start_web_ui 按需启动，与 MCP 工具共享同一状态。

框架层（server.py）协议无关，所有协议解析/构造通过 protocols/ 目录下的插件完成。切换协议只需修改 config.json 中的 `"protocol"` 字段。
