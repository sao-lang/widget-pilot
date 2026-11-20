"""M06 校验链生产装配（A1，v1.8 接线）。

把 M04 dispatcher 的轻量 ``ValidatorFn(str, dict) -> ValidationResult`` 接到
M06 ``validate_chain`` 的完整入参上：装配层负责把任务字段里的附件 id 解析成
``AttachmentView``、把事件 payload 桥接到 Redis 事件总线，dispatcher 不感知
embedder/store 等重依赖（默认零依赖实现由 validate_chain 内部兜底）。

L2/L3/L4 真实模型端口（embedder/classifier/gateway）由 M20/M07 提供，属
「需真实模型」排除项：本装配显式不注入，走 validate_chain 的默认结构化兜底，
并在降级时经 ``on_fallback`` 告警（接 M11 属范围外，本模块仅日志）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Final

import psycopg
import redis
from sqlalchemy.exc import SQLAlchemyError

from packages.agent_core.types import AgentEvent
from packages.contracts.enums import GridSpec, ReasonCode
from packages.contracts.types import TaskFields
from services.control_plane.attachments.stores import AttachmentRow
from services.guardrails.ports import Embedder, IntentClassifier, IntentGateway
from services.guardrails.types import AttachmentView, EventPayload, ValidationResult
from services.orchestrator.validate_chain import validate_chain

_KEY_CHECKPOINT: Final[str] = "checkpoint"
_SEQ_PREFIX: Final[str] = "wp:guardrail:eventseq:"
_DEFAULT_BUDGET_LEFT: Final[float] = float("inf")

logger = logging.getLogger(__name__)


def _attachment_view(row: AttachmentRow, extracted_text: str = "") -> AttachmentView:
    """把 M05 的 AttachmentRow 映射成 M06 视图（C1：role_confidence 正名）。

    Args:
        row: M05 产出的附件行（含 role / role_confidence / insight_ref）。
        extracted_text: 已提取文本（不可信数据，供 M06 不可信扫描；默认空）。

    Returns:
        M06 校验链可用的附件视图；role_confidence 取自 M05 正名字段，
        绝不取 insight_quality（双 confidence 隔离，C4）。
    """
    role = row.role.value if isinstance(row.role, Enum) else row.role
    return AttachmentView(
        attachment_id=row.id,
        role=str(role) if role is not None else None,
        role_confidence=float(row.role_confidence),
        insight_ref=row.insight_ref,
        extracted_text=extracted_text,
    )


def _make_event_sink(
    client: redis.Redis, session_id: str
) -> Callable[[EventPayload], None]:
    """构造事件桥接：把 M06 EventPayload 写入 M04 Redis 事件总线。

    session_id 由装配闭包按当前校验任务的会话绑定——M06 的 EventPayload 本身
    不含 session_id（判定逻辑与归属解耦），若从 payload 取会落到空 key 丢事件。
    seq 用 Redis 原子自增（per-session），与会话事件同域，避免多 worker 序号冲突；
    kind 直接透传（guard/routing/skill/plan/notice/error 均属 AgentEventKind）。

    Args:
        client: 复用的 Redis 客户端（m3：build_validator 外层建一次，避免每任务新建连接池）。
        session_id: 当前校验任务的会话标识（事件归属）。

    Returns:
        接收 EventPayload 的 sink 闭包。
    """

    def sink(payload: EventPayload) -> None:
        """把一条 M06 payload 转换为 AgentEvent 写入事件流。"""
        seq = int(client.incr(f"{_SEQ_PREFIX}{session_id}"))
        event = AgentEvent(
            seq=seq,
            session_id=session_id,
            run_id="",
            ts=datetime.now(UTC).isoformat(),
            kind=payload.kind,
            phase="",
            payload=payload.payload,
            trace_id="",
        )
        key = f"{_KEY_CHECKPOINT}:events:{session_id}"
        client.xadd(
            key,
            {
                "data": json.dumps(
                    {
                        "seq": event.seq,
                        "kind": event.kind,
                        "payload": event.payload,
                    },
                    default=str,
                )
            },
        )

    return sink


def build_validator(
    attachment_get: Callable[[str], AttachmentRow | None],
    redis_url: str,
    dsn: str,
    embedder: Embedder | None = None,
    classifier: IntentClassifier | None = None,
    gateway: IntentGateway | None = None,
) -> Callable[[str, TaskFields], ValidationResult]:
    """装配生产校验链入口，供 Dispatcher 注入（A1）。

    Args:
        attachment_get: 按 id 取附件行的回调（生产为 AttachmentStore.get）。
        redis_url: Redis 连接串（事件桥接用）。
        dsn: PG 连接串（A3）；注入五处生产留证（guard_hit + 三张日志表 +
            输入快照审计，复用 M04 AuditEvent 表），进程重启可复算。
            guard_hit PG 不可用时保守拒绝放行（M06 §2.3）。
        embedder: L2 端口（M20 提供；None 走默认零依赖实现）。
        classifier: L3 端口（M20/M07 提供；None 走默认兜底）。
        gateway: L4 端口（M07 提供；None 走默认结构化兜底）。

    Returns:
        符合 ValidatorFn 签名的闭包：``(description, fields) -> ValidationResult``。
    """
    # m3（v1.8）：Redis 客户端建一次，所有校验任务复用（长驻 worker 避免每任务
    # 新建连接池导致 FD 堆积）。
    redis_client = redis.from_url(redis_url)
    # A3（v1.8）：五处生产留证统一由 PG 注入，源码不再保留内存兜底。
    from services.guardrails.audit_pg import PgGuardrailAuditStore
    from services.guardrails.guard_hit_pg import PgGuardHitStore
    from services.guardrails.pg_stores import (
        PgIntentRouteLogStore,
        PgNeedSkillLogStore,
        PgSkillPrefetchLogStore,
    )

    route_log = PgIntentRouteLogStore(dsn)
    prefetch_log = PgSkillPrefetchLogStore(dsn)
    need_skill_log = PgNeedSkillLogStore(dsn)
    hit_store = PgGuardHitStore(dsn)
    audit_store = PgGuardrailAuditStore(dsn)

    def validator(description: str, fields: TaskFields) -> ValidationResult:
        """把 dispatcher 的扁平字段映射成 validate_chain 入参并执行校验。"""
        session_id = str(fields.get("session_id", ""))
        run_id = str(fields.get("run_id", ""))
        # 事件 sink 按当前会话绑定（M06 payload 不含 session_id，避免落到空 key）。
        event_sink = _make_event_sink(redis_client, session_id)
        # grid 保持原始字符串交给 validate_chain 强制转换：它内部对非法枚举返回
        # INPUT_INVALID 确定性拒绝；装配层若先行 GridSpec() 抛 ValueError 会绕过该
        # 守卫，导致 dispatcher 走 except 不 ACK 无限重试（ conduct 铁律：确定性失败
        # 须返回结论而非异常）。clarified_grid 同理。
        grid_raw = fields.get("grid")
        grid = str(grid_raw) if grid_raw else None
        # clarified_grid 直接进 validate_semantic→resolve_grid，后者要求 GridSpec
        # 枚举且非法值抛 ValueError；装配层在此转换，非法值降级 None（让 M06 从
        # 描述重新解析），避免异常击穿「校验链恒返回 ValidationResult」契约。
        clarified_raw = fields.get("clarified_grid")
        clarified_grid: GridSpec | None = None
        if clarified_raw:
            try:
                clarified_grid = GridSpec(str(clarified_raw))
            except ValueError:
                clarified_grid = None

        attachments: list[AttachmentView] = []
        raw_att_ids = fields.get("attachment_ids")
        att_ids = (
            raw_att_ids
            if isinstance(raw_att_ids, (list, tuple))
            else ([raw_att_ids] if raw_att_ids else [])
        )
        for att_id in att_ids:
            row = attachment_get(str(att_id))
            if row is not None:
                attachments.append(_attachment_view(row))

        # L1 意图分类：check_input 的 intent 白名单只放行 WIDGET_GENERATION；
        # validate_chain step0 已用 is_out_of_scope 短路越界，能走到此处即放行方向。
        # 传空串会被 check_input 误判为越界（INTENT_OUT_OF_SCOPE），必须给实际分类。
        from services.guardrails.intent_cascade import _l1_rule_intent
        from services.guardrails.types import IntentCategory

        l1_intent = _l1_rule_intent(description) or IntentCategory.WIDGET_GENERATION
        # R2（复审）：澄清重入标记透传——grid_confirmed 由此决定，不依赖 grid 解析成功。
        clarification_reply = str(fields.get("clarification_reply", "")) == "1"

        try:
            return validate_chain(
                session_id=session_id,
                run_id=run_id,
                description=description,
                grid=grid,
                attachments=attachments,
                intent=l1_intent.value,
                budget_left=_DEFAULT_BUDGET_LEFT,  # M11 未接：宽裕默认，不误拒
                clarified_grid=clarified_grid,
                clarification_reply=clarification_reply,
                event_sink=event_sink,
                on_fallback=lambda reason: logger.warning(
                    "guardrail fallback: %s", reason
                ),
                hit_store=hit_store,
                audit_store=audit_store,
                embedder=embedder,
                classifier=classifier,
                gateway=gateway,
                route_log=route_log,
                prefetch_log=prefetch_log,
                need_skill_log=need_skill_log,
            )
        except (psycopg.Error, SQLAlchemyError):
            # M06 §2.3 + runbook §2：任一留证表写入失败 → 保守拒绝放行，
            # 绝不降级内存（无证据放行）。guard_hit 走 psycopg，审计走 SQLAlchemy。
            # m2/R2-1（v1.8）：evidence_ref 用项目 canonical 快照哈希生成，
            # 与 validate_chain 同口径（M11 verify_snapshot 可回放）。
            from services.guardrails.audit import (
                build_snapshot,
                evidence_ref,
                snapshot_hash,
            )

            canonical_ref = evidence_ref(
                session_id, run_id, snapshot_hash(build_snapshot(description, grid, attachments))
            )
            return ValidationResult(
                ok=False,
                reason_code=ReasonCode.GUARD_INPUT_HIT,
                trace_id=f"{session_id}:{run_id}",
                evidence_ref=canonical_ref,
            )

    return validator
