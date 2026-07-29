#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户定制协议 Mock Server — MCP 工具集

将 Mock Server 的全部功能封装为 MCP 工具，供 AI 在对话中直接调用：
  - 启动/停止 UDP 监听
  - 查看状态、日志
  - 手动发送 UDP 数据
  - 管理 ACK 规则、ACK 抑制测试
  - 预设指令管理
  - 查询协议信息

MCP server 内嵌 mock server 核心逻辑（复用 server.py 的 state 和 protocol），
AI 直接操作内存状态，无需先启动独立的 HTTP 服务。
可选通过 mock_start_web_ui 工具在后台线程启动浏览器界面。

启动方式（stdio 传输）：
  python mcp_server.py
"""

import os
import sys
import json
import threading
import time
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict
from mcp.server import MCPServer

# 确保能 import server.py 中的组件
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# 复用 server.py 的核心组件（不会触发 main()）
from server import (
    state, protocol, start_udp_listener, stop_udp_listener,
    bytes_to_hex, hex_to_bytes,
    ThreadingHTTPServer, Handler, HTML_FILE, config,
)

# ==================== MCP Server ====================
mcp = MCPServer(name="protocol_mock_mcp")

# HTTP server 后台线程引用
_web_thread = None
_web_server = None


def _get_state_dict() -> dict:
    """获取当前状态的字典表示"""
    return {
        "udp_running": state.udp_running,
        "listen_port": state.listen_port,
        "bind_ip": state.bind_ip,
        "auto_reply": state.auto_reply,
        "packet_count": state.packet_count,
        "ack_count": state.ack_count,
        "rule_count": len([r for r in state.rules if r.get("enabled", True)]),
        "pending_count": len(state.pending_commands),
        "protocol_name": protocol.name,
        "ack_suppress_enabled": state.ack_suppress_enabled,
        "ack_suppress_count": state.ack_suppress_count,
        "ack_suppress_current": state.ack_suppress_current,
    }


# ==================== Pydantic 输入模型 ====================

class StartListenInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    port: int = Field(default=30088, description="UDP 监听端口 (1-65535)", ge=1, le=65535)
    bind_ip: str = Field(default="0.0.0.0", description="绑定 IP 地址，0.0.0.0 表示监听所有网卡")
    auto_reply: bool = Field(default=True, description="是否自动回复 ACK")


class SendUdpInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    ip: str = Field(..., description="目标 IP 地址 (如 192.168.3.126)", min_length=1)
    port: int = Field(..., description="目标端口 (如 5683)", ge=1, le=65535)
    hex_data: str = Field(..., description="Hex 格式的数据帧 (如 '68 04 00 01 ...')", min_length=2)


class GetLogsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    limit: int = Field(default=50, description="返回最近 N 条日志", ge=1, le=200)
    log_type: Optional[str] = Field(default=None, description="按类型过滤: upload/ack/ack_suppress/command/error/ack_error")


class SetAckSuppressInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(..., description="True=开启抑制, False=关闭抑制")
    count: int = Field(default=3, description="抑制次数：前 N 次不回 ACK，第 N+1 次正常回复并自动关闭", ge=1, le=255)


class SaveRulesInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rules: List[Dict[str, Any]] = Field(..., description="ACK 回复规则列表（完整替换）")


class AddPendingCommandInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    func_code: int = Field(..., description="指令功能码 (如 52=重启, 7=参数查询, 9=阀门控制, 71=预置量)", ge=0, le=255)
    params: Dict[str, Any] = Field(default_factory=dict, description="指令参数，具体取决于功能码")


class ToggleRuleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rule_id: str = Field(..., description="规则 ID", min_length=1)
    enabled: bool = Field(..., description="True=启用, False=禁用")


class StartWebUiInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    port: int = Field(default=8090, description="Web 界面端口", ge=1, le=65535)
    auto_open: bool = Field(default=False, description="是否自动打开浏览器")


# ==================== MCP 工具定义 ====================

@mcp.tool(
    name="mock_get_protocol_info",
    annotations={
        "title": "获取协议信息",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_get_protocol_info() -> str:
    '''获取当前协议信息和支持的指令定义。

    返回协议名称、支持的指令列表（含功能码、参数定义），用于了解可用的操作。

    Returns:
        str: JSON 格式的协议信息，包含:
        - protocol_name: 协议名称
        - commands: 指令定义列表，每项含 func_code/name/params
    '''
    cmds = protocol.get_command_definitions()
    info = {
        "protocol_name": protocol.name,
        "commands": cmds,
        "ack_rules": state.rules,
    }
    return json.dumps(info, ensure_ascii=False, indent=2)


@mcp.tool(
    name="mock_get_state",
    annotations={
        "title": "获取运行状态",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_get_state() -> str:
    '''获取 Mock Server 当前运行状态。

    Returns:
        str: JSON 格式状态信息:
        - udp_running: UDP 监听是否运行中
        - listen_port / bind_ip: 监听端口和 IP
        - auto_reply: 是否自动回复 ACK
        - packet_count: 已收到的数据包总数
        - ack_count: 已发送的 ACK 总数
        - pending_count: 待发送的预设指令数
        - ack_suppress_*: ACK 抑制测试状态
    '''
    return json.dumps(_get_state_dict(), ensure_ascii=False, indent=2)


@mcp.tool(
    name="mock_start_listen",
    annotations={
        "title": "启动 UDP 监听",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def mock_start_listen(params: StartListenInput) -> str:
    '''启动 UDP 监听，开始接收水表/终端上传的数据帧。

    启动后，当终端上传数据帧时，Mock Server 会按规则自动回复 ACK。
    如果设置了预设指令，会在 ACK 之后自动下发。

    Args:
        params: 监听配置，含 port(默认30088)、bind_ip(默认0.0.0.0)、auto_reply(默认True)

    Returns:
        str: JSON 格式结果，含 ok/msg 字段
    '''
    state.listen_port = params.port
    state.bind_ip = params.bind_ip
    state.auto_reply = params.auto_reply
    ok, msg = start_udp_listener()
    return json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False)


@mcp.tool(
    name="mock_stop_listen",
    annotations={
        "title": "停止 UDP 监听",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_stop_listen() -> str:
    '''停止 UDP 监听，不再接收数据帧。

    Returns:
        str: JSON 格式结果，含 ok/msg 字段
    '''
    ok, msg = stop_udp_listener()
    return json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False)


@mcp.tool(
    name="mock_send_udp",
    annotations={
        "title": "手动发送 UDP 数据",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def mock_send_udp(params: SendUdpInput) -> str:
    '''手动发送一帧 UDP 数据到指定地址。

    用于主动向水表/终端下发指令或测试数据。需先启动 UDP 监听（共用同一 socket）。

    Args:
        params: 含 ip(目标地址)、port(目标端口)、hex_data(Hex格式数据)

    Returns:
        str: JSON 格式结果，含 ok/msg，成功时 msg 包含发送字节数
    '''
    if not state.udp_socket:
        return json.dumps({"ok": False, "msg": "UDP 监听未启动，请先调用 mock_start_listen"}, ensure_ascii=False)
    try:
        frame = hex_to_bytes(params.hex_data)
        if len(frame) == 0:
            return json.dumps({"ok": False, "msg": "Hex 数据无效或为空"}, ensure_ascii=False)
        state.udp_socket.sendto(frame, (params.ip, params.port))
        return json.dumps({"ok": True, "msg": f"已发送 {len(frame)} 字节到 {params.ip}:{params.port}", "hex": bytes_to_hex(frame)}, ensure_ascii=False)
    except OSError as e:
        return json.dumps({"ok": False, "msg": f"发送失败: {e}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "msg": f"数据解析失败: {e}"}, ensure_ascii=False)


@mcp.tool(
    name="mock_get_logs",
    annotations={
        "title": "获取日志",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_get_logs(params: GetLogsInput) -> str:
    '''获取最近的通信日志。

    日志类型说明:
    - upload: 终端上传的数据帧
    - ack: Mock Server 回复的 ACK
    - ack_suppress: ACK 抑制记录（未回复 ACK）
    - command: 下发的预设指令
    - error: 帧解析失败
    - ack_error: ACK/指令发送失败

    Args:
        params: 含 limit(返回条数,默认50) 和 log_type(可选类型过滤)

    Returns:
        str: JSON 格式的日志列表，每条含 type/timestamp/packet_num/raw_hex 等字段
    '''
    with state.lock:
        logs = list(state.log_entries[-params.limit:])
    if params.log_type:
        logs = [l for l in logs if l.get("type") == params.log_type]
    return json.dumps({"count": len(logs), "logs": logs}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="mock_clear_logs",
    annotations={
        "title": "清空日志",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_clear_logs() -> str:
    '''清空所有通信日志。

    Returns:
        str: JSON 格式结果
    '''
    with state.lock:
        cleared = len(state.log_entries)
        state.log_entries.clear()
    state.save_logs()
    return json.dumps({"ok": True, "msg": f"已清空 {cleared} 条日志"}, ensure_ascii=False)


@mcp.tool(
    name="mock_get_rules",
    annotations={
        "title": "获取 ACK 规则",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_get_rules() -> str:
    '''获取当前 ACK 回复规则列表。

    规则定义了：当收到某功能码的上传帧时，回复什么功能码的 ACK，ACK 数据域如何构造。

    Returns:
        str: JSON 格式的规则列表，每项含 id/name/match_function_code/ack_function_code/enabled 等字段
    '''
    return json.dumps(state.rules, ensure_ascii=False, indent=2)


@mcp.tool(
    name="mock_toggle_rule",
    annotations={
        "title": "启用/禁用 ACK 规则",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_toggle_rule(params: ToggleRuleInput) -> str:
    '''启用或禁用单条 ACK 回复规则。

    Args:
        params: 含 rule_id(规则ID) 和 enabled(True=启用/False=禁用)

    Returns:
        str: JSON 格式结果
    '''
    with state.lock:
        for r in state.rules:
            if r["id"] == params.rule_id:
                r["enabled"] = params.enabled
                state.save_rules()
                return json.dumps({"ok": True, "msg": f"规则 '{r['name']}' 已{'启用' if params.enabled else '禁用'}"}, ensure_ascii=False)
    return json.dumps({"ok": False, "msg": f"未找到规则 ID: {params.rule_id}"}, ensure_ascii=False)


@mcp.tool(
    name="mock_save_rules",
    annotations={
        "title": "保存 ACK 规则",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_save_rules(params: SaveRulesInput) -> str:
    '''完整替换并保存 ACK 回复规则列表。

    警告：此操作会覆盖现有规则，建议先调用 mock_get_rules 获取当前规则再修改。

    Args:
        params: 含 rules(完整的规则列表)

    Returns:
        str: JSON 格式结果
    '''
    with state.lock:
        state.rules = params.rules
        state.save_rules()
    return json.dumps({"ok": True, "msg": f"已保存 {len(params.rules)} 条规则"}, ensure_ascii=False)


@mcp.tool(
    name="mock_set_ack_suppress",
    annotations={
        "title": "设置 ACK 抑制测试",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_set_ack_suppress(params: SetAckSuppressInput) -> str:
    '''设置 ACK 抑制测试：开启后前 N 次不回 ACK，第 N+1 次正常回复并自动关闭。

    用于测试水表/终端的重传机制：设备收不到 ACK 后应按协议设定的重传次数和间隔重新发送。

    Args:
        params: 含 enabled(True=开启/False=关闭) 和 count(抑制次数,默认3)

    Returns:
        str: JSON 格式结果
    '''
    count = max(1, params.count)
    with state.lock:
        state.ack_suppress_enabled = params.enabled
        state.ack_suppress_count = count
        state.ack_suppress_current = 0
    action = f"开启，抑制 {count} 次后自动恢复" if params.enabled else "关闭"
    return json.dumps({"ok": True, "msg": f"ACK 抑制已{action}"}, ensure_ascii=False)


@mcp.tool(
    name="mock_add_pending_command",
    annotations={
        "title": "添加预设指令",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def mock_add_pending_command(params: AddPendingCommandInput) -> str:
    '''添加一条预设指令，在终端下次上传数据时随 ACK 一起下发。

    指令类型和参数取决于当前加载的协议插件。
    可先调用 mock_get_protocol_info 查看完整指令定义和参数格式。

    Args:
        params: 含 func_code(功能码) 和 params(指令参数)

    Returns:
        str: JSON 格式结果，成功时含 cmd 对象（含分配的 id 和构造的 hex 数据）
    '''
    try:
        cmd_data, err = protocol.build_command_data(params.func_code, params.params)
        if err:
            return json.dumps({"ok": False, "msg": err}, ensure_ascii=False)
        with state.lock:
            state.command_seq += 1
            cmd = {
                "id": f"cmd_{state.command_seq}",
                "func_code": params.func_code,
                "func_name": protocol.get_cmd_name(params.func_code),
                "params": params.params,
                "data_hex": bytes_to_hex(cmd_data),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            state.pending_commands.append(cmd)
        return json.dumps({"ok": True, "msg": "预设指令已添加", "cmd": cmd}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "msg": str(e)}, ensure_ascii=False)


@mcp.tool(
    name="mock_get_pending_commands",
    annotations={
        "title": "获取预设指令列表",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_get_pending_commands() -> str:
    '''获取当前待发送的预设指令列表。

    指令在终端下次上传数据时自动下发，下发后自动从列表中移除。

    Returns:
        str: JSON 格式的指令列表
    '''
    with state.lock:
        cmds = list(state.pending_commands)
    return json.dumps({"count": len(cmds), "commands": cmds}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="mock_clear_pending_commands",
    annotations={
        "title": "清空预设指令",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_clear_pending_commands() -> str:
    '''清空所有待发送的预设指令。

    Returns:
        str: JSON 格式结果
    '''
    with state.lock:
        cleared = len(state.pending_commands)
        state.pending_commands.clear()
    return json.dumps({"ok": True, "msg": f"已清空 {cleared} 条预设指令"}, ensure_ascii=False)


@mcp.tool(
    name="mock_start_web_ui",
    annotations={
        "title": "启动 Web 界面",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def mock_start_web_ui(params: StartWebUiInput) -> str:
    '''在后台启动 Web 浏览器界面（HTTP 服务）。

    启动后可通过浏览器访问 http://localhost:{port} 查看实时日志和操作界面。
    MCP 工具和 Web 界面共享同一状态，操作互通。
    如端口被占用（已有 Mock Server 运行），会返回错误。

    Args:
        params: 含 port(默认8090) 和 auto_open(是否自动打开浏览器,默认False)

    Returns:
        str: JSON 格式结果
    '''
    global _web_thread, _web_server
    if _web_server is not None:
        return json.dumps({"ok": False, "msg": "Web 界面已在运行中"}, ensure_ascii=False)

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", params.port), Handler)
        srv.daemon_threads = True
        _web_server = srv

        def _serve():
            try:
                srv.serve_forever()
            except Exception:
                pass

        _web_thread = threading.Thread(target=_serve, daemon=True)
        _web_thread.start()

        if params.auto_open:
            import webbrowser
            webbrowser.open(f"http://localhost:{params.port}")

        return json.dumps({"ok": True, "msg": f"Web 界面已启动: http://localhost:{params.port}"}, ensure_ascii=False)
    except OSError as e:
        _web_server = None
        return json.dumps({"ok": False, "msg": f"端口 {params.port} 启动失败: {e}（可能已有 Mock Server 在运行）"}, ensure_ascii=False)


@mcp.tool(
    name="mock_stop_web_ui",
    annotations={
        "title": "停止 Web 界面",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def mock_stop_web_ui() -> str:
    '''停止 Web 浏览器界面（不影响 UDP 监听）。

    Returns:
        str: JSON 格式结果
    '''
    global _web_server, _web_thread
    if _web_server is None:
        return json.dumps({"ok": False, "msg": "Web 界面未运行"}, ensure_ascii=False)
    _web_server.shutdown()
    _web_server = None
    _web_thread = None
    return json.dumps({"ok": True, "msg": "Web 界面已停止"}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
