#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用 Mock Server 框架 — 协议无关

本文件只包含框架逻辑（HTTP 服务器、UDP 监听、SSE 推送、日志管理），
不包含任何协议细节。所有协议解析/构造通过 protocol 插件完成。

换协议只需：
  1. 写 protocols/xxx.py（继承 ProtocolBase）
  2. config.json 改 "protocol": "xxx"
  3. 重启

启动入口：python server.py
"""

import json
import os
import socket
import sys
import threading
import time
import datetime
import webbrowser
import queue
import importlib
import atexit
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# 确保当前目录在 path 中，以便 import protocols.xxx
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from protocol_base import ProtocolBase, bytes_to_hex, hex_to_bytes

# ==================== 加载配置 + 协议插件 ====================
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

_DEFAULT_CONFIG = {"protocol": "zibo", "listen_port": 30088, "bind_ip": "0.0.0.0",
                   "web_port": 8090, "auto_reply": True}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] config.json 解析失败，使用默认配置: {e}")
    return _DEFAULT_CONFIG.copy()

config = load_config()

def load_protocol(protocol_name: str) -> ProtocolBase:
    """动态加载协议插件"""
    try:
        module = importlib.import_module(f"protocols.{protocol_name}")
    except ModuleNotFoundError:
        # 扫描 protocols/ 目录，列出可用协议供用户参考
        protocols_dir = os.path.join(SCRIPT_DIR, "protocols")
        available = []
        if os.path.isdir(protocols_dir):
            available = [f[:-3] for f in os.listdir(protocols_dir)
                         if f.endswith(".py") and not f.startswith("_")]
        hint = f"可用协议: {', '.join(available)}" if available else "protocols/ 目录为空"
        print(f"[错误] 找不到协议插件 'protocols.{protocol_name}'\n{hint}\n"
              f"请修改 config.json 中的 \"protocol\" 字段")
        sys.exit(1)
    # 找到 ProtocolBase 的子类并实例化
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (isinstance(attr, type)
                and issubclass(attr, ProtocolBase)
                and attr is not ProtocolBase):
            return attr()
    raise ImportError(f"protocols.{protocol_name} 中未找到 ProtocolBase 子类")

protocol = load_protocol(config["protocol"])

# ==================== 路径 ====================
HTML_FILE = os.path.join(SCRIPT_DIR, "mock_server.html")
RULES_FILE = os.path.join(SCRIPT_DIR, f"rules_{config['protocol']}.json")
LOGS_FILE = os.path.join(SCRIPT_DIR, f"logs_{config['protocol']}.json")

# ==================== 全局状态 ====================
class ServerState:
    def __init__(self):
        self.udp_running = False
        self.udp_socket = None
        self.listen_port = config.get("listen_port", 30088)
        self.bind_ip = config.get("bind_ip", "0.0.0.0")
        self.auto_reply = config.get("auto_reply", True)
        self.rules = []
        self.log_entries = []
        self.max_logs = 200
        self.sse_clients = []
        self.lock = threading.Lock()
        self.packet_count = 0
        self.ack_count = 0
        self._save_timer = 0
        self.pending_commands = []
        self.command_seq = 0
        # ACK 抑制测试：开启后前 N 次不回 ACK，第 N+1 次正常回复并自动关闭
        self.ack_suppress_enabled = False
        self.ack_suppress_count = 3       # 用户设定的抑制次数
        self.ack_suppress_current = 0     # 当前已抑制次数
        self.load_rules()
        self.load_logs()

    def load_rules(self):
        if os.path.exists(RULES_FILE):
            try:
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    self.rules = json.load(f)
            except Exception:
                self.rules = protocol.get_default_rules()
        else:
            self.rules = protocol.get_default_rules()
            self.save_rules()

    def save_rules(self):
        try:
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(self.rules, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_logs(self):
        if os.path.exists(LOGS_FILE):
            try:
                with open(LOGS_FILE, "r", encoding="utf-8") as f:
                    self.log_entries = json.load(f)
            except Exception:
                self.log_entries = []

    def save_logs(self):
        try:
            with open(LOGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.log_entries, f, ensure_ascii=False)
        except Exception:
            pass

    def add_log(self, entry):
        with self.lock:
            self.log_entries.append(entry)
            if len(self.log_entries) > self.max_logs:
                self.log_entries = self.log_entries[-self.max_logs:]
            self._save_timer += 1
            need_save = (self._save_timer % 5 == 0)
            for q in self.sse_clients:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    pass
        if need_save:
            self.save_logs()

    def add_sse_client(self):
        q = queue.Queue(maxsize=100)
        with self.lock:
            self.sse_clients.append(q)
        return q

    def remove_sse_client(self, q):
        with self.lock:
            if q in self.sse_clients:
                self.sse_clients.remove(q)

state = ServerState()

# ==================== UDP 监听线程 ====================

def udp_listener():
    # 捕获 socket 引用到局部变量，防止 stop/restart 后旧线程竞争新 socket
    sock = state.udp_socket
    while state.udp_running:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError as e:
            # Windows: 向已关闭端口回复 ACK 后，内核返回 ICMP port unreachable，
            # 下次 recvfrom 抛 WSAECONNRESET(10054)。忽略并继续监听。
            win_err = getattr(e, 'winerror', None)
            if win_err == 10054:
                continue
            # 其他 OSError（socket 被关闭或替换），退出本线程
            # stop_udp_listener 会先设 udp_running=False，新线程用新 socket
            break

        # 顶层异常保护：协议插件任何未预期异常不应拖垮监听线程
        try:
            with state.lock:
                state.packet_count += 1
                packet_num = state.packet_count

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 调用协议插件解析上传帧
            info, error = protocol.parse_upload(data)

            if info is None:
                entry = {
                    "type": "error",
                    "timestamp": timestamp,
                    "packet_num": packet_num,
                    "source_ip": addr[0],
                    "source_port": addr[1],
                    "raw_hex": bytes_to_hex(data),
                    "frame_length": len(data),
                    "error": error
                }
                state.add_log(entry)
                continue

            # 构造字段详情
            field_details = []
            for name, val in info["fields"].items():
                field_details.append({"name": name, "hex": bytes_to_hex(val), "length": len(val)})

            entry = {
                "type": "upload",
                "timestamp": timestamp,
                "packet_num": packet_num,
                "source_ip": addr[0],
                "source_port": addr[1],
                "raw_hex": bytes_to_hex(data),
                "frame_length": len(data),
                "function_code": info["function_code"],
                "function_name": info["function_name"],
                "meter_address_logical": info["meter_address_logical"],
                "imei": info.get("imei", ""),
                "sequence": info["sequence"],
                "rsrp": info.get("rsrp", 0),
                "snr": info.get("snr", 0),
                "data_length": info["data_length"],
                "fields": field_details,
                "checksum_valid": True
            }

            # 检查 ACK 抑制
            should_suppress = False
            suppressed_num = 0
            with state.lock:
                if state.ack_suppress_enabled:
                    if state.ack_suppress_current < state.ack_suppress_count:
                        state.ack_suppress_current += 1
                        should_suppress = True
                        suppressed_num = state.ack_suppress_current
                    else:
                        # 达到抑制次数，重置并自动关闭
                        state.ack_suppress_current = 0
                        state.ack_suppress_enabled = False

            # 匹配规则并回复 ACK
            ack_entry = None
            suppress_entry = None
            if should_suppress:
                suppress_entry = {
                    "type": "ack_suppress",
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "packet_num": packet_num,
                    "source_ip": addr[0],
                    "source_port": addr[1],
                    "sequence": info["sequence"],
                    "function_name": info["function_name"],
                    "suppressed_num": suppressed_num,
                    "suppress_count": state.ack_suppress_count,
                }
            elif state.auto_reply:
                for rule in state.rules:
                    if not rule.get("enabled", True):
                        continue
                    if rule["match_function_code"] == info["function_code"]:
                        ack_frame = protocol.build_ack(info, rule)
                        if ack_frame:
                            try:
                                sock.sendto(ack_frame, addr)
                                with state.lock:
                                    state.ack_count += 1

                                meta = protocol.get_frame_meta(ack_frame)
                                ack_entry = {
                                    "type": "ack",
                                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "target_ip": addr[0],
                                    "target_port": addr[1],
                                    "raw_hex": bytes_to_hex(ack_frame),
                                    "frame_length": len(ack_frame),
                                    "ack_function_code": rule["ack_function_code"],
                                    "ack_name": rule["name"],
                                    "data_length": meta["data_length"],
                                    "checksum": meta["checksum"],
                                    "checksum_calculated": meta["checksum_calculated"],
                                    "rule_id": rule["id"],
                                    "sequence": info["sequence"]
                                }
                            except Exception as e:
                                ack_entry = {
                                    "type": "ack_error",
                                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "error": str(e),
                                    "rule_id": rule["id"]
                                }
                        break

            state.add_log(entry)
            if ack_entry:
                state.add_log(ack_entry)
            if suppress_entry:
                state.add_log(suppress_entry)

            # ===== 发送预设指令（跟 ACK 一起回复给终端）=====
            with state.lock:
                cmds_to_send = list(state.pending_commands)
                state.pending_commands.clear()

            for cmd in cmds_to_send:
                try:
                    cmd_frame = protocol.build_command(info, cmd["func_code"], hex_to_bytes(cmd["data_hex"]))
                    sock.sendto(cmd_frame, addr)

                    meta = protocol.get_frame_meta(cmd_frame)
                    cmd_entry = {
                        "type": "command",
                        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "target_ip": addr[0],
                        "target_port": addr[1],
                        "raw_hex": bytes_to_hex(cmd_frame),
                        "frame_length": len(cmd_frame),
                        "func_code": cmd["func_code"],
                        "func_name": cmd["func_name"],
                        "data_hex": cmd["data_hex"],
                        "data_length": meta["data_length"],
                        "checksum": meta["checksum"],
                        "checksum_calculated": meta["checksum_calculated"],
                        "sequence": info["sequence"],
                        "cmd_id": cmd["id"]
                    }
                    state.add_log(cmd_entry)
                except Exception as e:
                    err_entry = {
                        "type": "ack_error",
                        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "error": f"指令发送失败: {e}",
                        "cmd_id": cmd["id"]
                    }
                    state.add_log(err_entry)
        except Exception as e:
            # 兜底：记录异常帧但保持监听线程存活
            import traceback
            with state.lock:
                state.packet_count += 1
                packet_num = state.packet_count
            state.add_log({
                "type": "error",
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "packet_num": packet_num,
                "source_ip": addr[0] if 'addr' in dir() else "?",
                "source_port": addr[1] if 'addr' in dir() else 0,
                "raw_hex": bytes_to_hex(data) if 'data' in dir() else "",
                "frame_length": len(data) if 'data' in dir() else 0,
                "error": f"处理异常: {type(e).__name__}: {e}"
            })

def start_udp_listener():
    if state.udp_running:
        return False, "已在运行中"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((state.bind_ip, state.listen_port))
        sock.settimeout(1.0)
        state.udp_socket = sock
        state.udp_running = True
        thread = threading.Thread(target=udp_listener, daemon=True)
        thread.start()
        return True, f"UDP监听已启动: {state.bind_ip}:{state.listen_port}"
    except Exception as e:
        return False, f"启动失败: {e}"

def stop_udp_listener():
    state.udp_running = False
    if state.udp_socket:
        try:
            state.udp_socket.close()
        except Exception:
            pass
        state.udp_socket = None
    return True, "UDP监听已停止"

# ==================== HTTP 服务器 ====================
http_server = None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/state":
            self._serve_json(self._get_state())
        elif path == "/api/rules":
            self._serve_json(state.rules)
        elif path == "/api/logs":
            with state.lock:
                self._serve_json(state.log_entries[-100:])
        elif path == "/api/pending":
            with state.lock:
                self._serve_json(list(state.pending_commands))
        elif path == "/api/commands":
            # 返回协议支持的指令定义（供前端动态渲染）
            self._serve_json({
                "protocol_name": protocol.name,
                "commands": protocol.get_command_definitions()
            })
        elif path == "/api/protocol":
            # 返回当前协议信息
            self._serve_json({"name": protocol.name})
        elif path == "/events":
            self._handle_sse()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""

        if path == "/api/start":
            try:
                data = json.loads(body) if body else {}
                if "port" in data:
                    state.listen_port = int(data["port"])
                if "bind_ip" in data:
                    state.bind_ip = data["bind_ip"]
                if "auto_reply" in data:
                    state.auto_reply = data["auto_reply"]
            except Exception:
                pass
            ok, msg = start_udp_listener()
            self._serve_json({"ok": ok, "msg": msg})
        elif path == "/api/stop":
            ok, msg = stop_udp_listener()
            self._serve_json({"ok": ok, "msg": msg})
        elif path == "/api/rules":
            try:
                new_rules = json.loads(body)
                state.rules = new_rules
                state.save_rules()
                self._serve_json({"ok": True, "msg": "规则已保存"})
            except Exception as e:
                self._serve_json({"ok": False, "msg": f"保存失败: {e}"})
        elif path == "/api/send":
            try:
                data = json.loads(body)
                hex_str = data.get("hex", "")
                target_ip = data.get("ip", "")
                target_port = int(data.get("port", 0))
                frame = hex_to_bytes(hex_str)
                if not target_ip or not target_port:
                    self._serve_json({"ok": False, "msg": "参数不完整"})
                elif not state.udp_socket:
                    self._serve_json({"ok": False, "msg": "UDP监听未启动"})
                else:
                    try:
                        state.udp_socket.sendto(frame, (target_ip, target_port))
                        self._serve_json({"ok": True, "msg": f"已发送{len(frame)}字节到{target_ip}:{target_port}"})
                    except OSError:
                        self._serve_json({"ok": False, "msg": "UDP监听未启动或已关闭"})
            except Exception as e:
                self._serve_json({"ok": False, "msg": str(e)})
        elif path == "/api/pending/add":
            try:
                data = json.loads(body)
                func_code = int(data.get("func_code", 0))
                params = data.get("params", {})
                cmd_data, err = protocol.build_command_data(func_code, params)
                if err:
                    self._serve_json({"ok": False, "msg": err})
                else:
                    with state.lock:
                        state.command_seq += 1
                        cmd = {
                            "id": f"cmd_{state.command_seq}",
                            "func_code": func_code,
                            "func_name": protocol.get_cmd_name(func_code),
                            "params": params,
                            "data_hex": bytes_to_hex(cmd_data),
                            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        state.pending_commands.append(cmd)
                    self._serve_json({"ok": True, "msg": "预设指令已添加", "cmd": cmd})
            except Exception as e:
                self._serve_json({"ok": False, "msg": str(e)})
        elif path == "/api/pending/remove":
            try:
                data = json.loads(body)
                cmd_id = data.get("id", "")
                with state.lock:
                    state.pending_commands = [c for c in state.pending_commands if c["id"] != cmd_id]
                self._serve_json({"ok": True, "msg": "已删除"})
            except Exception as e:
                self._serve_json({"ok": False, "msg": str(e)})
        elif path == "/api/pending/clear":
            with state.lock:
                state.pending_commands.clear()
            self._serve_json({"ok": True, "msg": "已清空预设"})
        elif path == "/api/logs/clear":
            with state.lock:
                state.log_entries.clear()
            state.save_logs()
            self._serve_json({"ok": True, "msg": "日志已清空"})
        elif path == "/api/ack-suppress":
            try:
                data = json.loads(body) if body else {}
                enabled = bool(data.get("enabled", False))
                count = int(data.get("count", 3))
                if count < 1:
                    count = 1
                with state.lock:
                    state.ack_suppress_enabled = enabled
                    state.ack_suppress_count = count
                    state.ack_suppress_current = 0
                self._serve_json({"ok": True, "msg": f"ACK抑制已{'开启' if enabled else '关闭'}，抑制次数{count}"})
            except Exception as e:
                self._serve_json({"ok": False, "msg": str(e)})
        elif path == "/api/shutdown":
            stop_udp_listener()
            self._serve_json({"ok": True, "msg": "服务正在关闭..."})
            def _do_shutdown():
                time.sleep(0.5)
                if http_server:
                    http_server.shutdown()
            threading.Thread(target=_do_shutdown, daemon=True).start()
        else:
            self.send_error(404)

    def _serve_html(self):
        html_path = HTML_FILE
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except FileNotFoundError:
            self.send_error(404, "HTML文件未找到")

    def _serve_json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _get_state(self):
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

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = state.add_sse_client()
        try:
            while True:
                try:
                    entry = q.get(timeout=15)
                    data = json.dumps(entry, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            state.remove_sse_client(q)

def main():
    global http_server
    port = config.get("web_port", 8090)

    # 注册退出时保存日志，覆盖 KeyboardInterrupt / sys.exit 等所有退出路径
    atexit.register(state.save_logs)

    server = None
    for attempt in range(10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            server.daemon_threads = True
            http_server = server
            break
        except OSError as e:
            if attempt < 9:
                print(f"端口 {port} 被占用，等待重试 ({attempt+1}/10)... {e}")
                time.sleep(1)
            else:
                print(f"端口 {port} 启动失败: {e}")
                print(f"请等待几秒后重试，或修改 config.json 中的 web_port")
                # 非交互环境（VBS隐藏控制台启动）直接退出，避免 input() 无限阻塞
                if sys.stdin.isatty():
                    input("按回车键退出...")
                sys.exit(1)

    print(f"{protocol.name} Mock Server 已启动")
    print(f"浏览器访问: http://localhost:{port}")
    print(f"按 Ctrl+C 退出")

    webbrowser.open(f"http://localhost:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...")
        stop_udp_listener()
        state.save_logs()
        server.shutdown()

if __name__ == "__main__":
    main()
