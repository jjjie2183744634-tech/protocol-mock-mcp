# 协议插件开发指南

本指南介绍如何为本项目编写新的协议插件。整个过程**只需写一个 Python 文件 + 改一行配置**，框架代码和网页界面完全不用动。

---

## 设计理念

项目采用 **HAL（硬件抽象层）思维**：

- `server.py` — 协议无关的通用框架（UDP 监听、HTTP 服务、SSE 推送、日志管理）
- `protocol_base.py` — 抽象基类，定义协议插件必须实现的接口
- `protocols/xxx.py` — 具体协议实现，继承基类并实现所有抽象方法

框架通过 `importlib` 动态加载插件，`config.json` 中 `"protocol"` 字段决定使用哪个协议。框架层只调用基类定义的接口，不包含任何协议细节。

```
┌─────────────────────────────────────────────┐
│              MCP Server (mcp_server.py)       │
│         AI 通过 MCP 协议调用 16 个工具          │
├─────────────────────────────────────────────┤
│            框架层 (server.py)                  │
│    UDP 监听 / HTTP 服务 / SSE / 日志管理        │
│         只调用 ProtocolBase 接口               │
├─────────────────────────────────────────────┤
│          协议插件 (protocols/xxx.py)           │
│   帧解析 / ACK 构造 / 指令构造 / 校验计算       │
└─────────────────────────────────────────────┘
```

---

## 快速开始：4 步添加新协议

以添加"上海为源协议"为例：

### 第 1 步：创建插件文件

在 `protocols/` 目录下新建 `shanghai.py`：

```
protocols/
├── __init__.py
├── zibo.py          # 已有：淄博协议
└── shanghai.py      # 新增：上海为源协议
```

### 第 2 步：继承 ProtocolBase 并实现接口

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
上海为源协议插件

帧结构（示例，请替换为实际协议）:
  FE [地址 4B] [功能码 1B] [数据长度 1B] [数据域 N字节] [CRC16 2B] EF
"""

from protocol_base import ProtocolBase, bytes_to_hex, hex_to_bytes


class ShanghaiProtocol(ProtocolBase):
    """上海为源远传水表协议"""

    @property
    def name(self) -> str:
        return "上海为源协议"

    def parse_upload(self, data: bytes) -> tuple:
        # 见下文详细说明
        ...

    def build_ack(self, upload_info: dict, rule: dict) -> bytes:
        ...

    def build_command(self, upload_info: dict, func_code: int, data: bytes) -> bytes:
        ...

    def build_command_data(self, func_code: int, params: dict) -> tuple:
        ...

    def calc_checksum(self, data: bytes) -> int:
        ...

    def get_frame_meta(self, frame: bytes) -> dict:
        ...

    def get_command_definitions(self) -> list:
        ...

    def get_func_name(self, func_code: int) -> str:
        ...

    def get_default_rules(self) -> list:
        ...
```

### 第 3 步：修改配置

编辑 `config.json`，将 `protocol` 改为你的插件文件名（不含 `.py`）：

```json
{
    "protocol": "shanghai",
    "listen_port": 30088,
    "bind_ip": "0.0.0.0",
    "web_port": 8090,
    "auto_reply": true
}
```

### 第 4 步：重启服务

```bash
python mcp_server.py
```

框架会自动扫描 `protocols/shanghai.py`，找到 `ProtocolBase` 的子类并实例化。如果找不到会列出可用协议提示你：

```
[错误] 找不到协议插件 'protocols.shanghai'
可用协议: zibo, shanghai
请修改 config.json 中的 "protocol" 字段
```

---

## 接口详解

共 9 个必须实现的抽象方法。下面逐个说明参数、返回值和注意事项。

### 1. `name`（属性）

返回协议名称，用于日志和界面显示。

```python
@property
def name(self) -> str:
    return "上海为源协议"
```

### 2. `parse_upload`（帧解析）

解析终端上传的 UDP 数据帧。这是最核心的方法。

**参数：**
- `data: bytes` — UDP 收到的原始字节

**返回：**
- `(info_dict, None)` — 解析成功
- `(None, "错误描述")` — 解析失败

**info_dict 必须包含的标准化字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `function_code` | `int` | 功能码原始值 |
| `function_name` | `str` | 功能码可读名称（调用 `get_func_name`） |
| `meter_address` | `bytes` | 水表地址原始字节 |
| `meter_address_logical` | `str` | 水表逻辑地址（可读字符串） |
| `sequence` | `int` | 序列号 |
| `imei` | `str` | IMEI（如有，否则空字符串） |
| `rsrp` | `int` | 信号强度（如有，否则 0） |
| `snr` | `int` | 信噪比（如有，否则 0） |
| `data_length` | `int` | 数据域长度 |
| `data` | `bytes` | 数据域原始字节 |
| `fields` | `dict` | 解析出的字段 `{字段名: bytes}` |
| `checksum_valid` | `bool` | 校验是否通过 |

**示例（参考已有协议实现）：**

```python
def parse_upload(self, data: bytes) -> tuple:
    if len(data) < 10:
        return None, f"帧长{len(data)}字节，短于最小10字节"
    if data[0] != 0xFE:
        return None, f"起始符应为FE，实际{data[0]:02X}"

    func_code = data[5]
    data_length = data[6]
    expected_len = 7 + data_length + 3  # 头 + 数据 + CRC16 + 尾
    if len(data) != expected_len:
        return None, f"帧长{len(data)}≠期望{expected_len}"

    # 校验
    received_crc = data[7 + data_length] | (data[7 + data_length + 1] << 8)
    calculated_crc = self.calc_checksum(data[:7 + data_length])
    if received_crc != calculated_crc:
        return None, f"CRC不匹配: 收到{received_crc:04X} 计算{calculated_crc:04X}"

    if data[-1] != 0xEF:
        return None, f"结束符应为EF，实际{data[-1]:02X}"

    meter_addr = data[1:5]
    frame_data = data[7:7 + data_length]

    # 解析数据域字段（根据功能码分别处理）
    fields = {}
    if func_code == 0x01:  # 冻结数据
        if len(frame_data) >= 4:
            fields["累计量"] = frame_data[0:4]
    elif func_code == 0x02:  # 周期数据
        if len(frame_data) >= 8:
            fields["总包数"] = bytes([frame_data[0]])
            fields["当前包"] = bytes([frame_data[1]])

    info = {
        "function_code": func_code,
        "function_name": self.get_func_name(func_code),
        "meter_address": meter_addr,
        "meter_address_logical": meter_addr.hex(),
        "sequence": data[5],  # 如果帧中有序列号
        "imei": "",
        "rsrp": 0,
        "snr": 0,
        "data_length": data_length,
        "data": frame_data,
        "fields": fields,
        "checksum_valid": True,
    }
    return info, None
```

> **重要：** `fields` 字典里的 key 会被 `build_ack` 的 `copy` 类型字段引用，所以命名要和 ACK 规则中的一致。

### 3. `build_ack`（构造 ACK 应答帧）

根据上传帧信息和 ACK 规则构造应答帧。

**参数：**
- `upload_info: dict` — `parse_upload` 返回的 info_dict
- `rule: dict` — ACK 规则，结构见 `get_default_rules`

**返回：**
- `bytes` — 完整的 ACK 帧字节

**ACK 规则结构：**

```python
{
    "id": "rule_freeze",
    "name": "冻结ACK",
    "enabled": True,
    "match_function_code": 101,     # 匹配的上传功能码
    "ack_function_code": 1,         # ACK 帧的功能码
    "ack_data_fields": [            # ACK 数据域构成
        {"type": "fixed", "value": "00", "label": "执行结果=成功"},
        {"type": "copy", "field": "freeze_date", "length": 3, "label": "冻结日期"}
    ]
}
```

**ack_data_fields 类型：**
- `"fixed"` — 固定值，`value` 是 Hex 字符串（如 `"00"`、`"01 02"`）
- `"copy"` — 从上传帧字段复制，`field` 对应 `parse_upload` 中 `fields` 的 key

**示例：**

```python
def build_ack(self, upload_info: dict, rule: dict) -> bytes:
    ack_data = b""
    for field_def in rule.get("ack_data_fields", []):
        if field_def["type"] == "fixed":
            ack_data += hex_to_bytes(field_def["value"])
        elif field_def["type"] == "copy":
            field_name = field_def["field"]
            field_bytes = upload_info["fields"].get(
                field_name, b"\x00" * field_def.get("length", 1)
            )
            ack_data += field_bytes[:field_def.get("length", len(field_bytes))]

    ack_func = rule["ack_function_code"]
    meter_addr = upload_info["meter_address"]
    seq = upload_info["sequence"]
    data_len = len(ack_data)

    frame = bytes([0xFE]) + meter_addr + bytes([seq, ack_func, data_len]) + ack_data
    crc = self.calc_checksum(frame)
    frame += crc.to_bytes(2, byteorder="little") + bytes([0xEF])
    return frame
```

> **重要：** 校验和必须实时计算，不能预存。框架在记录日志时会调用 `get_frame_meta` 重新验证。

### 4. `build_command`（构造下发指令帧）

构造平台主动下发给终端的指令帧。

**参数：**
- `upload_info: dict` — 终端上传帧信息（用于提取地址、序列号等）
- `func_code: int` — 指令功能码
- `data: bytes` — 数据域字节（由 `build_command_data` 生成）

**返回：**
- `bytes` — 完整的指令帧

**示例：**

```python
def build_command(self, upload_info: dict, func_code: int, data: bytes) -> bytes:
    meter_addr = upload_info["meter_address"]
    seq = upload_info["sequence"]
    data_len = len(data)

    frame = bytes([0xFE]) + meter_addr + bytes([seq, func_code, data_len]) + data
    crc = self.calc_checksum(frame)
    frame += crc.to_bytes(2, byteorder="little") + bytes([0xEF])
    return frame
```

### 5. `build_command_data`（构造指令数据域）

根据功能码和前端传来的参数构造数据域。

**参数：**
- `func_code: int` — 指令功能码
- `params: dict` — 前端参数（对应 `get_command_definitions` 中定义的表单）

**返回：**
- `(data_bytes, None)` — 成功
- `(b"", "错误描述")` — 失败

**示例：**

```python
def build_command_data(self, func_code: int, params: dict) -> tuple:
    try:
        if func_code == 0x10:  # 阀门控制
            action = params.get("action", "open")
            if action == "open":
                return bytes([0x01]), None
            elif action == "close":
                return bytes([0x00]), None
            else:
                return b"", "无效的阀门动作"
        elif func_code == 0x20:  # 参数查询
            tags = params.get("tags", [])
            if not tags:
                return b"", "至少选择一个Tag"
            return bytes([len(tags)]) + bytes([int(t) for t in tags]), None
        else:
            return b"", f"不支持的功能码{func_code}"
    except Exception as e:
        return b"", str(e)
```

### 6. `calc_checksum`（校验和计算）

每家协议的校验算法不同，交给插件自己实现。

**返回：** `int` — 校验值

**常见算法：**

```python
# 累加和 & 0xFF（示例）
def calc_checksum(self, data: bytes) -> int:
    return sum(data) & 0xFF

# CRC16-Modbus
def calc_checksum(self, data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc

# CRC16-CCITT
def calc_checksum(self, data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc

# XOR 异或校验
def calc_checksum(self, data: bytes) -> int:
    result = 0
    for byte in data:
        result ^= byte
    return result
```

### 7. `get_frame_meta`（提取帧元信息）

从帧字节中提取校验值、数据长度等，供框架记录日志。

**返回：**

```python
{
    "checksum": int,              # 帧中的校验值
    "checksum_calculated": int,   # 重新计算的校验值
    "data_length": int,           # 数据域长度
}
```

**示例：**

```python
def get_frame_meta(self, frame: bytes) -> dict:
    data_length = frame[6]
    checksum_offset = 7 + data_length
    return {
        "checksum": frame[checksum_offset] | (frame[checksum_offset + 1] << 8),
        "checksum_calculated": self.calc_checksum(frame[:checksum_offset]),
        "data_length": data_length,
    }
```

### 8. `get_command_definitions`（指令定义）

返回此协议支持的指令列表，前端会根据这个定义**自动渲染指令表单**。

**返回格式：**

```python
[
    {
        "func_code": 52,           # 功能码（int）
        "name": "重启/恢复出厂",     # 指令名称
        "params": [                # 参数定义
            {
                "name": "action",           # 参数名（前端提交时的 key）
                "label": "动作类型",         # 显示标签
                "type": "select",           # 表单类型
                "options": [                # 仅 select / checkbox-group 有
                    {"value": "reset", "label": "仅重启"},
                    {"value": "factory", "label": "恢复出厂"}
                ],
                "default": "reset",         # 默认值
            }
        ]
    }
]
```

**支持的表单类型：**

| type | 用途 | 额外字段 |
|------|------|----------|
| `select` | 下拉选择 | `options`, `default` |
| `number` | 数字输入 | `default`, `min`, `max` |
| `text` | 文本输入 | `default`, `placeholder` |
| `checkbox-group` | 多选 | `options` |

### 9. `get_func_name`（功能码名称）

将功能码转为可读名称，用于日志和界面显示。

```python
_FUNC_NAMES = {
    0x01: "冻结数据",
    0x02: "周期数据",
    0x10: "阀门控制",
}

def get_func_name(self, func_code: int) -> str:
    return self._FUNC_NAMES.get(func_code, f"功能码{func_code}")
```

### 10. `get_default_rules`（默认 ACK 规则）

首次启动时写入 JSON 文件的默认规则。后续用户可在网页界面修改。

```python
def get_default_rules(self) -> list:
    return [
        {
            "id": "rule_freeze",
            "name": "冻结ACK",
            "enabled": True,
            "match_function_code": 1,    # 匹配上传功能码 0x01
            "ack_function_code": 0x81,   # ACK 功能码
            "ack_data_fields": [
                {"type": "fixed", "value": "00", "label": "执行结果=成功"},
                {"type": "copy", "field": "累计量", "length": 4, "label": "累计量回显"}
            ]
        }
    ]
```

---

## 可选重写方法

### `get_cmd_name`（指令功能码名称）

基类有默认实现，从 `get_command_definitions` 中查找。如果指令较多、性能敏感，可重写为字典查找：

```python
_CMD_NAMES = {
    0x10: "10-阀门控制",
    0x20: "20-参数查询",
}

def get_cmd_name(self, func_code: int) -> str:
    return self._CMD_NAMES.get(func_code, f"功能码{func_code}")
```

---

## 工具函数

`protocol_base.py` 提供了两个共享工具函数，可直接导入使用：

```python
from protocol_base import bytes_to_hex, hex_to_bytes

# bytes 转可读 Hex 字符串
bytes_to_hex(b'\x68\x04')  # → '68 04'

# Hex 字符串转 bytes，容错空格/逗号/0x前缀
hex_to_bytes('68 04')      # → b'\x68\x04'
hex_to_bytes('0x68,0x04')  # → b'\x68\x04'
```

---

## 完整示例：最小协议插件

以下是一个可运行的最小协议插件模板，可以直接复制修改：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XXX协议插件

帧结构:
  AA [地址 4B] [功能码 1B] [数据长度 1B] [数据域 N字节] [XOR 1B] 55
"""

from protocol_base import ProtocolBase, bytes_to_hex, hex_to_bytes

FRAME_HEAD = 0xAA
FRAME_TAIL = 0x55

_FUNC_NAMES = {
    0x01: "冻结数据",
    0x02: "周期数据",
}


class XxxProtocol(ProtocolBase):
    """XXX远传水表协议"""

    @property
    def name(self) -> str:
        return "XXX协议"

    def parse_upload(self, data: bytes) -> tuple:
        if len(data) < 8:
            return None, f"帧长{len(data)}字节，短于最小8字节"
        if data[0] != FRAME_HEAD:
            return None, f"起始符应为AA，实际{data[0]:02X}"

        func_code = data[5]
        data_length = data[6]
        expected_len = 7 + data_length + 2
        if len(data) != expected_len:
            return None, f"帧长{len(data)}≠期望{expected_len}"

        received_xor = data[7 + data_length]
        calculated_xor = self.calc_checksum(data[:7 + data_length])
        if received_xor != calculated_xor:
            return None, f"校验不匹配: 收到{received_xor:02X} 计算{calculated_xor:02X}"

        if data[-1] != FRAME_TAIL:
            return None, f"结束符应为55，实际{data[-1]:02X}"

        meter_addr = data[1:5]
        frame_data = data[7:7 + data_length]

        fields = {}
        if func_code == 0x01 and len(frame_data) >= 4:
            fields["累计量"] = frame_data[0:4]

        info = {
            "function_code": func_code,
            "function_name": self.get_func_name(func_code),
            "meter_address": meter_addr,
            "meter_address_logical": meter_addr.hex(),
            "sequence": 0,
            "imei": "",
            "rsrp": 0,
            "snr": 0,
            "data_length": data_length,
            "data": frame_data,
            "fields": fields,
            "checksum_valid": True,
        }
        return info, None

    def build_ack(self, upload_info: dict, rule: dict) -> bytes:
        ack_data = b""
        for field_def in rule.get("ack_data_fields", []):
            if field_def["type"] == "fixed":
                ack_data += hex_to_bytes(field_def["value"])
            elif field_def["type"] == "copy":
                field_name = field_def["field"]
                field_bytes = upload_info["fields"].get(
                    field_name, b"\x00" * field_def.get("length", 1)
                )
                ack_data += field_bytes[:field_def.get("length", len(field_bytes))]

        ack_func = rule["ack_function_code"]
        meter_addr = upload_info["meter_address"]
        data_len = len(ack_data)

        frame = bytes([FRAME_HEAD]) + meter_addr + bytes([ack_func, data_len]) + ack_data
        checksum = self.calc_checksum(frame)
        frame += bytes([checksum, FRAME_TAIL])
        return frame

    def build_command(self, upload_info: dict, func_code: int, data: bytes) -> bytes:
        meter_addr = upload_info["meter_address"]
        data_len = len(data)

        frame = bytes([FRAME_HEAD]) + meter_addr + bytes([func_code, data_len]) + data
        checksum = self.calc_checksum(frame)
        frame += bytes([checksum, FRAME_TAIL])
        return frame

    def build_command_data(self, func_code: int, params: dict) -> tuple:
        try:
            if func_code == 0x10:
                action = params.get("action", "open")
                if action == "open":
                    return bytes([0x01]), None
                elif action == "close":
                    return bytes([0x00]), None
                return b"", "无效动作"
            return b"", f"不支持的功能码{func_code}"
        except Exception as e:
            return b"", str(e)

    def calc_checksum(self, data: bytes) -> int:
        result = 0
        for byte in data:
            result ^= byte
        return result

    def get_frame_meta(self, frame: bytes) -> dict:
        data_length = frame[6]
        checksum_offset = 7 + data_length
        return {
            "checksum": frame[checksum_offset],
            "checksum_calculated": self.calc_checksum(frame[:checksum_offset]),
            "data_length": data_length,
        }

    def get_func_name(self, func_code: int) -> str:
        return _FUNC_NAMES.get(func_code, f"功能码{func_code}")

    def get_command_definitions(self) -> list:
        return [
            {
                "func_code": 0x10,
                "name": "阀门控制",
                "params": [
                    {
                        "name": "action",
                        "label": "动作",
                        "type": "select",
                        "options": [
                            {"value": "open", "label": "开阀"},
                            {"value": "close", "label": "关阀"}
                        ],
                        "default": "open",
                    }
                ]
            }
        ]

    def get_default_rules(self) -> list:
        return [
            {
                "id": "rule_freeze",
                "name": "冻结ACK",
                "enabled": True,
                "match_function_code": 1,
                "ack_function_code": 0x81,
                "ack_data_fields": [
                    {"type": "fixed", "value": "00", "label": "成功"},
                    {"type": "copy", "field": "累计量", "length": 4, "label": "累计量回显"}
                ]
            }
        ]
```

---

## 调试技巧

### 1. 验证插件能被加载

```python
# 临时测试脚本
from protocol_base import ProtocolBase
import importlib

module = importlib.import_module("protocols.shanghai")
for attr_name in dir(module):
    attr = getattr(module, attr_name)
    if isinstance(attr, type) and issubclass(attr, ProtocolBase) and attr is not ProtocolBase:
        proto = attr()
        print(f"加载成功: {proto.name}")
        print(f"指令定义: {len(proto.get_command_definitions())} 个")
        print(f"ACK规则: {len(proto.get_default_rules())} 条")
```

### 2. 验证帧解析

准备一帧测试数据（Hex 字符串），手动调用 `parse_upload`：

```python
from protocol_base import hex_to_bytes

proto = ShanghaiProtocol()
test_frame = hex_to_bytes("AA 01 02 03 04 01 04 00 00 00 01 08 55")
info, err = proto.parse_upload(test_frame)
if err:
    print(f"解析失败: {err}")
else:
    print(f"功能码: {info['function_code']}")
    print(f"地址: {info['meter_address_logical']}")
    print(f"字段: {info['fields']}")
```

### 3. 验证 ACK 构造

```python
rules = proto.get_default_rules()
ack_frame = proto.build_ack(info, rules[0])
print(f"ACK帧: {bytes_to_hex(ack_frame)}")

# 验证校验和
meta = proto.get_frame_meta(ack_frame)
print(f"校验匹配: {meta['checksum'] == meta['checksum_calculated']}")
```

### 4. 通过 MCP 工具端到端测试

配置 `.mcp.json` 后，在 AI 对话中直接说：

- "启动 UDP 监听"
- "查看最近的通信日志"
- "发送一帧测试数据：AA 01 02 03 04 01 04 00 00 00 01 08 55"

---

## 参考实现

`protocols/zibo.py` 是一个完整的、经过真机验证的协议实现，包含：

- 帧头/帧尾校验
- 累加和校验算法
- 多功能码解析（101 冻结、102 周期、107 参数查询等）
- TLV 格式数据域解析
- 4 种指令定义（重启、参数查询、周期查询、透传）
- 2 条默认 ACK 规则

编写新协议时建议参考 `zibo.py` 的代码结构和错误处理方式。

---

## 常见问题

### Q: MCP 工具名带 `mock_` 前缀，换协议后名字不对怎么办？

工具名前缀在 `mcp_server.py` 中硬编码。如果不改，功能完全正常，只是工具名仍为 `mock_xxx`。如果希望前缀也跟着变，需要修改 `mcp_server.py` 中的工具定义。后续版本计划将前缀改为从配置读取。

### Q: 一个协议插件可以同时支持多个协议版本吗？

可以。在 `parse_upload` 中根据版本字节分发到不同的解析逻辑即可。`build_ack` 和 `build_command` 中也可以根据 `upload_info["version"]` 构造不同版本的帧。

### Q: 校验和可以跳过吗？

不建议。但如果协议确实没有校验和，可以在 `calc_checksum` 中返回 `0`，在 `parse_upload` 中跳过校验检查。`get_frame_meta` 中 `checksum` 和 `checksum_calculated` 都返回 `0` 即可。

### Q: ACK 规则文件在哪里？

首次启动时由 `get_default_rules()` 生成，保存为 `rules_{protocol}.json`（如 `rules_shanghai.json`）。用户可在网页界面修改，修改后通过 `mock_save_rules` 工具持久化。框架启动时优先读取 JSON 文件，不存在时才调用 `get_default_rules()`。
