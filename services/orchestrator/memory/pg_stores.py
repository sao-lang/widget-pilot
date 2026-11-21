"""M07 记忆的 PostgreSQL 真实存储（ADR-002 D-9~D-14）。

语义记忆 ``user_preference``（字段证据化 + version）、情节记忆 ``preference_event``
（只追加 + hash 链 + 失效不删行）、程序记忆 ``user_procedure``（零 LLM 采纳计数）。

与 guard_hit_pg 同纪律：PG 失败抛异常（fail-closed），**绝不降级为非持久化实现**；
事件 hash 链写入在单事务内用 advisory xact lock 串行化，读末行 row_hash 后追加。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import (  # purity-exempt: cast(Any,dict_row) 收口需要
    Any,  # purity-exempt: cast(Any,dict_row) 唯一收口点需要
    Final,
    Literal,
    cast,
)

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from packages.agent_core.types import JsonValue
from services.orchestrator.memory.learning import decide_operation
from services.orchestrator.memory.types import (
    SOURCE_WEIGHT,
    FieldEvidence,
    PreferenceDecision,
    PreferenceEvent,
    PreferenceField,
    PreferenceSource,
    UserPreference,
    UserProcedure,
    utc_now_iso,
)

_TABLE_PREFERENCE: Final[str] = "user_preference"
_TABLE_EVENT: Final[str] = "preference_event"
_TABLE_PROCEDURE: Final[str] = "user_procedure"
_GENESIS: Final[str] = "0" * 64
_ASYNCPG_PREFIX: Final[str] = "postgresql+asyncpg://"
# 固定 bigint 写锁键：串行化偏好写入 + 事件链追加，防并发断链/丢版本。
_PREF_ADVISORY_KEY: Final[int] = 2026112102
_PROC_ADVISORY_KEY: Final[int] = 2026112103


def _sync_dsn(url: str) -> str:
    """asyncpg DSN（compose 生产格式）→ psycopg 同步 DSN（B1）。

    Args:
        url: 原始 DSN，可能是 SQLAlchemy asyncpg 格式。

    Returns:
        psycopg 可直接连接的同步 DSN。
    """
    return (
        url.replace(_ASYNCPG_PREFIX, "postgresql://", 1)
        if url.startswith(_ASYNCPG_PREFIX)
        else url
    )


def _connect_dict(dsn: str) -> psycopg.Connection[DictRow]:
    """开返回 dict 行的同步连接（psycopg 类型桩未把 row_factory 纳入泛型推断）。"""
    # purity-exempt: dict_row 由 psycopg 运行时注入行工厂，类型桩无法表达其行类型，
    # 故在此唯一 helper 收口一次动态转换，下游只用 DictRow 具名视图。
    return cast(
        psycopg.Connection[DictRow],
        psycopg.connect(dsn, row_factory=cast(Any, dict_row)),  # purity-exempt: psycopg row_factory 类型桩缺口，唯一收口点
    )


def _json_text(value: JsonValue | None) -> str:
    """JSON 值的确定性文本（键排序、无空格），供 hash 与跨语言复算。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _encode_fields(fields: dict[PreferenceField, FieldEvidence]) -> Jsonb:
    """领域字段映射 → jsonb 值（StrEnum 由 json 自然序列化为字符串）。"""
    return Jsonb({key.value: asdict(value) for key, value in fields.items()})


def _as_float(value: JsonValue | None, default: float = 0.0) -> float:
    """把 JSON 边界值收窄为 float（非数值回退默认值，fail-safe）。"""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _as_int(value: JsonValue | None, default: int = 0) -> int:
    """把 JSON 边界值收窄为 int（非整数回退默认值，fail-safe）。"""
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def _decode_fields(raw: JsonValue) -> dict[PreferenceField, FieldEvidence]:
    """jsonb 列 → 领域字段映射（非法字段/枚举直接抛错，fail-closed）。"""
    result: dict[PreferenceField, FieldEvidence] = {}
    if not isinstance(raw, dict):
        return result
    for key, item in raw.items():
        if not isinstance(item, dict):
            continue
        result[PreferenceField(str(key))] = FieldEvidence(
            value=item.get("value"),
            source=PreferenceSource(str(item.get("source", ""))),
            weight=_as_float(item.get("weight")),
            evidence_count=_as_int(item.get("evidence_count")),
            updated_at=str(item.get("updated_at", "")),
        )
    return result


def _event_hash(event: PreferenceEvent, prev_hash: str) -> str:
    """复算事件行 hash：sha256(prev_hash + 各不可变字段的定序拼接)。

    ``superseded_at`` / ``revoked_at`` 在写入后仍会被 UPDATE，纳入 hash 会让链在
    正常失效流程下断裂，故只哈希写入时即确定的字段（D-13 失效后仍可验链）。
    """
    raw = "|".join(
        [
            prev_hash,
            event.user_id,
            event.session_id or "",
            event.field_name.value,
            _json_text(event.old_value),
            _json_text(event.new_value),
            event.source.value,
            str(event.source_weight),
            event.decision.value,
            event.trace_id,
            event.created_at,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_from_row(row: DictRow) -> PreferenceEvent:
    """PG 行 → 事件（验链用；非法枚举/字段抛错，不静默容忍脏数据）。"""
    return PreferenceEvent(
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]) if row["session_id"] else None,
        field_name=PreferenceField(str(row["field_name"])),
        old_value=row["old_value"],
        new_value=row["new_value"],
        source=PreferenceSource(str(row["source"])),
        source_weight=float(row["source_weight"]),
        decision=PreferenceDecision(str(row["decision"])),
        trace_id=str(row["trace_id"]),
        created_at=str(row["created_at"]),
        superseded_at=str(row["superseded_at"]) if row["superseded_at"] else None,
        revoked_at=str(row["revoked_at"]) if row["revoked_at"] else None,
        row_hash=str(row["row_hash"]),
    )


def _load_preference(cur: psycopg.Cursor[DictRow], user_id: str) -> UserPreference:
    """在已加锁事务内读取偏好；无记录返回空偏好（version=1）。"""
    cur.execute(
        f"SELECT fields, version FROM {_TABLE_PREFERENCE} WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if row is None:
        return UserPreference(user_id=user_id)
    return UserPreference(
        user_id=user_id,
        fields=_decode_fields(row["fields"]),
        version=int(row["version"]),
    )


def _upsert_preference(cur: psycopg.Cursor[DictRow], pref: UserPreference) -> None:
    """整行 upsert：fields 全量覆盖，version 写调用方算好的绝对值。"""
    cur.execute(
        f"INSERT INTO {_TABLE_PREFERENCE} (user_id, fields, version) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE "
        "SET fields = EXCLUDED.fields, version = EXCLUDED.version",
        (pref.user_id, _encode_fields(pref.fields), pref.version),
    )


def _mark_events(
    cur: psycopg.Cursor[DictRow],
    user_id: str,
    field_name: PreferenceField,
    column: Literal["superseded_at", "revoked_at"],
    now: str,
) -> None:
    """把该用户该字段仍有效的 ADD/UPDATE 事件标记失效（只改列，不删行）。"""
    cur.execute(
        f"UPDATE {_TABLE_EVENT} SET {column} = %s "
        "WHERE user_id = %s AND field_name = %s "
        f"AND {column} IS NULL AND decision IN (%s, %s)",
        (
            now,
            user_id,
            field_name.value,
            PreferenceDecision.ADD.value,
            PreferenceDecision.UPDATE.value,
        ),
    )


def _append_event(cur: psycopg.Cursor[DictRow], event: PreferenceEvent) -> str:
    """追加一条事件并返回行哈希（读末行 row_hash 串成链）。"""
    cur.execute(f"SELECT row_hash FROM {_TABLE_EVENT} ORDER BY id DESC LIMIT 1")
    last = cur.fetchone()
    prev_hash = str(last["row_hash"]) if last else _GENESIS
    row_hash = _event_hash(event, prev_hash)
    cur.execute(
        f"INSERT INTO {_TABLE_EVENT} "
        "(user_id, session_id, field_name, old_value, new_value, source, "
        "source_weight, decision, superseded_at, revoked_at, trace_id, "
        "created_at, row_hash) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            event.user_id,
            event.session_id,
            event.field_name.value,
            Jsonb(event.old_value),
            Jsonb(event.new_value),
            event.source.value,
            event.source_weight,
            event.decision.value,
            event.superseded_at,
            event.revoked_at,
            event.trace_id,
            event.created_at,
            row_hash,
        ),
    )
    return row_hash


class PgPreferenceStore:
    """语义 + 情节记忆 PG 存储：四操作写入 + 事件 hash 链（PG 失败抛异常）。

    Attributes:
        _dsn: 归一化后的 psycopg 同步 DSN（asyncpg 前缀已去除）。
    """

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短连接，跨实例天然读回同一份数据）。"""
        self._dsn = _sync_dsn(dsn)  # 归一化后的同步 DSN

    def get(self, user_id: str) -> UserPreference:
        """按 user_id 读取偏好；无记录返回空偏好（version=1，fields 为空）。"""
        with _connect_dict(self._dsn) as conn:
            row = conn.execute(
                f"SELECT fields, version FROM {_TABLE_PREFERENCE} "
                "WHERE user_id = %s",
                (user_id,),
            ).fetchone()
        if row is None:
            return UserPreference(user_id=user_id)
        return UserPreference(
            user_id=user_id,
            fields=_decode_fields(row["fields"]),
            version=int(row["version"]),
        )

    def apply(
        self,
        user_id: str,
        session_id: str | None,
        field_name: PreferenceField,
        new_value: JsonValue,
        source: PreferenceSource,
        trace_id: str,
    ) -> tuple[UserPreference, PreferenceEvent]:
        """四操作写入：返回写入后的偏好与刚追加的事件（含行哈希）。

        ADD/UPDATE 写偏好并 version+1；UPDATE 把旧事件标 superseded_at；
        NOOP 只记事件（低权重不覆盖高权重，I-M3）；DELETE 仅由 clear 入口触发。
        """
        value = cast(JsonValue, new_value)
        with _connect_dict(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_PREF_ADVISORY_KEY,))
            pref = _load_preference(cur, user_id)
            existing = pref.fields.get(field_name)
            decision = decide_operation(existing, source, value)
            now = utc_now_iso()
            event = PreferenceEvent(
                user_id=user_id,
                session_id=session_id,
                field_name=field_name,
                old_value=existing.value if existing else None,
                new_value=value,
                source=source,
                source_weight=SOURCE_WEIGHT[source],
                decision=decision,
                trace_id=trace_id,
                created_at=now,
            )
            if decision in (PreferenceDecision.ADD, PreferenceDecision.UPDATE):
                pref.fields[field_name] = FieldEvidence(
                    value=value,
                    source=source,
                    weight=SOURCE_WEIGHT[source],
                    evidence_count=existing.evidence_count + 1 if existing else 1,
                    updated_at=now,
                )
                pref.version += 1
                _upsert_preference(cur, pref)
                # UPDATE 才失效旧证据；ADD 没有旧证据可失效。
                if decision is PreferenceDecision.UPDATE:
                    _mark_events(cur, user_id, field_name, "superseded_at", now)
            # NOOP 分支：不碰偏好表、版本不变，仅落审计事件（I-M3）。
            row_hash = _append_event(cur, event)
        return pref, replace(event, row_hash=row_hash)

    def clear(
        self,
        user_id: str,
        fields: list[PreferenceField] | None,
        trace_id: str,
    ) -> UserPreference:
        """清除全字段或指定字段：值置空、version+1、事件保留 revoked_at。"""
        with _connect_dict(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_PREF_ADVISORY_KEY,))
            pref = _load_preference(cur, user_id)
            targets = (
                list(pref.fields)
                if fields is None
                else [f for f in fields if f in pref.fields]
            )
            now = utc_now_iso()
            for field_name in targets:
                old = pref.fields.pop(field_name)
                _mark_events(cur, user_id, field_name, "revoked_at", now)
                # 清除属显式操作：DELETE 事件的 source 记 EXPLICIT，新值置空。
                deleted = PreferenceEvent(
                    user_id=user_id,
                    session_id=None,
                    field_name=field_name,
                    old_value=old.value,
                    new_value=None,
                    source=PreferenceSource.EXPLICIT,
                    source_weight=SOURCE_WEIGHT[PreferenceSource.EXPLICIT],
                    decision=PreferenceDecision.DELETE,
                    trace_id=trace_id,
                    created_at=now,
                    revoked_at=now,
                )
                _append_event(cur, deleted)
            pref.version += 1
            _upsert_preference(cur, pref)
        return pref

    def verify_event_chain(self) -> bool:
        """从 PG 读回全链：按 id 顺序复算 prev_hash 衔接与行哈希是否一致。"""
        with _connect_dict(self._dsn) as conn:
            rows = conn.execute(
                f"SELECT * FROM {_TABLE_EVENT} ORDER BY id"
            ).fetchall()
        expected_prev = _GENESIS
        for row in rows:
            event = _event_from_row(row)
            if event.row_hash != _event_hash(event, expected_prev):
                return False
            expected_prev = event.row_hash
        return True


class PgProcedureStore:
    """程序记忆 PG 存储：显式采纳 +1 / 撤销 -1（归零失效不删行）。

    Attributes:
        _dsn: 归一化后的 psycopg 同步 DSN。
    """

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（表内只存结构化短字段，不写 prompt 前缀，I-P1）。"""
        self._dsn = _sync_dsn(dsn)  # 归一化后的同步 DSN

    def get_for_type(self, user_id: str, widget_type: str) -> list[UserProcedure]:
        """按用户 + 控件类型读取采纳记录；含归零行（count=0 不注入由端口判定）。"""
        with _connect_dict(self._dsn) as conn:
            rows = conn.execute(
                f"SELECT user_id, recipe_id, widget_type, adopt_count, "
                f"last_adopted_at, version FROM {_TABLE_PROCEDURE} "
                "WHERE user_id = %s AND widget_type = %s ORDER BY recipe_id",
                (user_id, widget_type),
            ).fetchall()
        return [
            UserProcedure(
                user_id=str(row["user_id"]),
                recipe_id=str(row["recipe_id"]),
                widget_type=str(row["widget_type"]),
                adopt_count=int(row["adopt_count"]),
                last_adopted_at=str(row["last_adopted_at"]),
                version=int(row["version"]),
            )
            for row in rows
        ]

    def record_adopt(
        self,
        user_id: str,
        recipe_id: str,
        widget_type: str,
        trace_id: str,  # purity-exempt: 接口契约参数名，U1 表暂无 trace 列故未落库
    ) -> UserProcedure:
        """显式采纳 +1（首次插入为 1）、version+1。

        Args:
            user_id: 租户隔离键。
            recipe_id: 被采纳的规程句柄。
            widget_type: 采纳控件类型。
            trace_id: 接口约定预留（U1 程序记忆表暂无 trace 列，不落库）。
        """
        now = utc_now_iso()
        with _connect_dict(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_PROC_ADVISORY_KEY,))
            cur.execute(
                f"INSERT INTO {_TABLE_PROCEDURE} "
                "(user_id, recipe_id, widget_type, adopt_count, "
                "last_adopted_at, version) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id, recipe_id) DO UPDATE SET "
                f"widget_type = EXCLUDED.widget_type, "
                f"adopt_count = {_TABLE_PROCEDURE}.adopt_count + 1, "
                f"last_adopted_at = EXCLUDED.last_adopted_at, "
                f"version = {_TABLE_PROCEDURE}.version + 1 "
                "RETURNING user_id, recipe_id, widget_type, adopt_count, "
                "last_adopted_at, version",
                (user_id, recipe_id, widget_type, 1, now, 1),
            )
            row = cur.fetchone()
        if row is None:  # INSERT ... RETURNING 必返一行；否则 fail-closed
            raise RuntimeError("user_procedure upsert 未返回行")
        return UserProcedure(
            user_id=str(row["user_id"]),
            recipe_id=str(row["recipe_id"]),
            widget_type=str(row["widget_type"]),
            adopt_count=int(row["adopt_count"]),
            last_adopted_at=str(row["last_adopted_at"]),
            version=int(row["version"]),
        )

    def record_revoke(self, user_id: str, recipe_id: str) -> None:
        """采纳撤销 -1（下限 0，归零只失效不删行）、version+1；无记录则无操作。"""
        with _connect_dict(self._dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_PROC_ADVISORY_KEY,))
            cur.execute(
                f"UPDATE {_TABLE_PROCEDURE} "
                "SET adopt_count = GREATEST(adopt_count - 1, 0), "
                "version = version + 1 "
                "WHERE user_id = %s AND recipe_id = %s",
                (user_id, recipe_id),
            )
