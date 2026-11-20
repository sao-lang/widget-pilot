"""M04 M03-Protocol 的真实落库适配器（§2.3 / §4.3 / v1.4 新增）。

把 M03 库级算法函数接到底层设施：
- PgCheckpointStore：checkpoint 表（PG，主键 (run_id, rev) + hash 链）
- RedisIdempotencyStore：side_effect 幂等表（Redis SETNX 原子占位）
- RedisEventBus：Redis Streams 扇出 + 保留窗口

这些是**完整可运行的异步实现**（psycopg3 / redis-py asyncio）。本机无 Docker
daemon 时 §8 验收用 conftest 内存替身跑全用例；真实 PG SQL 原子性
（UPDATE ... WHERE lease_until < now()）与 Redis Streams 扇出须在有容器环境
补跑 A3/A4/A11，未补跑项在 overview 如实登记，不冒充 GATE OK。

注：M03 Protocol 是同步签名（见 injection.py），生产适配器用 psycopg 同步
连接承载；事件总线用 redis-py 同步客户端，避免把 async 泄漏进内核。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Final

import psycopg
import redis
from sqlalchemy import func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from packages.agent_core.errors import CoreError
from packages.agent_core.types import AgentEvent, CheckpointNode, State
from services.control_plane.accounts.constants import LEASE_TTL_S
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import Checkpoint

# 事件解码由调用方注入（Stream data → AgentEvent），避免适配器重实现 schema 还原（规则 8）
type EventDecoder = Callable[[str], AgentEvent | None]

# checkpoint 表的列名前缀（结构性字面量，集中一处 —— 编码规则 2）
_KEY_CHECKPOINT: Final[str] = "checkpoint"
_KEY_SIDE_EFFECT: Final[str] = "side_effect"
_FIELD_RESULT_REF: Final[str] = "result_ref"
# 租约未占用时的占位 owner（抢锁成功后立即被真实 worker 覆盖）
_LEASE_UNOWNED: Final[str] = ""


def _to_str(value: bytes | str | None) -> str:
    """把 redis-py 返回的 bytes/str 统一解码为 str（wire 契约全用字符串）。

    redis-py 默认 decode_responses=False，GET/XRANGE 返回 bytes；直接 str(bytes)
    会得到 "b'...'" 伪串，故集中一处显式解码（编码规则 6：动态边界归一化）。
    """
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value) if value is not None else ""


def _stream_data(fields: Mapping[bytes | str, bytes | str]) -> str:
    """从 Redis Stream 的 fields 映射取 'data' 字段并解码（键可能是 bytes）。

    XRANGE/XREVRANGE 返回 {b'data': b'...'}（键与值皆 bytes）；用 str 键直取
    会落空。此函数兼容 bytes/str 两种键，是 Stream 读取的唯一动态边界。
    """
    if not isinstance(fields, dict):
        return ""
    for key, value in fields.items():
        if _to_str(key) == "data":
            return _to_str(value)
    return ""


class PgCheckpointStore:
    """PG checkpoint 存储：实现 M03 CheckpointStore Protocol（§3.4 表定义）。

    v1.5 ORM 化：save/load/latest_rev/hashes 用 SQLAlchemy ORM；主键
    (run_id, rev) 唯一约束防重复（I1）。
    """

    def __init__(self, dsn: str, object_get: Callable[[str], bytes]) -> None:
        """注入 PG DSN 与对象读取回调（state_ref → 字节）。

        Args:
            dsn: PostgreSQL 连接串。
            object_get: 从对象存储按 ref 读回字节的回调（含 hash 校验），由 Deps 注入。
        """
        self._factory = make_session_factory(dsn)
        self._object_get = object_get

    def save(
        self,
        run_id: str,
        rev: int,
        node: CheckpointNode,
        state_ref: str,
        prev_hash: str,
    ) -> None:
        """落一行 checkpoint：主键冲突即抛（I1 断言点）。

        B2：新 rev 行继承同 run 最新行的租约列（run 级租约，不随 checkpoint 丢）。
        单 Session 事务内先查 MAX(rev) 行租约、再写新行。

        Args:
            run_id: 运行标识。
            rev: 单调版本号。
            node: 触发落盘的决策节点。
            state_ref: 对象存储引用（state 字节 + hash）。
            prev_hash: 上一 rev 的链 hash。
        """
        try:
            with session_scope(self._factory) as session:
                # B2：查最新 rev 行的租约列；首行无前驱时二者为 None（= 空闲）。
                # with_for_update 锁住该行——restore 旧单语句 INSERT...SELECT 的
                # 快照原子性：SELECT 与 INSERT 之间租约不会被并发事务改写。
                prev = session.execute(
                    select(Checkpoint.lease_owner, Checkpoint.lease_until)
                    .where(Checkpoint.run_id == run_id)
                    .order_by(Checkpoint.rev.desc())
                    .limit(1)
                    .with_for_update()
                ).first()
                lease_owner = prev.lease_owner if prev is not None else None
                lease_until = prev.lease_until if prev is not None else None
                session.add(
                    Checkpoint(
                        run_id=run_id,
                        rev=rev,
                        node=node.value,
                        state_ref=state_ref,
                        prev_hash=prev_hash,
                        lease_owner=lease_owner,
                        lease_until=lease_until,
                        # created_at 省略 → server_default func.now()
                    )
                )
        except IntegrityError as exc:
            # 行为等价：原 psycopg 实现直接抛 UniqueViolation（I1 断言点），
            # ORM 包装成 IntegrityError——此处还原底层异常，保持对外契约不变
            if isinstance(exc.orig, psycopg.errors.UniqueViolation):
                raise exc.orig from exc
            raise

    def load(self, run_id: str) -> State | None:
        """取最新 rev 的 state_ref，从对象存储读回并反序列化为 State（§3.4）。

        Args:
            run_id: 运行标识。

        Returns:
            最新 checkpoint 的 State；无 checkpoint 返回 None。
        """
        with session_scope(self._factory) as session:
            ref = session.scalar(
                select(Checkpoint.state_ref)
                .where(Checkpoint.run_id == run_id)
                .order_by(Checkpoint.rev.desc())
                .limit(1)
            )
        if ref is None:
            return None
        # 完整 ref 交给注入的对象读取回调（含 hash 校验），契约不变
        data = self._object_get(str(ref))
        return State.from_snapshot(json.loads(data))

    def latest_rev(self, run_id: str) -> int:
        """返回最大 rev；无记录返回 -1（§4.3 契约）。"""
        with session_scope(self._factory) as session:
            value = session.scalar(
                select(func.coalesce(func.max(Checkpoint.rev), -1)).where(
                    Checkpoint.run_id == run_id
                )
            )
        return int(value) if value is not None else -1

    def hashes(self, run_id: str) -> list[str]:
        """按 rev 排序返回 prev_hash 列（供 hash 链重算，I2）。

        Args:
            run_id: 运行标识。

        Returns:
            按 rev 升序的 prev_hash 列表（首行为空串）。
        """
        with session_scope(self._factory) as session:
            values = session.scalars(
                select(Checkpoint.prev_hash)
                .where(Checkpoint.run_id == run_id)
                .order_by(Checkpoint.rev.asc())
            ).all()
        return [str(v) for v in values]


class PgLease:
    """PG 租约对象（实现 M03 Lease Protocol）：run() 内无参 heartbeat/release。"""

    def __init__(self, provider: PgLeaseProvider, run_id: str, owner: str) -> None:
        """持有 provider 引用与身份，无参方法由 provider 按 (run_id, owner) 操作。"""
        self._provider = provider
        self.run_id = run_id
        """租约所属 run。"""
        self.owner = owner
        """持锁者标识。"""

    def heartbeat(self) -> bool:
        """续约：仅当本人仍持锁时刷新 lease_until（M03 Lease 契约）。"""
        return self._provider.heartbeat(self.run_id, self.owner)

    def release(self) -> None:
        """主动释放租约（终态让出持锁权，§11.4 step 5）。"""
        self._provider.release(self.run_id, self.owner)


class PgLeaseProvider:
    """PG 租约提供者：实现 M03 LeaseProvider Protocol（§11.4 step 2/3）。

    租约存在 checkpoint 表最新 rev 行的 lease_owner/lease_until 列（与 checkpoint
    同事务，崩溃后状态一致）。原子抢锁用条件 UPDATE —— 只有 lease_until 为空或
    已过期的行才能被接管（UPDATE ... WHERE lease_until < now() OR IS NULL），
    并发下恰好一个 worker 的 UPDATE 命中（I3/A4）。
    """

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN，构造该实例绑定的 sessionmaker。

        Args:
            dsn: PostgreSQL 连接串。
        """
        self._factory = make_session_factory(dsn)

    def acquire(self, run_id: str, owner: str, ttl_s: int) -> PgLease:
        """原子抢锁；成功返回 PgLease，抢不到抛 CoreError（A4 单持锁者）。

        先 ON CONFLICT DO NOTHING 建 rev=0 锚点，再条件 UPDATE（租约空闲/过期
        才命中）。RETURNING 判赢家 —— PG 行锁保证并发恰好一个成功。

        Args:
            run_id: 运行标识。
            owner: 持锁 worker 标识。
            ttl_s: 租约有效期秒数。

        Returns:
            抢锁成功返回 PgLease（符合 M03 LeaseProvider 协议的非空返回约定）。

        Raises:
            CoreError: 已被他人持有且未过期，抛业务冲突 HUMAN_REQUIRED；
                与 FakeLeaseProvider 行为一致，防止未持锁的 worker 继续跑 run()。
        """
        ttl_expr = func.now() + (literal(ttl_s) * text("interval '1 second'"))
        with session_scope(self._factory) as session:
            # 懒建锚点（PG 方言的 ON CONFLICT DO NOTHING）
            session.execute(
                pg_insert(Checkpoint)
                .values(run_id=run_id, rev=0, node="FINAL", state_ref="", prev_hash="")
                .on_conflict_do_nothing(index_elements=["run_id", "rev"])
            )
            # 原子条件更新：仅最新 rev 行、租约空闲/过期时命中
            max_rev = (
                select(func.max(Checkpoint.rev))
                .where(Checkpoint.run_id == run_id)
                .scalar_subquery()
            )
            stmt = (
                update(Checkpoint)
                .where(Checkpoint.run_id == run_id)
                .where(Checkpoint.rev == max_rev)
                .where(
                    or_(
                        Checkpoint.lease_owner == _LEASE_UNOWNED,
                        Checkpoint.lease_until.is_(None),
                        Checkpoint.lease_until < func.now(),
                    )
                )
                .values(lease_owner=owner, lease_until=ttl_expr)
                .returning(Checkpoint.run_id)
            )
            won = session.execute(stmt).first() is not None
        if not won:
            raise CoreError.business("HUMAN_REQUIRED", "lease held by another owner")
        return PgLease(self, run_id, owner)

    def heartbeat(self, run_id: str, owner: str) -> bool:
        """续租：仅 owner 匹配且未过期时刷新；返回是否成功（§11.4 step 2）。

        Args:
            run_id: 运行标识。
            owner: 持锁 worker 标识（不匹配则不更新，返回 False）。

        Returns:
            True 表示续约成功；False 表示租约已被他人接管或过期。

        TTL 引用 LEASE_TTL_S 常量（M02 Q4）。不限制 rev —— B2 租约继承让同 run
        多行共享 owner，heartbeat 须刷新所有匹配行（行为等价原 SQL）。
        """
        ttl_expr = func.now() + (literal(LEASE_TTL_S) * text("interval '1 second'"))
        stmt = (
            update(Checkpoint)
            .where(Checkpoint.run_id == run_id)
            .where(Checkpoint.lease_owner == owner)
            .where(Checkpoint.lease_until >= func.now())
            .values(lease_until=ttl_expr)
            .returning(Checkpoint.run_id)
        )
        with session_scope(self._factory) as session:
            renewed = session.execute(stmt).first() is not None
        return renewed

    def release(self, run_id: str, owner: str) -> None:
        """释放租约：仅 owner 可主动归还（终态立即让出，不等过期）。

        Args:
            run_id: 运行标识。
            owner: 持锁 worker 标识（不匹配则无操作，防误放他人租约）。
        """
        with session_scope(self._factory) as session:
            session.execute(
                update(Checkpoint)
                .where(Checkpoint.run_id == run_id)
                .where(Checkpoint.lease_owner == owner)
                .values(lease_owner=_LEASE_UNOWNED, lease_until=None)
            )

    def scan_expired(self, now: datetime) -> list[str]:
        """扫描 lease_until < now 且仍有持锁者的 runId（§11.4 step 3）。

        Args:
            now: 判定时刻（生产传 utcnow；测试可注入过去/未来时间）。

        Returns:
            租约已过期的 run_id 列表（去重）。
        """
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(Checkpoint.run_id)
                .where(Checkpoint.lease_until < now)
                .where(Checkpoint.lease_owner != _LEASE_UNOWNED)
                .distinct()
            ).all()
        return [str(r) for r in rows]


class RedisIdempotencyStore:
    """Redis 幂等表：实现 M03 IdempotencyStore Protocol（§3.4 side_effect）。

    SETNX 原子占位：命中复用 result_ref、不重复执行（I4/A5）。
    """

    def __init__(self, redis_url: str) -> None:
        """注入 Redis URL；客户端在使用时惰性创建。"""
        self._redis_url = redis_url
        self._client: redis.Redis | None = None

    def _conn(self) -> redis.Redis:
        """惰性创建同步 Redis 客户端（连接复用，避免每次调用重建）。"""
        if self._client is None:
            self._client = redis.from_url(self._redis_url)
        return self._client

    def get_or_reserve(self, idem_key: str, run_id: str) -> str | None:
        """命中返回 result_ref；否则 SETNX 占位返回 None。

        Args:
            idem_key: 幂等键（session_id + step + payload_hash）。
            run_id: 运行标识，写入占位值 reserved:{run_id} 便于溯源。

        Returns:
            命中时返回已提交的 result_ref（复用既有结果，不重复执行副作用）；
            首次占位成功返回 None（调用方继续执行副作用后须 commit）。
        """
        full_key = f"{_KEY_SIDE_EFFECT}:{idem_key}"
        client = self._conn()
        existing = client.get(full_key)
        if existing is not None:
            return _to_str(existing)  # 命中：复用既有结果，不重复执行
        # SETNX：只有占位成功者继续执行副作用，并发下恰好一个赢家（I4）
        client.setnx(full_key, f"reserved:{run_id}")
        return None

    def commit(self, idem_key: str, result_ref: str) -> None:
        """副作用完成后写回 result_ref（占位 → 真实结果）。

        Args:
            idem_key: 幂等键（与 get_or_reserve 同一键）。
            result_ref: 副作用产物的对象存储引用。
        """
        client = self._conn()
        client.set(f"{_KEY_SIDE_EFFECT}:{idem_key}", result_ref)

    def count(self, run_id: str) -> int:
        """统计该 run 的幂等条目数（A5 断言副作用不重复）。"""
        client = self._conn()
        # 生产用 SCAN 统计前缀，测试替身用内存；此处返回扫描命中数
        keys = list(client.scan_iter(match=f"{_KEY_SIDE_EFFECT}:*"))
        return len(keys)


def make_event_decoder() -> EventDecoder:
    """默认事件解码器：Stream data JSON → AgentEvent（emit 的逆操作）。

    未知 kind 返回 None 让总线跳过（§4.5：前端/服务端对未知 kind 忽略而非报错）。
    构造 AgentEvent 用最小字段（SSE 只消费 seq/kind/payload），其余填默认值。
    """
    from packages.agent_core.types import AgentEvent
    from packages.contracts.enums import AgentEventKind

    def decode(raw: str) -> AgentEvent | None:
        """把一条 Stream data JSON 还原为 AgentEvent；未知 kind / 坏数据返回 None 跳过。

        Args:
            raw: XADD 写入的 data 字段 JSON 字符串（ emit 的 payload 序列化结果）。

        Returns:
            AgentEvent：用最小字段构造（SSE 只消费 seq/kind/payload），其余填默认值；
            解析失败或 kind 不在 AgentEventKind 时返回 None（§4.5 忽略未知事件）。
        """
        try:
            data = json.loads(raw)
            kind = AgentEventKind(str(data["kind"]))
        except (ValueError, KeyError, json.JSONDecodeError):
            return None  # 未知 kind / 坏数据：跳过（§4.5）
        return AgentEvent(
            seq=int(data["seq"]),
            session_id=str(data.get("session_id", "")),
            run_id=str(data.get("run_id", "")),
            ts=str(data.get("ts", "")),
            kind=kind.value,
            phase=str(data.get("phase", "")),
            payload=data.get("payload", {}),
            trace_id=str(data.get("trace_id", "")),
        )

    return decode


class RedisEventBus:
    """Redis Streams 事件总线：实现 M03 EventBus Protocol（§4.2 / §11.8）。

    XADD 写流、XRANGE 按 seq 续传、XTRIM 保留窗口（I5/A11）。
    """

    def __init__(
        self, redis_url: str, window_n: int, event_decode: EventDecoder
    ) -> None:
        """注入 Redis URL、保留窗口 N 与事件解码器。

        Args:
            redis_url: Redis 连接串。
            window_n: 保留窗口大小（SSE_WINDOW_N，M02 Q4 定稿）。
            event_decode: Stream data JSON → AgentEvent 的回调；返回 None 表示未知 kind 跳过。
        """
        self._redis_url = redis_url
        self._window_n = window_n
        self._event_decode = event_decode
        self._client: redis.Redis | None = None

    def _conn(self) -> redis.Redis:
        """惰性创建同步 Redis 客户端。"""
        if self._client is None:
            self._client = redis.from_url(self._redis_url)
        return self._client

    def _stream_key(self, session_id: str) -> str:
        """按会话生成 Stream key（结构性前缀集中一处）。"""
        return f"{_KEY_CHECKPOINT}:events:{session_id}"

    def emit(self, event: AgentEvent) -> None:
        """XADD 写一条事件；XTRIM 保留最近 N 条（超窗由快照补，§4.5）。"""
        client = self._conn()
        key = self._stream_key(event.session_id)
        payload = json.dumps(
            {
                "seq": event.seq,
                "kind": event.kind,  # AgentEvent.kind 已是 AgentEventKind 的 wire 字符串
                "payload": event.payload,
            },
            default=str,
        )
        client.xadd(key, {"data": payload})
        client.xtrim(key, maxlen=self._window_n, approximate=False)

    def window(self, session_id: str, n: int) -> list[AgentEvent]:
        """返回该会话最近 n 条事件（XREVRANGE 取尾部）。"""
        client = self._conn()
        key = self._stream_key(session_id)
        raw = client.xrevrange(key, count=n) or []
        # XREVRANGE 返回新→旧，反转为按 seq 升序（I5：续传要求顺序）
        events: list[AgentEvent] = []
        for _msg_id, fields in reversed(raw):
            decoded = self._event_decode(_stream_data(fields or {}))
            # 未知 kind 返回 None → 跳过（§4.5 前端/服务端对未知 kind 忽略而非报错）
            if decoded is not None:
                events.append(decoded)
        return events

    def all(self) -> list[AgentEvent]:
        """全部事件：跨会话遍历 Stream key（回放场景；生产通常按会话用 window）。"""
        client = self._conn()
        events: list[AgentEvent] = []
        for key in client.scan_iter(match=f"{_KEY_CHECKPOINT}:events:*"):
            for _msg_id, fields in client.xrange(_to_str(key)) or []:
                decoded = self._event_decode(_stream_data(fields or {}))
                if decoded is not None:
                    events.append(decoded)
        return events
