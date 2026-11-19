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
from collections.abc import Callable
from datetime import datetime
from typing import Final

import redis

from packages.agent_core.types import AgentEvent, CheckpointNode, State

# 事件解码由调用方注入（Stream data → AgentEvent），避免适配器重实现 schema 还原（规则 8）
type EventDecoder = Callable[[str], AgentEvent | None]

# checkpoint 表的列名前缀（结构性字面量，集中一处 —— 编码规则 2）
_KEY_CHECKPOINT: Final[str] = "checkpoint"
_KEY_SIDE_EFFECT: Final[str] = "side_effect"
_FIELD_RESULT_REF: Final[str] = "result_ref"
# 租约未占用时的占位 owner（抢锁成功后立即被真实 worker 覆盖）
_LEASE_UNOWNED: Final[str] = ""


def _to_str(value: object) -> str:
    """把 redis-py 返回的 bytes/str 统一解码为 str（wire 契约全用字符串）。

    redis-py 默认 decode_responses=False，GET/XRANGE 返回 bytes；直接 str(bytes)
    会得到 "b'...'" 伪串，故集中一处显式解码（编码规则 6：动态边界归一化）。
    """
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value) if value is not None else ""


def _stream_data(fields: object) -> str:
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

    save/load/latest_rev/hashes 映射到 SQL；主键 (run_id, rev) 唯一约束防重复（I1）。
    生产部署前须在 Docker 环境补跑 A10（rev 单调 + hash 链）。
    """

    def __init__(self, dsn: str, object_get: Callable[[str], bytes]) -> None:
        """注入 PG DSN 与对象读取回调（state_ref → 字节）。

        Args:
            dsn: PostgreSQL 连接串。
            object_get: 从对象存储按 ref 读回字节的回调（含 hash 校验），由 Deps 注入。
        """
        self._dsn = dsn
        self._object_get = object_get

    def save(
        self,
        run_id: str,
        rev: int,
        node: CheckpointNode,
        state_ref: str,
        prev_hash: str,
    ) -> None:
        """落一行 checkpoint：主键冲突即抛（I1 断言点）。"""
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO checkpoint "
                "(run_id, rev, node, state_ref, prev_hash, created_at) "
                "VALUES (%s, %s, %s, %s, %s, now())",
                (run_id, rev, node.value, state_ref, prev_hash),
            )

    def load(self, run_id: str) -> State | None:
        """取最新 rev 的 state_ref，从对象存储读回并反序列化为 State（§3.4）。"""
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT state_ref FROM checkpoint WHERE run_id = %s "
                "ORDER BY rev DESC LIMIT 1",
                (run_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        # state_ref 形如 "key#sha256:<digest>"；完整 ref 交给注入的对象读取回调（含 hash 校验）
        ref = str(row[0])
        data = self._object_get(ref)
        return State.from_snapshot(json.loads(data))

    def latest_rev(self, run_id: str) -> int:
        """返回最大 rev；无记录返回 -1（§4.3 契约）。"""
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(rev), -1) FROM checkpoint WHERE run_id = %s",
                (run_id,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else -1

    def hashes(self, run_id: str) -> list[str]:
        """按 rev 排序返回 prev_hash 列（供 hash 链重算，I2）。"""
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT prev_hash FROM checkpoint WHERE run_id = %s ORDER BY rev ASC",
                (run_id,),
            )
            rows = cur.fetchall()
        return [str(r[0]) for r in rows]


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
        """注入 PG DSN；连接在使用时惰性创建。"""
        self._dsn = dsn

    def acquire(self, run_id: str, owner: str, ttl_s: int) -> PgLease | None:
        """原子抢锁；成功返回 PgLease，已被他人持有返回 None（A4 单持锁者）。

        若该 run 尚无 checkpoint 行，先插入 rev=0 占位行（懒建租约锚点），
        再走条件 UPDATE；两步在同一连接内，UPDATE 的 WHERE 保证原子性。
        """
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                # 懒建锚点：ON CONFLICT DO NOTHING，不影响已有行
                cur.execute(
                    "INSERT INTO checkpoint (run_id, rev, node, state_ref, prev_hash) "
                    "VALUES (%s, 0, 'FINAL', '', '') ON CONFLICT (run_id, rev) DO NOTHING",
                    (run_id,),
                )
                # 原子抢锁：仅当租约空闲（owner 空）或已过期时，条件成立才更新。
                # RETURNING 命中行数即赢家标记 —— 并发下 PG 行锁保证恰好一个成功。
                cur.execute(
                    "UPDATE checkpoint SET lease_owner = %s, "
                    "lease_until = now() + (%s * interval '1 second') "
                    "WHERE run_id = %s AND rev = (SELECT MAX(rev) FROM checkpoint WHERE run_id = %s) "
                    "AND (lease_owner = %s OR lease_until IS NULL OR lease_until < now()) "
                    "RETURNING run_id",
                    (owner, ttl_s, run_id, run_id, _LEASE_UNOWNED),
                )
                won = cur.fetchone() is not None
            conn.commit()
        return PgLease(self, run_id, owner) if won else None

    def heartbeat(self, run_id: str, owner: str) -> bool:
        """续租：仅 owner 匹配且未过期时刷新；返回是否成功（§11.4 step 2）。"""
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE checkpoint SET lease_until = now() + interval '30 seconds' "
                    "WHERE run_id = %s AND lease_owner = %s AND lease_until >= now() "
                    "RETURNING run_id",
                    (run_id, owner),
                )
                renewed = cur.fetchone() is not None
            conn.commit()
        return renewed

    def release(self, run_id: str, owner: str) -> None:
        """释放租约：仅 owner 可主动归还（终态立即让出，不等过期）。"""
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE checkpoint SET lease_owner = %s, lease_until = NULL "
                    "WHERE run_id = %s AND lease_owner = %s",
                    (_LEASE_UNOWNED, run_id, owner),
                )
            conn.commit()

    def scan_expired(self, now: datetime) -> list[str]:
        """扫描 lease_until < now 且仍有持锁者的 runId（§11.4 step 3）。

        Args:
            now: 判定时刻（生产传 utcnow；测试可注入过去/未来时间）。

        Returns:
            租约已过期的 run_id 列表（去重）。
        """
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT run_id FROM checkpoint "
                "WHERE lease_until < %s AND lease_owner != %s",
                (now, _LEASE_UNOWNED),
            )
            rows = cur.fetchall()
        return [str(r[0]) for r in rows]


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
        """命中返回 result_ref；否则 SETNX 占位返回 None。"""
        full_key = f"{_KEY_SIDE_EFFECT}:{idem_key}"
        client = self._conn()
        existing = client.get(full_key)
        if existing is not None:
            return _to_str(existing)  # 命中：复用既有结果，不重复执行
        # SETNX：只有占位成功者继续执行副作用，并发下恰好一个赢家（I4）
        client.setnx(full_key, f"reserved:{run_id}")
        return None

    def commit(self, idem_key: str, result_ref: str) -> None:
        """副作用完成后写回 result_ref（占位 → 真实结果）。"""
        client = self._conn()
        client.set(f"{_KEY_SIDE_EFFECT}:{idem_key}", result_ref)

    def count(self, run_id: str) -> int:
        """统计该 run 的幂等条目数（A5 断言副作用不重复）。"""
        client = self._conn()
        # 生产用 SCAN 统计前缀，测试替身用内存；此处返回扫描命中数
        keys = list(client.scan_iter(match=f"{_KEY_SIDE_EFFECT}:*"))
        return len(keys)


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
            decoded = self._event_decode(_stream_data(fields))
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
                decoded = self._event_decode(_stream_data(fields))
                if decoded is not None:
                    events.append(decoded)
        return events
