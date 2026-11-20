"""M04 生产 Deps 装配 + checkpoint 真实落库集成测试（B1a）。

补充单测无法覆盖的部分：checkpoint 的 state 大对象经对象存储落 Redis、
元数据行落 PG，run() 用真实适配器写 checkpoint 后新实例能 load 恢复。
这是「能力造好但没接生产」断点的核心验证——保存/加载走真实设施。

前置：docker compose --profile core up -d（PG:5432 / Redis:6379）；
本机无容器时整文件 skip（连通性守卫），不冒充通过。
"""

from __future__ import annotations

import psycopg
import pytest
import redis

from packages.agent_core.injection import CheckpointStore, ObjectStore
from packages.agent_core.state.checkpoint import save_checkpoint
from packages.agent_core.types import CheckpointNode, State
from services.orchestrator.deps import (
    RedisObjectStore,
    build_checkpoint_store,
)

PG_DSN = "postgresql://widgetpilot:widgetpilot_dev@127.0.0.1:5432/widgetpilot"
REDIS_URL = "redis://127.0.0.1:6379/0"


def _infra_available() -> bool:
    """探测 PG/Redis 是否可达；不可达则跳过（不冒充通过）。"""
    try:
        with psycopg.connect(PG_DSN, connect_timeout=2):
            pass
        redis.from_url(REDIS_URL, socket_connect_timeout=2).ping()
    except Exception:  # noqa: BLE001 - 连通性探测必须吞掉任意连接错误以决定 skip
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _infra_available(),
    reason="PG/Redis 未就绪（需 docker compose --profile core up -d）",
)


@pytest.fixture(autouse=True)
def clean_infra() -> None:
    """每例前清空 checkpoint 表与 Redis（集成测试隔离）。"""
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE checkpoint CASCADE")
        conn.commit()
    redis.from_url(REDIS_URL).flushdb()


class TestRedisObjectStore:
    """checkpoint state 大对象走 Redis（通用 put/get + sha256 校验）。"""

    def test_put_get_roundtrip(self) -> None:
        """put 返回带 hash 的 ref，get 按 ref 校验取回原字节。"""
        store = RedisObjectStore(REDIS_URL)
        ref = store.put("checkpoint/run1/0.json", b'{"a": 1}')
        assert "#sha256:" in ref
        assert store.get(ref) == b'{"a": 1}'

    def test_hash_mismatch_rejected(self) -> None:
        """篡改 ref 的 hash 段后 get 抛错（防篡改，fail-closed）。"""
        from packages.agent_core.errors import CoreError

        store = RedisObjectStore(REDIS_URL)
        ref = store.put("checkpoint/run1/0.json", b"data")
        bad = ref.rsplit("#sha256:", 1)[0] + "#sha256:" + "0" * 64
        with pytest.raises(CoreError):
            store.get(bad)


class TestProdCheckpointStore:
    """生产 checkpoint 适配器满足 M03 契约 + 真实落库。"""

    def test_protocol_and_convenience_attrs(self) -> None:
        """满足 CheckpointStore Protocol 且带 objects/states/rows 便利属性。"""
        store = build_checkpoint_store(PG_DSN, REDIS_URL)
        assert isinstance(store, CheckpointStore)
        assert isinstance(store.objects, ObjectStore)
        assert hasattr(store, "states") and hasattr(store, "rows")

    def test_save_checkpoint_persists_and_reloads(self) -> None:
        """save_checkpoint 经生产适配器写 PG+Redis，新实例 load 恢复同 rev。"""
        store = build_checkpoint_store(PG_DSN, REDIS_URL)
        state = State.initial("sess-1", "run-1", "做一个日历", "4x4")
        state = state.bump(rev=1)
        save_checkpoint(store, state, CheckpointNode.CALL_MODEL)
        assert store.latest_rev("run-1") == 1

        rebuilt = build_checkpoint_store(PG_DSN, REDIS_URL)
        loaded = rebuilt.load("run-1")
        assert loaded is not None
        assert loaded.run_id == "run-1"
        assert loaded.spec.grid == "4x4"
