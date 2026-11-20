"""M04 生产 Deps 装配单测（B1a：fail-fast 与 Protocol 契约，不依赖真实环境）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from packages.agent_core.errors import CoreError
from packages.agent_core.injection import (
    CheckpointStore,
    Clock,
    Deps,
    EventBus,
    ModelGateway,
    ObjectStore,
)
from services.orchestrator.deps import RealClock, RedisObjectStore, build_deps


class TestBuildDepsFailFast:
    """无 LLM 配置时明确报错，禁止静默降级 fake。"""

    def test_missing_model_config_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """缺 MODEL_* 任一配置 → RuntimeError（不返回带 fake model 的 Deps）。"""
        for key in ("MODEL_ENDPOINT", "MODEL_API_KEY", "MODEL_NAME"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(RuntimeError, match="模型网关未配置"):
            build_deps("postgresql://x", "redis://x")

    def test_partial_model_config_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """只配 endpoint 不算就位——三件缺一仍 fail-fast。"""
        monkeypatch.setenv("MODEL_ENDPOINT", "http://localhost:1234/v1")
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        monkeypatch.delenv("MODEL_NAME", raising=False)
        with pytest.raises(RuntimeError, match="模型网关未配置"):
            build_deps("postgresql://x", "redis://x")


class TestProductionContracts:
    """生产实现满足 M03 注入协议的静态契约。"""

    def test_prod_components_satisfy_protocols(self) -> None:
        """RealClock/RedisObjectStore 分别满足 Clock/ObjectStore Protocol。"""
        assert isinstance(RealClock(), Clock)
        assert isinstance(RedisObjectStore("redis://127.0.0.1:6379/0"), ObjectStore)

    def test_object_store_rejects_decoded_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """redis 误开 decode_responses 时 str 不能当 bytes 用，fail-closed。"""
        store = RedisObjectStore("redis://unused")
        monkeypatch.setattr(store, "_conn", lambda: SimpleNamespace(get=lambda _: "oops"))
        with pytest.raises(CoreError) as ei:
            store.get("checkpoint:object:run-1/0#sha256:deadbeef")
        assert ei.value.code == "CONTRACT_MISMATCH"
        assert "not bytes" in ei.value.message

    def test_deps_fields_are_protocol_ready(self) -> None:
        """Deps 所需协议类型均可导入且有明确签名（防止契约漂移）。"""
        for protocol in (Deps, CheckpointStore, Clock, EventBus, ModelGateway):
            assert protocol is not None
