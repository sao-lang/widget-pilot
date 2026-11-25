"""确定性 widget 生成通道（ADR-013 §9）：解释器四分支 + 成分编译器 + 三门禁成包。

默认路径零云端 LLM；本地小模型不算 LLM；llm_fallback / llm-rework 是显式降级例外。
"""
from services.deterministic.channel import run_deterministic
from services.deterministic.compiler import (
    assert_valid_spec,
    build_manifest,
    compile_spec,
    render_tsx,
)

__all__ = [
    "assert_valid_spec",
    "build_manifest",
    "compile_spec",
    "render_tsx",
    "run_deterministic",
]
