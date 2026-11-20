"""M04 生产依赖装配（B1a：把 M03 内核接到真实 PG/Redis）。

M03 run() 通过 Deps 注入全部基础设施；本模块提供生产实现：
- RedisObjectStore：checkpoint state 大对象的通用存储（put/get + sha256 校验）；
  checkpoint 只存引用，字节落 Redis（复用已就绪的 Redis 依赖，不新增设施）。
- ProdCheckpointStore：包装 PgCheckpointStore，补齐 M03 save_checkpoint/chain_valid
  依赖的 objects/states/rows 便利属性，元数据行仍落 PG（rev/hash 链/租约）。
- RealClock：生产时钟（单测用 FakeClock）。
- build_deps：一次性装配完整 Deps；真实 ModelGateway 从 env 构造，缺 key
  明确 fail-fast，不静默降级为 fake（conductor 铁律 8）。
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Final

import redis
from sqlalchemy import select

from packages.agent_core.errors import CoreError
from packages.agent_core.injection import Deps
from packages.agent_core.model import provider as model_provider
from packages.agent_core.types import CheckpointNode, Message, ModelOutput, State
from packages.contracts.types import JsonObject
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import Checkpoint
from services.orchestrator.adapters import (
    PgCheckpointStore,
    PgLeaseProvider,
    RedisEventBus,
    RedisIdempotencyStore,
    make_event_decoder,
)

_ENV_MODEL_ENDPOINT: Final[str] = "MODEL_ENDPOINT"
_ENV_MODEL_KEY: Final[str] = "MODEL_API_KEY"
_ENV_MODEL_NAME: Final[str] = "MODEL_NAME"
_ENV_REDIS_URL: Final[str] = "REDIS_URL"
_ENV_SSE_WINDOW: Final[str] = "SSE_WINDOW_N"
_HASH_PREFIX: Final[str] = "#sha256:"
_KEY_PREFIX: Final[str] = "checkpoint:object:"
_DEFAULT_SSE_WINDOW: Final[int] = 500


class RedisObjectStore:
    """通用对象存储（Redis 承载字节），实现 M03 ObjectStore Protocol。"""

    def __init__(self, redis_url: str) -> None:
        """注入 Redis URL；客户端惰性创建（连接复用）。

        Args:
            redis_url: Redis 连接串。
        """
        self._redis_url = redis_url
        self._client: redis.Redis | None = None

    def _conn(self) -> redis.Redis:
        """惰性创建同步 Redis 客户端。"""
        if self._client is None:
            self._client = redis.from_url(self._redis_url)
        return self._client

    def put(self, key: str, data: bytes) -> str:
        """写字节并返回带 sha256 的引用；同 key 覆盖（幂等，便于恢复重放）。

        Args:
            key: 逻辑对象键（如 checkpoint/run/0.json）。
            data: 原始字节。

        Returns:
            形如 ``checkpoint:object:<key>#sha256:<hex>`` 的引用。
        """
        digest = hashlib.sha256(data).hexdigest()
        ref = f"{_KEY_PREFIX}{key}{_HASH_PREFIX}{digest}"
        self._conn().set(f"{_KEY_PREFIX}{key}", data)
        return ref

    def get(self, ref: str) -> bytes:
        """按 ref 取字节并校验 sha256；不匹配或缺键抛 CoreError（fail-closed）。

        Args:
            ref: put 返回的带 hash 引用。

        Returns:
            校验通过的原始字节。

        Raises:
            CoreError: 引用无 hash 段、缺键、或内容与 hash 不符。
        """
        logical, sep, tagged = ref.partition(_HASH_PREFIX)
        if not sep or not tagged:
            raise CoreError.business(
                "CONTRACT_MISMATCH", "object ref missing hash", ref
            )
        data = self._conn().get(logical)
        if data is None:
            raise CoreError.business("CONTRACT_MISMATCH", "object missing", ref)
        # redis 的类型桩把 get() 标成 bytes | str；本存储契约只接受原始字节，
        # 显式收窄以 fail-closed（误开 decode_responses 时不能把 str 当 bytes 用）。
        if not isinstance(data, bytes):
            raise CoreError.business("CONTRACT_MISMATCH", "object not bytes", ref)
        if hashlib.sha256(data).hexdigest() != tagged:
            raise CoreError.business("CONTRACT_MISMATCH", "object hash mismatch", ref)
        return data


class ProdCheckpointStore:
    """生产 checkpoint 存储：PG 存元数据行 + Redis 存 state 字节。

    同时满足 M03 CheckpointStore Protocol（save/load/latest_rev/hashes）与
    save_checkpoint/chain_valid 需要的便利属性（objects/states/rows）。
    """

    def __init__(self, dsn: str, objects: RedisObjectStore) -> None:
        """注入 PG DSN 与对象存储。

        Args:
            dsn: PostgreSQL 连接串。
            objects: checkpoint state 字节存储（同时作为 load 的 object_get 源）。
        """
        self._pg = PgCheckpointStore(dsn, object_get=objects.get)
        self._objects: RedisObjectStore = objects
        self.states: dict[str, State] = {}
        """run_id → 最近一次 save/load 的 State（内存便利，跨实例不依赖）。"""
        self._factory = make_session_factory(dsn)

    @property
    def objects(self) -> RedisObjectStore:
        """暴露对象存储给 save_checkpoint（写 state_ref）与 chain_valid（回读校验）。"""
        return self._objects

    @property
    def rows(self) -> dict[str, list[JsonObject]]:
        """从 PG 组装 chain_valid 所需的行视图：run_id → 按 rev 的行列表。

        Returns:
            run_id → [{rev, node, state_ref, prev_hash}, ...]（按 rev 升序）；
            各值均为 JSON 标量（node 为 StrEnum 的 wire 字符串）。
        """
        result: dict[str, list[JsonObject]] = {}
        with session_scope(self._factory) as session:
            all_rows = session.scalars(
                select(Checkpoint).order_by(Checkpoint.run_id, Checkpoint.rev)
            ).all()
        for row in all_rows:
            result.setdefault(str(row.run_id), []).append(
                {
                    "rev": int(row.rev),
                    "node": row.node,
                    "state_ref": str(row.state_ref),
                    "prev_hash": str(row.prev_hash),
                }
            )
        return result

    def save(
        self,
        run_id: str,
        rev: int,
        node: CheckpointNode,
        state_ref: str,
        prev_hash: str,
    ) -> None:
        """委托 PG 存一行 checkpoint（主键 (run_id, rev) 唯一，I1）。"""
        self._pg.save(run_id, rev, node, state_ref, prev_hash)

    def load(self, run_id: str) -> State | None:
        """从 PG 取最新 state_ref → 对象存储回读 → 反序列化为 State。"""
        state = self._pg.load(run_id)
        if state is not None:
            self.states[run_id] = state
        return state

    def latest_rev(self, run_id: str) -> int:
        """PG 内该 run 的最大 rev；无记录返回 -1。"""
        return self._pg.latest_rev(run_id)

    def hashes(self, run_id: str) -> list[str]:
        """按 rev 升序返回 prev_hash 列（供 save_checkpoint 续链）。"""
        return self._pg.hashes(run_id)


class RealClock:
    """生产时钟：返回当前 UTC 时间（单测用 FakeClock 冻结）。"""

    def now(self) -> datetime:
        """返回当前 UTC 时间。"""
        return datetime.now(UTC)


class _EnvModelGateway:
    """生产模型网关：把 M03 provider.complete 包装成 ModelGateway Protocol。

    真实 LLM 调用只在 env 配齐 endpoint/key/model 时发出；本类不做 fake——
    无配置时由 build_deps fail-fast，绝不静默降级（conductor 铁律 8）。
    """

    def __init__(self, endpoint: str, api_key: str, model: str) -> None:
        """注入 chat-completions 端点、密钥与模型名。

        Args:
            endpoint: 兼容 chat-completions 的 HTTP 端点。
            api_key: 供应商密钥（仅内存持有，不落日志）。
            model: 模型名/档位标识。
        """
        self._endpoint = endpoint
        self._api_key = api_key
        self._model = model

    def complete(self, messages: list[Message], tier: str, stream: bool) -> ModelOutput:
        """调用真实模型端点；签名与 ModelGateway Protocol 逐字对齐。

        Args:
            messages: 对话消息列表。
            tier: 模型档位（契约字符串）。
            stream: 是否流式（透传 provider）。

        Returns:
            归一化后的 ModelOutput（usage 跨供应商对齐）。
        """
        return model_provider.complete(
            messages,
            tier=tier,
            stream=stream,
            endpoint=self._endpoint,
            api_key=self._api_key,
            model=self._model,
        )


def build_checkpoint_store(dsn: str, redis_url: str) -> ProdCheckpointStore:
    """构造生产 checkpoint 存储（PG 元数据 + Redis state 字节）。

    Args:
        dsn: PostgreSQL 连接串。
        redis_url: Redis 连接串（state 字节与事件流共用）。

    Returns:
        满足 M03 全部约定的生产 checkpoint 存储。
    """
    return ProdCheckpointStore(dsn, RedisObjectStore(redis_url))


def build_deps(
    dsn: str,
    redis_url: str,
    owner: str = "orchestrator-1",
) -> Deps:
    """装配一次 run() 所需的全部生产依赖。

    Args:
        dsn: PostgreSQL 连接串。
        redis_url: Redis 连接串。
        owner: 持锁 worker 标识（多实例各不同，避免抢同一租约）。

    Returns:
        完整 Deps：真实 checkpoint/lease/幂等/事件/模型/时钟全部就位。

    Raises:
        RuntimeError: 模型网关 env 未配齐（禁止静默降级为 fake）。
    """
    endpoint = os.getenv(_ENV_MODEL_ENDPOINT, "")
    api_key = os.getenv(_ENV_MODEL_KEY, "")
    model = os.getenv(_ENV_MODEL_NAME, "")
    if not (endpoint and api_key and model):
        raise RuntimeError(
            f"模型网关未配置：需 {_ENV_MODEL_ENDPOINT}/{_ENV_MODEL_KEY}/{_ENV_MODEL_NAME}；"
            "拒绝静默降级为 fake（生产必须真实调用 LLM）"
        )
    window = int(os.getenv(_ENV_SSE_WINDOW, str(_DEFAULT_SSE_WINDOW)))
    bus = RedisEventBus(redis_url, window, make_event_decoder())
    checkpoint_store = build_checkpoint_store(dsn, redis_url)
    gateway = _EnvModelGateway(endpoint, api_key, model)
    # ADR-003 / Q2：composition root 单次注册首批能力并冻结；视觉理解复用同一
    # ModelGateway（Q9）。已冻结时跳过——同进程重复 build_deps 不重复注册。
    from packages.agent_core.tools.registry import is_frozen
    from services.orchestrator.capabilities import register_capabilities

    if not is_frozen():
        register_capabilities(gateway=gateway)
    from services.orchestrator.capability_guard_adapter import ProductionCapabilityGuard

    return Deps(
        model=gateway,
        checkpoint_store=checkpoint_store,
        idempotency_store=RedisIdempotencyStore(redis_url),
        event_bus=bus,
        clock=RealClock(),
        lease_provider=PgLeaseProvider(dsn),
        owner=owner,
        capability_guard=ProductionCapabilityGuard(),  # ADR-003 / B2：全五项闸门
        object_store=RedisObjectStore(redis_url),  # ADR-003 / B5：能力结果落 ref
    )
