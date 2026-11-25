"""v3 确定性通道真沙箱集成测试（podman 后端）。

验证 ADR-013 §9 最短闭环的「真门禁」环节：确定性生成的 WidgetSpec→TSX
经真 validate_and_test（输出护栏 + manifest 契约 + 真容器沙箱）通过，
并真成包。无容器后端时 skip（环境非回归）；不依赖 S3（内存 stores 注入）。

跑法：
  WIDGETPILOT_CONTAINER_CLI_PATH=D:/apps/podman/podman.exe pytest -q \
    tests/integration/test_v3_deterministic_podman.py
"""
from __future__ import annotations

import os

import pytest

from services.guardrails.interpreter.rule import RuleInterpreter
from services.sandbox_runner import container_cli
from services.sandbox_runner.ports import DockerCliRunner
from tests.support.m08 import MemoryEvidenceStore

pytestmark = pytest.mark.skipif(
    not container_cli.available() or not os.getenv("WIDGETPILOT_CONTAINER_CLI_PATH"),
    reason="容器后端不可用（需 WIDGETPILOT_CONTAINER_CLI_PATH 指向 podman）",
)

_DESCRIPTION = "主屏幕放一个 4x2 的任务清单，显示待办"


class _MemSource:
    """按 key 返回确定性 TSX 字节（绕过 S3，专注验证容器门禁）。"""

    def __init__(self, key: str, tsx: str) -> None:
        self._key = key
        self._tsx = tsx.encode("utf-8")

    def get(self, ref: str) -> bytes:
        key, _, _ = ref.partition("#sha256:")
        assert key == self._key
        return self._tsx


def test_deterministic_tsx_passes_real_podman_gates() -> None:
    """确定性 TSX 真过 podman 三门禁（编译/渲染/尺寸/运行时/能力/交互/设备）。"""
    from services.control_plane.bundles.adapter import is_validation_passed
    from services.deterministic.compiler import (
        assert_valid_spec,
        build_manifest,
        compile_spec,
        render_tsx,
    )
    from services.sandbox_runner.entry import validate_and_test

    fill = RuleInterpreter().interpret(_DESCRIPTION).instances[0]
    spec = compile_spec(fill)
    assert_valid_spec(spec)
    tsx = render_tsx(spec)
    key = "code/it/deterministic/widget.tsx"

    validation = validate_and_test(
        spec,
        key,
        build_manifest(spec),
        0,
        session_id="it-det",
        run_id="it-det-run",
        evidence_store=MemoryEvidenceStore(),
        source_store=_MemSource(key, tsx),
        docker_runner=DockerCliRunner(),
        prefix="evidence/it-det/it-det-run",
    )
    assert validation.passed is True, f"真门禁未过：{validation.reason_code}"
    assert is_validation_passed(validation) is True
    cases = {c.name: c.passed for c in (validation.sandbox_test.cases if validation.sandbox_test else ())}
    for name in ("compile", "render", "size_assert", "runtime_no_error",
                 "capability_via_bridge", "task_check", "device_api_safe"):
        assert cases.get(name) is True, f"沙箱 case 未过：{name}"
