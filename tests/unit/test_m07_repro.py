"""M07 复现指纹单测（§3.4 G / I-M6 / §5 D4 验收）。

验证同输入同输出、十字段逐一变化均改变指纹（差异敏感）、
``preference_version`` 纳入指纹（偏好变化是可解释输入变化）。
"""
from __future__ import annotations

from dataclasses import replace

from services.orchestrator.memory.repro import ReproInputs, compute_repro_fingerprint

_BASE = ReproInputs()


def _fingerprint(inputs: ReproInputs) -> str:
    """计算指纹的测试短别名（减少重复）。"""
    return compute_repro_fingerprint(inputs)


class TestComputeReproFingerprint:
    """复现指纹确定性与差异敏感。"""

    def test_same_inputs_same_fingerprint(self) -> None:
        """同输入两次计算恒等，输出为 64 位十六进制 sha256。"""
        first = _fingerprint(_BASE)
        second = _fingerprint(ReproInputs())
        assert first == second
        assert len(first) == 64
        assert all(ch in "0123456789abcdef" for ch in first)

    def test_each_field_change_changes_fingerprint(self) -> None:
        """十字段逐一改一个值，指纹均变化且互不相同（差异敏感）。"""
        baseline = _fingerprint(_BASE)
        variants = [
            replace(_BASE, prompt_version="v2"),
            replace(_BASE, recipe_version="v2"),
            replace(_BASE, retrieval_snapshot="snap-2"),
            replace(_BASE, model="frontier"),
            replace(_BASE, model_version="v2"),
            replace(_BASE, tier_rubric_version="v2"),
            replace(_BASE, temperature=0.5),
            replace(_BASE, seed=1),
            replace(_BASE, spec_version="v2"),
            replace(_BASE, preference_version=2),
        ]
        assert len(variants) == 10
        for variant in variants:
            assert _fingerprint(variant) != baseline
        assert len({_fingerprint(v) for v in variants}) == 10

    def test_preference_version_included(self) -> None:
        """仅改 preference_version 即改变指纹（I-M6，偏好版本纳入）。"""
        v1 = _fingerprint(replace(_BASE, preference_version=1))
        v2 = _fingerprint(replace(_BASE, preference_version=2))
        assert v1 != v2
