#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
淄博协议插件 — 从 zb_mock_server.py 抽出的协议特定逻辑。

帧结构（淄博协议）:
  68 [版本1B] [IMEI 15B] [RSRP 2B] [SNR 2B] [覆盖1B] [CSQ 1B]
     [水表地址 6B] [序列号 1B] [功能码 1B] [加密 1B] [数据长度 2B LE]
     [数据域 N字节] [校验和 1B] 16

校验和: 起始符到数据域末尾的累加和 & 0xFF
"""

import datetime
from protocol_base import ProtocolBase, bytes_to_hex, hex_to_bytes

# ==================== 淄博协议常量 ====================
FRAME_HEAD = 0x68
FRAME_TAIL = 0x16
FUNC_FREEZE = 0x65        # 101 冻结数据
FUNC_PERIOD = 0x66        # 102 周期数据
FUNC_ALARM = 0x67         # 103 突发报警
FUNC_PARAM_REPLY = 0x6B   # 107 参数查询应答
FUNC_REBOOT_REPLY = 0x98  # 152 重启/恢复出厂应答

# 参数查询 Tag 名称映射（协议文档 7.1 节）
_PARAM_TAG_NAMES = {
    0x0D: "周期采集间隔(5min)",
    0x10: "上报重发次数",
    0x11: "重发延时(min)",
    0x12: "上传间隔(h)",
    0x15: "服务器地址",
    0x16: "APN",
}


def _u16le(data, offset):
    """读取小端 16 位无符号整数"""
    return data[offset] | (data[offset + 1] << 8)


def _s16le(data, offset):
    """读取小端 16 位有符号整数"""
    val = _u16le(data, offset)
    return val - 0x10000 if val >= 0x8000 else val


def _get_platform_time():
    """获取平台时间（6字节: 年-2000, 月, 日, 时, 分, 秒）"""
    now = datetime.datetime.now()
    return bytes([now.year - 2000, now.month, now.day, now.hour, now.minute, now.second])


class ZiboProtocol(ProtocolBase):
    """淄博远传表平台通讯协议"""

    @property
    def name(self) -> str:
        return "淄博协议"

    # ==================== 帧解析 ====================

    def parse_upload(self, data: bytes) -> tuple:
        """解析淄博协议上传帧"""
        if len(data) < 36:
            return None, f"帧长{len(data)}字节，短于最小36字节"
        if data[0] != FRAME_HEAD:
            return None, f"起始符应为68，实际{data[0]:02X}"

        func_code = data[30]
        if func_code not in (FUNC_FREEZE, FUNC_PERIOD, FUNC_ALARM,
                             FUNC_PARAM_REPLY, FUNC_REBOOT_REPLY):
            return None, f"不支持的功能码{func_code}"

        data_length = _u16le(data, 32)
        expected_length = 36 + data_length
        checksum_offset = 34 + data_length

        if len(data) != expected_length:
            return None, f"帧长{len(data)}≠期望{expected_length}"

        received_cs = data[checksum_offset]
        calculated_cs = self.calc_checksum(data[:checksum_offset])
        if received_cs != calculated_cs:
            return None, f"校验和不匹配: 收到{received_cs:02X} 计算{calculated_cs:02X}"

        if data[checksum_offset + 1] != FRAME_TAIL:
            return None, f"结束符应为16，实际{data[checksum_offset+1]:02X}"

        meter_addr = data[23:29]
        meter_logic = bytes_to_hex(meter_addr[::-1])
        frame_data = data[34:checksum_offset]

        # 解析数据域字段
        fields = {}
        if func_code == FUNC_FREEZE:
            if len(frame_data) >= 3:
                fields["freeze_date"] = frame_data[0:3]
        elif func_code == FUNC_PERIOD:
            if len(frame_data) >= 8:
                fields["total_packets"] = bytes([frame_data[0]])
                fields["current_packet"] = bytes([frame_data[1]])
                fields["start_date"] = frame_data[2:5]
                fields["start_time"] = frame_data[5:8]
        elif func_code == FUNC_ALARM:
            if len(frame_data) >= 3:
                fields["alarm_date"] = frame_data[0:3]
        elif func_code == FUNC_PARAM_REPLY:
            # 107 参数查询应答：TLV 格式
            # 返回数量(1B) + [Tag(1B) + Length(1B) + Value(nB)] * N
            if len(frame_data) >= 1:
                count = frame_data[0]
                offset = 1
                for _ in range(count):
                    if offset + 2 > len(frame_data):
                        break
                    tag = frame_data[offset]
                    tlv_len = frame_data[offset + 1]
                    if offset + 2 + tlv_len > len(frame_data):
                        break
                    value = frame_data[offset + 2:offset + 2 + tlv_len]
                    tag_name = _PARAM_TAG_NAMES.get(tag, f"Tag{tag}")
                    fields[f"参数{tag}_{tag_name}"] = value
                    offset += 2 + tlv_len
        elif func_code == FUNC_REBOOT_REPLY:
            # 152 重启/恢复出厂应答：固定2字节
            # 字节1: 重启(0/1)  字节2: 恢复出厂(0/1)
            if len(frame_data) >= 2:
                fields["重启"] = bytes([frame_data[0]])
                fields["恢复出厂"] = bytes([frame_data[1]])

        info = {
            "version": data[1],
            "imei": data[2:17].decode("ascii", errors="replace"),
            "rsrp": _s16le(data, 17),
            "snr": _s16le(data, 19),
            "coverage_level": data[21],
            "csq": data[22],
            "meter_address": meter_addr,
            "meter_address_logical": meter_logic,
            "sequence": data[29],
            "function_code": func_code,
            "function_name": self.get_func_name(func_code),
            "encryption": data[31],
            "data_length": data_length,
            "data": frame_data,
            "fields": fields,
            "checksum_valid": True,
        }
        return info, None

    # ==================== 帧构造 ====================

    def build_ack(self, upload_info: dict, rule: dict) -> bytes:
        """根据规则构造 ACK 帧，校验和实时计算"""
        # 构造 ACK 数据域
        ack_data = b""
        for field_def in rule.get("ack_data_fields", []):
            if field_def["type"] == "fixed":
                ack_data += hex_to_bytes(field_def["value"])
            elif field_def["type"] == "copy":
                field_name = field_def["field"]
                field_bytes = upload_info["fields"].get(field_name, b"\x00" * field_def.get("length", 1))
                ack_data += field_bytes[:field_def.get("length", len(field_bytes))]

        ack_func_code = rule["ack_function_code"]
        platform_time = _get_platform_time()
        data_length = len(ack_data)
        data_length_bytes = data_length.to_bytes(2, byteorder="little")

        frame = bytes([
            FRAME_HEAD,
            upload_info["version"],
        ]) + upload_info["meter_address"] + platform_time + bytes([
            upload_info["sequence"],
            ack_func_code,
            upload_info["encryption"],
        ]) + data_length_bytes + ack_data

        checksum = self.calc_checksum(frame)
        frame += bytes([checksum, FRAME_TAIL])
        return frame

    def build_command(self, upload_info: dict, func_code: int, data: bytes) -> bytes:
        """构造平台下发指令帧（淄博协议下行帧），校验和实时计算。
        版本字节使用0x02（客户平台下行帧版本），终端prvZBFrameCheck已兼容0x02/0x04。"""
        version = 0x02
        platform_time = _get_platform_time()
        seq = upload_info["sequence"]
        encryption = 0x00
        data_length = len(data)
        data_length_bytes = data_length.to_bytes(2, byteorder="little")

        frame = bytes([
            FRAME_HEAD,
            version,
        ]) + upload_info["meter_address"] + platform_time + bytes([
            seq,
            func_code,
            encryption,
        ]) + data_length_bytes + data

        checksum = self.calc_checksum(frame)
        frame += bytes([checksum, FRAME_TAIL])
        return frame

    def build_command_data(self, func_code: int, params: dict) -> tuple:
        """根据功能码和参数构造数据域"""
        try:
            if func_code == 52:
                action = params.get("action", "reset")
                if action == "reset":
                    return bytes([0x01, 0x00]), None  # [重启=1, 恢复出厂=0]
                elif action == "factory":
                    return bytes([0x00, 0x01]), None  # [重启=0, 恢复出厂=1]
                else:
                    return b"", "无效的动作类型"
            elif func_code == 7:
                tags = params.get("tags", [])
                if not tags:
                    return b"", "至少选择一个Tag"
                tag_bytes = bytes([int(t) for t in tags])
                return bytes([len(tag_bytes)]) + tag_bytes, None
            elif func_code == 9:
                days = int(params.get("days", 1))
                total = int(params.get("total_packets", 1))
                current = int(params.get("current_packet", 1))
                if not (1 <= days <= 7):
                    return b"", "查询天数应在1-7之间"
                if not (1 <= total <= 255):
                    return b"", "总包数应在1-255之间"
                if not (1 <= current <= total):
                    return b"", "当前包号应小于等于总包数"
                return bytes([days, total, current]), None
            elif func_code == 71:
                raw_hex = params.get("raw_hex", "").strip()
                if not raw_hex:
                    return b"", "透传数据不能为空"
                data = hex_to_bytes(raw_hex)
                if len(data) == 0:
                    return b"", "透传数据解析失败"
                return data, None
            else:
                return b"", f"不支持的功能码{func_code}"
        except Exception as e:
            return b"", str(e)

    # ==================== 校验 ====================

    def calc_checksum(self, data: bytes) -> int:
        """淄博协议校验和: 累加和 & 0xFF"""
        return sum(data) & 0xFF

    def get_frame_meta(self, frame: bytes) -> dict:
        """从淄博帧中提取校验和、数据长度等元信息"""
        data_length = _u16le(frame, 17)
        checksum_offset = 19 + data_length
        return {
            "checksum": frame[checksum_offset],
            "checksum_calculated": self.calc_checksum(frame[:checksum_offset]),
            "data_length": data_length,
        }

    # ==================== 协议元信息 ====================

    _FUNC_NAMES = {
        FUNC_FREEZE: "101冻结数据",
        FUNC_PERIOD: "102周期数据",
        FUNC_ALARM: "103突发报警",
        FUNC_PARAM_REPLY: "107参数查询应答",
        FUNC_REBOOT_REPLY: "152重启/恢复出厂应答",
    }

    _CMD_NAMES = {
        52: "52-重启/恢复出厂",
        7:  "7-参数查询",
        9:  "9-周期查询",
        71: "71-透传",
    }

    def get_func_name(self, func_code: int) -> str:
        return self._FUNC_NAMES.get(func_code, f"功能码{func_code}")

    def get_cmd_name(self, func_code: int) -> str:
        """指令功能码可读名称（下行指令用）"""
        return self._CMD_NAMES.get(func_code, f"功能码{func_code}")

    def get_command_definitions(self) -> list:
        """返回淄博协议支持的指令定义，供前端动态渲染"""
        return [
            {
                "func_code": 52,
                "name": "重启/恢复出厂",
                "params": [
                    {
                        "name": "action",
                        "label": "动作类型",
                        "type": "select",
                        "options": [
                            {"value": "reset", "label": "仅重启（数据域 01 00）"},
                            {"value": "factory", "label": "恢复出厂并重启（数据域 00 01）"}
                        ],
                        "default": "reset",
                    }
                ]
            },
            {
                "func_code": 7,
                "name": "参数查询",
                "params": [
                    {
                        "name": "tags",
                        "label": "查询Tag（多选）",
                        "type": "checkbox-group",
                        "options": [
                            {"value": "13", "label": "13-周期采集间隔(5min)"},
                            {"value": "16", "label": "16-上报重发次数"},
                            {"value": "17", "label": "17-重发延时(min)"},
                            {"value": "18", "label": "18-上传间隔(h)"},
                            {"value": "21", "label": "21-服务器地址"},
                            {"value": "22", "label": "22-APN"},
                        ],
                    }
                ]
            },
            {
                "func_code": 9,
                "name": "周期查询",
                "params": [
                    {
                        "name": "days", "label": "天数(1-7)", "type": "number",
                        "default": 1, "min": 1, "max": 7,
                    },
                    {
                        "name": "total_packets", "label": "总包数", "type": "number",
                        "default": 1, "min": 1, "max": 255,
                    },
                    {
                        "name": "current_packet", "label": "当前包", "type": "number",
                        "default": 1, "min": 1, "max": 255,
                    },
                ]
            },
            {
                "func_code": 71,
                "name": "透传",
                "params": [
                    {
                        "name": "raw_hex", "label": "CJ188帧（Hex）", "type": "text",
                        "default": "", "placeholder": "68 08 08 68 …",
                    }
                ]
            },
        ]

    def get_default_rules(self) -> list:
        """返回默认 ACK 规则"""
        return [
            {
                "id": "rule_freeze",
                "name": "冻结ACK（应答101）",
                "enabled": True,
                "match_function_code": 101,
                "ack_function_code": 1,
                "ack_data_fields": [
                    {"type": "fixed", "value": "00", "label": "执行结果=成功"},
                    {"type": "copy", "field": "freeze_date", "length": 3, "label": "冻结日期"}
                ]
            },
            {
                "id": "rule_period",
                "name": "周期ACK（应答102）",
                "enabled": True,
                "match_function_code": 102,
                "ack_function_code": 2,
                "ack_data_fields": [
                    {"type": "fixed", "value": "00", "label": "执行结果=成功"},
                    {"type": "copy", "field": "total_packets", "length": 1, "label": "总包数"},
                    {"type": "copy", "field": "current_packet", "length": 1, "label": "当前包号"},
                    {"type": "copy", "field": "start_date", "length": 3, "label": "起始日期"},
                    {"type": "copy", "field": "start_time", "length": 3, "label": "起始时间"}
                ]
            }
        ]
