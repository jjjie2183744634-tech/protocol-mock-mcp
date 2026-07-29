#!/usr/bin/env python3
"""
install_mcp.py — 自动配置 MCP 到当前项目

用法：
  python install_mcp.py                # 配置到当前目录的 .mcp.json
  python install_mcp.py /path/to/project  # 配置到指定项目目录

原理：
  找到 mcp_server.py 的绝对路径，生成 .mcp.json 写入目标项目根目录。
  AI 客户端（Claude Code、TRAE 等）打开该项目后自动发现 MCP 工具。
"""

import json
import os
import sys

def main():
    # mcp_server.py 和本脚本在同一目录
    mcp_server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
    if not os.path.exists(mcp_server_path):
        print(f"[错误] 找不到 mcp_server.py: {mcp_server_path}")
        sys.exit(1)

    # 确定目标项目目录
    if len(sys.argv) > 1:
        target_dir = os.path.abspath(sys.argv[1])
    else:
        target_dir = os.getcwd()

    if not os.path.isdir(target_dir):
        print(f"[错误] 目标目录不存在: {target_dir}")
        sys.exit(1)

    # 检测 python 命令
    python_cmd = sys.executable if sys.executable else "python"

    # 生成 .mcp.json
    mcp_config = {
        "mcpServers": {
            "protocol_mock": {
                "command": python_cmd,
                "args": [mcp_server_path]
            }
        }
    }

    config_path = os.path.join(target_dir, ".mcp.json")

    # 如果已存在，提示覆盖
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            old = json.load(f)
        if "protocol_mock" in old.get("mcpServers", {}):
            print(f"[提示] .mcp.json 中已有 protocol_mock 配置，将覆盖")

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(mcp_config, f, indent=2, ensure_ascii=False)

    print(f"[完成] MCP 配置已写入: {config_path}")
    print(f"  Python: {python_cmd}")
    print(f"  Server: {mcp_server_path}")
    print()
    print("下一步:")
    print(f"  1. 用 AI 客户端打开目录: {target_dir}")
    print(f"  2. AI 会自动发现 protocol_mock 的 16 个工具")
    print(f"  3. 对话中说: '启动 UDP 监听' 即可开始")

if __name__ == "__main__":
    main()
