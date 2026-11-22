"""M07 记忆的 PostgreSQL 真实存储（ADR-002 D-9~D-14）。

语义记忆 ``user_preference``（字段证据化 + version）、情节记忆 ``preference_event``
（只追加 + hash 链 + 失效不删行）、程序记忆 ``user_procedure``（零 LLM 采纳计数）。

与 guard_hit_pg 同纪律：PG 失败抛异常（fail-closed），**绝不降级为非持久化实现**；
事件 hash 链写入在单事务内用 advisory xact lock 串行化，读末行 row_hash 后追加。
SQL 全部由 SQLAlchemy ORM/方言表达，不依赖具体 DBAPI。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, replace
from typing import Final, Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from packages.agent_core.types import JsonValue
from services.control_plane.db import make_session_factory, session_scope
from services.control_plane.models import (
    PreferenceEvent as PreferenceEventRow,
)
from services.control_plane.models import (
    UserPreference as PreferenceRow,
)
from services.control_plane.models import (
    UserProcedure as ProcedureRow,
)
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

_GENESIS: Final[str] = "0" * 64
# 固定 bigint 写锁键：串行化偏好写入 + 事件链追加，防并发断链/丢版本。
_PREF_ADVISORY_KEY: Final[int] = 2026112102
_PROC_ADVISORY_KEY: Final[int] = 2026112103


def _json_text(value: JsonValue | None) -> str:
    """JSON 值的确定性文本（键排序、无空格），供 hash 与跨语言复算。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _encode_fields(fields: dict[PreferenceField, FieldEvidence]) -> dict[str, object]:
    """领域字段映射 → JSONB 可序列化的 dict（StrEnum 键转字符串）。"""
    return {key.value: asdict(value) for key, value in fields.items()}


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


def _event_from_row(row: Mapping[str, object]) -> PreferenceEvent:
    """PG 行 → 事件（验链用；非法枚举/字段抛错，不静默容忍脏数据）。"""
    old_value = cast(JsonValue | None, row["old_value"])
    new_value = cast(JsonValue | None, row["new_value"])
    return PreferenceEvent(
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]) if row["session_id"] else None,
        field_name=PreferenceField(str(row["field_name"])),
        old_value=old_value,
        new_value=new_value,
        source=PreferenceSource(str(row["source"])),
        source_weight=float(cast(float, row["source_weight"])),
        decision=PreferenceDecision(str(row["decision"])),
        trace_id=str(row["trace_id"]),
        created_at=str(row["created_at"]),
        superseded_at=str(row["superseded_at"]) if row["superseded_at"] else None,
        revoked_at=str(row["revoked_at"]) if row["revoked_at"] else None,
        row_hash=str(row["row_hash"]),
    )


def _load_preference(session: Session, user_id: str) -> UserPreference:
    """在已加锁事务内读取偏好；无记录返回空偏好（version=1）。"""
    row = session.execute(
        select(PreferenceRow.fields, PreferenceRow.version).where(
            PreferenceRow.user_id == user_id
        )
    ).first()
    if row is None:
        return UserPreference(user_id=user_id)
    return UserPreference(
        user_id=user_id,
        fields=_decode_fields(row.fields),
        version=int(row.version),
    )


def _upsert_preference(session: Session, pref: UserPreference) -> None:
    """整行 upsert：fields 全量覆盖，version 写调用方算好的绝对值。"""
    fields = _encode_fields(pref.fields)
    session.execute(
        pg_insert(PreferenceRow)
        .values(user_id=pref.user_id, fields=fields, version=pref.version)
        .on_conflict_do_update(
            index_elements=["user_id"], set_={"fields": fields, "version": pref.version}
        )
    )


def _mark_events(
    session: Session,
    user_id: str,
    field_name: PreferenceField,
    column: Literal["superseded_at", "revoked_at"],
    now: str,
) -> None:
    """把该用户该字段仍有效的 ADD/UPDATE 事件标记失效（只改列，不删行）。"""
    target = getattr(PreferenceEventRow, column)
    session.execute(
        update(PreferenceEventRow)
        .where(PreferenceEventRow.user_id == user_id)
        .where(PreferenceEventRow.field_name == field_name.value)
        .where(target.is_(None))
        .where(
            PreferenceEventRow.decision.in_(
                (PreferenceDecision.ADD.value, PreferenceDecision.UPDATE.value)
            )
        )
        .values(**{column: now})
    )


def _append_event(session: Session, event: PreferenceEvent) -> str:
    """追加一条事件并返回行哈希（读末行 row_hash 串成链）。"""
    last = session.execute(
        select(PreferenceEventRow.row_hash)
        .order_by(PreferenceEventRow.id.desc())
        .limit(1)
    ).first()
    prev_hash = str(last.row_hash) if last else _GENESIS
    row_hash = _event_hash(event, prev_hash)
    session.add(
        PreferenceEventRow(
            user_id=event.user_id,
            session_id=event.session_id,
            field_name=event.field_name.value,
            old_value=event.old_value,
            new_value=event.new_value,
            source=event.source.value,
            source_weight=event.source_weight,
            decision=event.decision.value,
            superseded_at=event.superseded_at,
            revoked_at=event.revoked_at,
            trace_id=event.trace_id,
            created_at=event.created_at,
            row_hash=row_hash,
        )
    )
    return row_hash


class PgPreferenceStore:
    """语义 + 情节记忆 PG 存储：四操作写入 + 事件 hash 链（PG 失败抛异常）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（每次读写开短 Session，跨实例天然读回同一份数据）。"""
        self._factory = make_session_factory(dsn)

    def get(self, user_id: str) -> UserPreference:
        """按 user_id 读取偏好；无记录返回空偏好（version=1，fields 为空）。"""
        with session_scope(self._factory) as session:
            row = session.execute(
                select(PreferenceRow.fields, PreferenceRow.version).where(
                    PreferenceRow.user_id == user_id
                )
            ).first()
        if row is None:
            return UserPreference(user_id=user_id)
        return UserPreference(
            user_id=user_id,
            fields=_decode_fields(row.fields),
            version=int(row.version),
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
        value = new_value
        with session_scope(self._factory) as session:
            session.execute(select(func.pg_advisory_xact_lock(_PREF_ADVISORY_KEY)))
            pref = _load_preference(session, user_id)
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
                _upsert_preference(session, pref)
                # UPDATE 才失效旧证据；ADD 没有旧证据可失效。
                if decision is PreferenceDecision.UPDATE:
                    _mark_events(session, user_id, field_name, "superseded_at", now)
            # NOOP 分支：不碰偏好表、版本不变，仅落审计事件（I-M3）。
            row_hash = _append_event(session, event)
        return pref, replace(event, row_hash=row_hash)

    def clear(
        self,
        user_id: str,
        fields: list[PreferenceField] | None,
        trace_id: str,
    ) -> UserPreference:
        """清除全字段或指定字段：值置空、version+1、事件保留 revoked_at。"""
        with session_scope(self._factory) as session:
            session.execute(select(func.pg_advisory_xact_lock(_PREF_ADVISORY_KEY)))
            pref = _load_preference(session, user_id)
            targets = (
                list(pref.fields)
                if fields is None
                else [f for f in fields if f in pref.fields]
            )
            now = utc_now_iso()
            for field_name in targets:
                old = pref.fields.pop(field_name)
                _mark_events(session, user_id, field_name, "revoked_at", now)
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
                _append_event(session, deleted)
            pref.version += 1
            _upsert_preference(session, pref)
        return pref

    def verify_event_chain(self) -> bool:
        """从 PG 读回全链：按 id 顺序复算 prev_hash 衔接与行哈希是否一致。"""
        with session_scope(self._factory) as session:
            rows = session.execute(
                select(
                    PreferenceEventRow.user_id,
                    PreferenceEventRow.session_id,
                    PreferenceEventRow.field_name,
                    PreferenceEventRow.old_value,
                    PreferenceEventRow.new_value,
                    PreferenceEventRow.source,
                    PreferenceEventRow.source_weight,
                    PreferenceEventRow.decision,
                    PreferenceEventRow.trace_id,
                    PreferenceEventRow.created_at,
                    PreferenceEventRow.superseded_at,
                    PreferenceEventRow.revoked_at,
                    PreferenceEventRow.row_hash,
                ).order_by(PreferenceEventRow.id)
            ).all()
        expected_prev = _GENESIS
        for row in rows:
            event = _event_from_row(cast(Mapping[str, object], row._mapping))
            if event.row_hash != _event_hash(event, expected_prev):
                return False
            expected_prev = event.row_hash
        return True


class PgProcedureStore:
    """程序记忆 PG 存储：显式采纳 +1 / 撤销 -1（归零失效不删行）。"""

    def __init__(self, dsn: str) -> None:
        """注入 PG DSN（表内只存结构化短字段，不写 prompt 前缀，I-P1）。"""
        self._factory = make_session_factory(dsn)

    def get_for_type(self, user_id: str, widget_type: str) -> list[UserProcedure]:
        """按用户 + 控件类型读取采纳记录；含归零行（count=0 不注入由端口判定）。"""
        with session_scope(self._factory) as session:
            rows = session.execute(
                select(
                    ProcedureRow.user_id,
                    ProcedureRow.recipe_id,
                    ProcedureRow.widget_type,
                    ProcedureRow.adopt_count,
                    ProcedureRow.last_adopted_at,
                    ProcedureRow.version,
                )
                .where(ProcedureRow.user_id == user_id)
                .where(ProcedureRow.widget_type == widget_type)
                .order_by(ProcedureRow.recipe_id)
            ).all()
        return [
            UserProcedure(
                user_id=str(row.user_id),
                recipe_id=str(row.recipe_id),
                widget_type=str(row.widget_type),
                adopt_count=int(row.adopt_count),
                last_adopted_at=str(row.last_adopted_at),
                version=int(row.version),
            )
            for row in rows
        ]

    def record_adopt(
        self,
        user_id: str,
        recipe_id: str,
        widget_type: str,
        trace_id: str,  # 接口契约参数名；U1 表暂无 trace 列故未落库
    ) -> UserProcedure:
        """显式采纳 +1（首次插入为 1）、version+1。"""
        now = utc_now_iso()
        with session_scope(self._factory) as session:
            session.execute(select(func.pg_advisory_xact_lock(_PROC_ADVISORY_KEY)))
            stmt = (
                pg_insert(ProcedureRow)
                .values(
                    user_id=user_id,
                    recipe_id=recipe_id,
                    widget_type=widget_type,
                    adopt_count=1,
                    last_adopted_at=now,
                    version=1,
                )
                .on_conflict_do_update(
                    index_elements=["user_id", "recipe_id"],
                    set_={
                        "widget_type": widget_type,
                        "adopt_count": ProcedureRow.adopt_count + 1,
                        "last_adopted_at": now,
                        "version": ProcedureRow.version + 1,
                    },
                )
                .returning(
                    ProcedureRow.user_id,
                    ProcedureRow.recipe_id,
                    ProcedureRow.widget_type,
                    ProcedureRow.adopt_count,
                    ProcedureRow.last_adopted_at,
                    ProcedureRow.version,
                )
            )
            row = session.execute(stmt).mappings().first()
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
        with session_scope(self._factory) as session:
            session.execute(select(func.pg_advisory_xact_lock(_PROC_ADVISORY_KEY)))
            session.execute(
                update(ProcedureRow)
                .where(ProcedureRow.user_id == user_id)
                .where(ProcedureRow.recipe_id == recipe_id)
                .values(
                    adopt_count=func.greatest(ProcedureRow.adopt_count - 1, 0),
                    version=ProcedureRow.version + 1,
                )
            )
