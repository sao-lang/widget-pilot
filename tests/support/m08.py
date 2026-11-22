"""M08 测试替身（test doubles）：生产代码禁止 import 本文件。

只放测试用的内存对象存储、源码读取桩、Docker 命令桩与模型调用计数器；
数据类与端口协议从生产模块导入，避免重复定义导致类型漂移。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from packages.contracts.types import JsonObject, JsonValue
from services.guardrails.output_ports import parse_object_ref
from services.sandbox_runner.ports import (
    CommandResult,
    ObjectRef,
    SandboxUnavailableError,
)

__all__ = [
    "FailingEvidenceStore",
    "FakeDockerRunner",
    "FakeSourceStore",
    "MemoryEvidenceStore",
    "RecordingModel",
    "sandbox_report",
]


@dataclass(slots=True)
class FakeSourceStore:
    """内存源码读取桩：按 ref 返回字节并记录调用。"""

    sources: dict[str, bytes] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def get(self, ref: str) -> bytes:
        self.calls.append(ref)
        return self.sources[ref]


@dataclass(slots=True)
class MemoryEvidenceStore:
    """内存证据存储桩：真实计算与校验 sha256，支持篡改测试。"""

    objects: dict[str, bytes] = field(default_factory=dict)

    def put(self, key: str, data: bytes) -> ObjectRef:
        payload = bytes(data)
        self.objects[key] = payload
        return ObjectRef(key=key, hash=hashlib.sha256(payload).hexdigest(), size=len(payload))

    def get(self, ref: str) -> bytes:
        key, expected = parse_object_ref(ref)
        payload = self.objects[key]
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("hash mismatch: evidence object tampered")
        return payload

    def tamper(self, key: str, data: bytes) -> None:
        """模拟对象被替换（A17）。"""
        self.objects[key] = bytes(data)


@dataclass(slots=True)
class FailingEvidenceStore(MemoryEvidenceStore):
    """写入 sandbox.log 失败桩（证据写失败不判通过，I10）。"""

    fail_key_suffix: str = "sandbox.log"

    def put(self, key: str, data: bytes) -> ObjectRef:
        if key.endswith(self.fail_key_suffix):
            raise OSError("injected evidence write failure")
        return super().put(key, data)


@dataclass(slots=True)
class RecordingModel:
    """模型调用计数器：护栏路径必须为 0（I2/I14）。"""

    calls: int = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return ""


@dataclass(slots=True)
class FakeDockerRunner:
    """Docker 命令桩：记录命令、kill、存在性；可注入不可用/超时。"""

    result: CommandResult | None = None
    unavailable: bool = False
    timeout: bool = False
    commands: list[tuple[str, ...]] = field(default_factory=list)
    timeouts: list[int] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    existing: set[str] = field(default_factory=set)

    def run(self, cmd: Sequence[str], *, stdin: bytes, timeout_s: int) -> CommandResult:
        self.commands.append(tuple(cmd))
        self.timeouts.append(timeout_s)
        container = _container_name(cmd)
        if container:
            self.existing.add(container)
        if self.timeout:
            self.kill(container)
            raise SandboxUnavailableError("injected docker timeout")
        if self.unavailable:
            raise SandboxUnavailableError("injected docker unavailable")
        return self.result or CommandResult(stdout="{}", stderr="", exit_code=0)

    def kill(self, container_name: str) -> None:
        self.killed.append(container_name)
        self.existing.discard(container_name)

    def exists(self, container_name: str) -> bool:
        return container_name in self.existing


def _container_name(cmd: Sequence[str]) -> str:
    """从 --name 参数取容器名；DockerCliRunner 与桩共用该约定。"""
    args = list(cmd)
    for index, arg in enumerate(args):
        if arg == "--name" and index + 1 < len(args):
            return args[index + 1]
    return ""


def sandbox_report(
    *,
    compiled: str | None = "const Widget = () => null;",
    render: JsonObject | None = None,
    bridge_events: list[JsonObject] | None = None,
    interactions: list[JsonObject] | None = None,
    logs: str = "sandbox ok\n",
    snapshot: str = "<div id=\"root\"></div>",
) -> JsonObject:
    """构造容器内 runner 的 JSON 结果；默认全通过，按需注入失败。"""
    if render is None:
        render = {
            "grid": "2x2",
            "width_px": 160,
            "height_px": 160,
            "max_child_width_px": 150,
            "max_child_height_px": 150,
            "errors": [],
        }
    if bridge_events is None:
        bridge_events = [
            {"type": "capability_request", "capability": "date", "allowed": True}
        ]
    if interactions is None:
        interactions = [
            {"name": name, "passed": True, "applicable": True}
            for name in (
                "calendar_next_month",
                "clock_tick",
                "alarm_toggle",
                "task_check",
            )
        ]
    stages: dict[str, JsonValue] = {
        "render": cast(JsonValue, render),
        "bridge": {"events": cast(list[JsonValue], bridge_events)},
    }
    if compiled is not None:
        stages["compile"] = {"compiled": compiled}
    result: JsonObject = {
        "stages": stages,
        "interactions": cast(list[JsonValue], interactions),
        "logs": logs,
        "snapshot": snapshot,
    }
    return result


def json_bytes(value: JsonValue) -> bytes:
    """测试桩序列化辅助（确定性、无中文占位）。"""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
