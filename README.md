# 淄博协议 Mock Server MCP

将淄博远传水表协议的 Mock Server 封装为 MCP 工具，供 AI（Claude Code、TRAE 等支持 MCP 的客户端）在对话中直接调用。

## 功能

- 启动/停止 UDP 监听，接收水表终端上传的数据帧
- 自动回复 ACK，支持 ACK 规则配置
- ACK 抑制测试（前 N 次不回 ACK，测试设备重传机制）
- 手动发送 UDP 数据帧
- 预设指令管理（重启、阀门控制、参数查询、预置量）
- 实时日志查询与过滤
- 可选启动 Web 浏览器界面

## 快速开始

### 1. 安装依赖

```bash
pip install mcp
```

### 2. 配置 MCP

在你的项目根目录创建 `.mcp.json`：

```json
{
  "mcpServers": {
    "zibo_mock": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"]
    }
  }
}
```

将 `args` 中的路径替换为本仓库 `mcp_server.py` 的实际路径。

### 3. 使用

用支持 MCP 的 AI 客户端（Claude Code、TRAE 等）打开项目目录，AI 会自动发现工具。对话中直接说：

- "启动 UDP 监听，端口 30088"
- "查看最近的通信日志"
- "开启 ACK 抑制，抑制 3 次"
- "添加一条重启指令的预设"

## 可用工具（16 个）

| 工具 | 功能 |
|------|------|
| zibo_get_protocol_info | 获取协议信息和指令定义 |
| zibo_get_state | 获取运行状态 |
| zibo_start_listen | 启动 UDP 监听 |
| zibo_stop_listen | 停止 UDP 监听 |
| zibo_send_udp | 手动发送 UDP 数据 |
| zibo_get_logs | 获取通信日志 |
| zibo_clear_logs | 清空日志 |
| zibo_get_rules | 获取 ACK 规则 |
| zibo_toggle_rule | 启用/禁用规则 |
| zibo_save_rules | 保存规则 |
| zibo_set_ack_suppress | 设置 ACK 抑制测试 |
| zibo_add_pending_command | 添加预设指令 |
| zibo_get_pending_commands | 获取预设指令列表 |
| zibo_clear_pending_commands | 清空预设指令 |
| zibo_start_web_ui | 启动 Web 界面 |
| zibo_stop_web_ui | 停止 Web 界面 |

## 文件说明

```
├── .mcp.json              # MCP 配置示例
├── mcp_server.py          # MCP Server 主体（16 个工具）
├── server.py              # Mock Server 核心逻辑
├── protocol_base.py       # 协议抽象基类
├── config.json            # 配置文件
├── mock_server.html       # Web 界面
└── protocols/
    ├── __init__.py
    └── zibo.py            # 淄博协议实现
```

## 架构

MCP Server 内嵌 Mock Server 核心逻辑，AI 直接操作内存状态，无需先启动独立的 HTTP 服务。Web 界面作为可选功能通过 zibo_start_web_ui 按需启动，与 MCP 工具共享同一状态。
