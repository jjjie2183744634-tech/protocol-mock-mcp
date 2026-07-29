#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
协议抽象基类 — 所有协议插件必须继承此类并实现抽象方法。

设计思路（HAL 抽象层思维）：
  框架层（server.py）只调用本基类定义的接口，不包含任何协议细节。
  每家水务公司的协议写成单独的插件文件（protocols/xxx.py），
  继承本基类并实现所有抽象方法。
  换协议只需写一个新插件文件 + config.json 改一行，框架和网页不动。

新增协议步骤：
  1. 阅读 protocol_base.py 了解接口定义
  2. 创建 protocols/your_protocol.py，继承 ProtocolBase
  3. 实现所有 @abstractmethod
  4. config.json 改 "protocol": "your_protocol"
  5. 重启服务
"""

from abc import ABC, abstractmethod


# ==================== 共享工具函数 ====================
# 框架和协议插件都需要用到的通用函数，放这里统一导入

def bytes_to_hex(data: bytes) -> str:
    """bytes 转可读 Hex 字符串，如 b'\\x68\\x04' → '68 04'"""
    return " ".join(f"{b:02X}" for b in data)


def hex_to_bytes(hex_str: str) -> bytes:
    """Hex 字符串转 bytes，如 '68 04' → b'\\x68\\x04'，容错空格/逗号/0x前缀"""
    cleaned = hex_str.replace(" ", "").replace("0x", "").replace(",", "")
    return bytes(int(cleaned[i:i+2], 16) for i in range(0, len(cleaned), 2))


class ProtocolBase(ABC):
    """协议抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """协议名称，如 '淄博协议'、'上海为源协议'"""
        ...

    # ==================== 帧解析 ====================

    @abstractmethod
    def parse_upload(self, data: bytes) -> tuple:
        """解析终端上传帧。

        Args:
            data: UDP 收到的原始字节

        Returns:
            (info_dict, error_msg)
            - 成功: (info_dict, None)
            - 失败: (None, "错误描述")

            info_dict 必须包含以下标准化字段（供框架和前端统一处理）:
            {
                "function_code": int,        # 功能码（原始值）
                "function_name": str,        # 功能码可读名称
                "meter_address": bytes,      # 水表地址（原始字节）
                "meter_address_logical": str,# 水表逻辑地址（可读字符串）
                "sequence": int,             # 序列号
                "imei": str,                 # IMEI（如有）
                "rsrp": int,                 # 信号强度（如有）
                "snr": int,                  # 信噪比（如有）
                "data_length": int,          # 数据域长度
                "data": bytes,               # 数据域原始字节
                "fields": dict,              # 解析出的字段 {字段名: bytes}
                "checksum_valid": bool,      # 校验是否通过
            }
        """
        ...

    @abstractmethod
    def build_ack(self, upload_info: dict, rule: dict) -> bytes:
        """根据上传帧信息和 ACK 规则构造 ACK 帧。

        Args:
            upload_info: parse_upload 返回的 info_dict
            rule: ACK 规则，包含 match_function_code, ack_function_code, ack_data_fields 等

        Returns:
            完整的 ACK 帧字节
        """
        ...

    @abstractmethod
    def build_command(self, upload_info: dict, func_code: int, data: bytes) -> bytes:
        """构造平台下发指令帧。

        Args:
            upload_info: 终端上传帧信息（用于提取地址、序列号等）
            func_code: 功能码
            data: 数据域字节（由 build_command_data 生成）

        Returns:
            完整的指令帧字节
        """
        ...

    @abstractmethod
    def build_command_data(self, func_code: int, params: dict) -> tuple:
        """根据功能码和用户参数构造数据域。

        Args:
            func_code: 功能码
            params: 前端传来的参数字典

        Returns:
            (data_bytes, error_msg)
            - 成功: (data_bytes, None)
            - 失败: (b"", "错误描述")
        """
        ...

    # ==================== 校验 ====================

    @abstractmethod
    def calc_checksum(self, data: bytes) -> int:
        """计算校验和。

        不同协议校验算法不同：
        - 淄博: 累加和 & 0xFF
        - 上海为源: 可能用 CRC16
        - 其他: 视协议而定

        Returns:
            校验值（int）
        """
        ...

    @abstractmethod
    def get_frame_meta(self, frame: bytes) -> dict:
        """从帧中提取元信息（供日志展示）。

        框架在记录 ACK/指令日志时需要校验和、数据长度等信息，
        但这些信息在帧中的位置因协议而异，因此交给协议插件解析。

        Returns:
            {
                "checksum": int,              # 帧中的校验值
                "checksum_calculated": int,   # 重新计算的校验值
                "data_length": int,           # 数据域长度
            }
        """
        ...

    # ==================== 协议元信息 ====================

    @abstractmethod
    def get_command_definitions(self) -> list:
        """返回此协议支持的指令定义，供前端动态渲染指令表单。

        返回格式:
        [
            {
                "func_code": 52,
                "name": "重启/恢复出厂",
                "params": [
                    {
                        "name": "action",
                        "label": "动作类型",
                        "type": "select",      # select | number | text | checkbox-group
                        "options": [            # 仅 type=select/checkbox-group 时有
                            {"value": "reset", "label": "仅重启（数据域 01 00）"},
                            {"value": "factory", "label": "恢复出厂并重启（数据域 00 01）"}
                        ],
                        "default": "reset",
                        "min": None,            # 仅 type=number 时有
                        "max": None,
                    }
                ]
            }
        ]
        """
        ...

    @abstractmethod
    def get_func_name(self, func_code: int) -> str:
        """根据功能码返回可读名称，如 0x65 → '101冻结数据'"""
        ...

    def get_cmd_name(self, func_code: int) -> str:
        """根据指令功能码返回可读名称（下行指令用）。
        默认实现从 get_command_definitions() 查找，协议插件可重写以提升性能。"""
        for cmd in self.get_command_definitions():
            if cmd["func_code"] == func_code:
                return cmd["name"]
        return f"功能码{func_code}"

    @abstractmethod
    def get_default_rules(self) -> list:
        """返回默认 ACK 规则列表（首次启动时写入 JSON 文件）"""
        ...
