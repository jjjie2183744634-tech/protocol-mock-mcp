# 客户定制协议 Mock Server MCP

固件开发完成后的**协议调试验证工具**。模拟平台服务器侧，接收终端上传的数据帧并解析验证，回复 ACK 测试设备接收，下发指令测试设备响应。支持任意客户定制协议，通过插件扩展。

## 典型使用场景

- **上传验证**：设备按定制协议组帧上传，查看解析结果是否正确（地址、功能码、数据域各字段是否符合协议文档）
- **ACK 验证**：Mock Server 按规则回复 ACK，验证设备是否正确接收、ACK 内容是否匹配
- **指令下发验证**：平台下发指令（重启、参数查询、阀门控制等），验证设备是否正确响应
- **重传机制测试**：ACK 抑制功能，前 N 次不回 ACK，观察设备是否按协议设定的次数和间隔重传
- **异常帧测试**：手动发送异常帧，验证设备容错能力
- **AI 辅助分析**：AI 读取解析后的日志，自动比对协议文档，辅助定位字段错位、校验错误等问题

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

### 3. 调试流程

用支持 MCP 的 AI 客户端（Claude Code、TRAE 等）打开配置了 `.mcp.json` 的项目目录，AI 会自动发现工具。典型调试流程：

```
1. "启动 Web 界面和 UDP 监听"     → 浏览器实时显示 + AI 同时可读
2. 设备上电，开始上传数据帧          → 浏览器实时查看解析结果
3. "查看最近的通信日志"             → AI 读取日志，分析字段是否正确
4. "添加一条参数查询指令的预设"      → 设备下次上传时自动下发
5. "开启 ACK 抑制，抑制 3 次"       → 测试设备重传机制
6. "发送一帧测试数据到设备"          → 手动下发异常帧测试容错
```

**Web 界面与 AI 同步：** `mock_start_web_ui` 启动后，浏览器显示的内容和 AI 通过 `mock_get_logs` 读到的是同一份内存数据，SSE 实时推送无需刷新。你肉眼盯着浏览器看实时数据流，同时让 AI 帮你分析字段对不对。

## 可用工具（16 个）

| 工具 | 功能 | 调试用途 |
|------|------|----------|
| mock_get_protocol_info | 获取协议信息和指令定义 | 查看当前协议支持的指令和参数 |
| mock_get_state | 获取运行状态 | 确认监听是否运行、收包计数 |
| mock_start_listen | 启动 UDP 监听 | 开始接收设备上传帧 |
| mock_stop_listen | 停止 UDP 监听 | 停止接收 |
| mock_send_udp | 手动发送 UDP 数据 | 主动下发测试帧或异常帧 |
| mock_get_logs | 获取通信日志 | AI 分析解析结果是否正确 |
| mock_clear_logs | 清空日志 | 清除上一轮测试数据 |
| mock_get_rules | 获取 ACK 规则 | 查看 ACK 回复规则 |
| mock_toggle_rule | 启用/禁用规则 | 临时关闭某条 ACK 测试设备重传 |
| mock_save_rules | 保存规则 | 修改 ACK 内容后持久化 |
| mock_set_ack_suppress | 设置 ACK 抑制 | 测试设备重传次数和间隔 |
| mock_add_pending_command | 添加预设指令 | 设备下次上传时自动下发指令 |
| mock_get_pending_commands | 获取预设指令列表 | 查看待下发的指令 |
| mock_clear_pending_commands | 清空预设指令 | 取消待下发指令 |
| mock_start_web_ui | 启动 Web 界面 | 浏览器实时查看数据流 |
| mock_stop_web_ui | 停止 Web 界面 | 关闭 Web 界面 |

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
