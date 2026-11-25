"""P1 生产装配：确定性通道 runner 工厂（worker 注入 Dispatcher）。

集中构造真实 BundleService / S3EvidenceStore / DockerCliRunner / 事件扇出，
返回符合 ``DeterministicRunnerFn`` 的闭包 ``(Input, session_id, run_id) -> dict``。

诚实纪律：
- 缺 S3 配置或 JWT secret（BundleService confirm_secret 必填）时返回 None —— 不
  造假、不留内存桩冒充生产；Dispatcher 未注入 runner 即走原 LLM 链路（fail-open）。
- 通道结果直接来自 services.deterministic.channel.run_deterministic（真三门禁 +
  真成包）；本工厂不重复实现任何门禁/成包逻辑。
- 零云端 LLM：本模块不引用 ModelGateway，不调 model.complete。
"""
from __future__ import annotations

import logging
import os

from packages.agent_core.injection import ModelGateway
from packages.agent_core.types import Input
from services.deterministic.channel import run_deterministic

__all__ = ["build_deterministic_runner", "merge_clarification_into_description"]

logger = logging.getLogger(__name__)


def merge_clarification_into_description(inp: Input) -> str:
    """澄清重入：把 grid / clarification_answer 并入解释描述（B4）。

    规则解释器只接 description 文本；用户答「4x4」若只留在 Input.grid 而描述
    仍为原文，会二次判缺 grid → 追问死循环。补入后规则层同一次即收敛。
    """
    supplements = [s for s in (inp.grid, inp.clarification_answer) if s]
    return inp.text + (" " + " ".join(supplements) if supplements else "")


def build_deterministic_runner(
    *,
    dsn: str,
    redis_url: str,
    jwt_secret: str | None,
    container_cli_path: str = "",
    model_gateway: ModelGateway | None = None,
) -> object | None:
    """装配真实确定性 runner；依赖不全返回 None（fail-open 到 LLM，非造假）。

    ``model_gateway`` 是返工③的唯一真实模型接线点：由 worker 传 ``deps.model``，
    env ``WIDGETPILOT_REWORK_LLM=on`` 时才构造 GatewayReworkLLM；off/缺 gateway
    一律不注入端口（零 model.complete，fail-closed）。
    """
    if not jwt_secret:
        logger.warning("确定性通道未启用：缺 JWT_SECRET（BundleService confirm_secret 必填）")
        return None
    # 返工③ env 门控：off 默认零模型；on 且缺 gateway → fail-fast（不静默降级）。
    rework_llm = os.getenv("WIDGETPILOT_REWORK_LLM", "off").strip().lower()
    rework_port = None
    if rework_llm == "on":
        if model_gateway is None:
            raise RuntimeError(
                "WIDGETPILOT_REWORK_LLM=on 但缺 ModelGateway（返工③禁止静默降级）"
            )
        from services.orchestrator.rework.model_ports import GatewayReworkLLM

        rework_port = GatewayReworkLLM(model_gateway)
    elif rework_llm != "off":
        raise RuntimeError(f"WIDGETPILOT_REWORK_LLM 值非法：{rework_llm!r}（仅 on/off）")
    try:
        from packages.agent_core.tools.invoke import load as load_capability
        from services.control_plane.attachments.pg_stores import PgAttachmentStore
        from services.control_plane.audit.pg_ledger import PgAuditLedger
        from services.control_plane.bundles.adoption import M07ArtifactAdoption
        from services.control_plane.bundles.corpus_link import AuditBackedCorpusLink
        from services.control_plane.bundles.m04_events import M04ArtifactSink
        from services.control_plane.bundles.pg_store import PgBundleStore
        from services.control_plane.bundles.service import BundleService
        from services.orchestrator.adapters import (
            RedisEventBus,
            make_event_decoder,
        )
        from services.orchestrator.memory.pg_stores import (
            PgPreferenceStore,
            PgProcedureStore,
        )
        from services.orchestrator.tasks.events import RedisSeqAllocator
        from services.sandbox_runner.ports import DockerCliRunner, S3EvidenceStore
    except ImportError as exc:  # 依赖模块缺失 → 不启用
        logger.warning("确定性通道未启用：依赖模块缺失 %s", exc)
        return None

    try:
        evidence = S3EvidenceStore()  # 缺 S3_* env 时 from_env fail-fast
    except Exception as exc:  # noqa: BLE001 - S3 缺失属环境阻断，fail-open 到 LLM
        logger.warning("确定性通道未启用：S3 配置缺失 %s", exc)
        return None

    from services.orchestrator.deps import _DEFAULT_SSE_WINDOW

    bus = RedisEventBus(redis_url, _DEFAULT_SSE_WINDOW, make_event_decoder())
    audit = PgAuditLedger(dsn)
    pref_store = PgPreferenceStore(dsn)
    bundle_service = BundleService(
        store=PgBundleStore(dsn),
        audit=audit,
        events=M04ArtifactSink(bus, RedisSeqAllocator(redis_url)),
        corpus=AuditBackedCorpusLink(audit),
        preference_revocation=pref_store,
        adoption=M07ArtifactAdoption(pref_store, PgProcedureStore(dsn)),
        confirm_secret=jwt_secret,
    )
    docker_runner = DockerCliRunner()

    # P0 附件生成侧：注入真工具链（REGISTRY 33 能力）+ 附件 id→富对象解析。
    # 不注入则 capability_planner 分支静默不进，"取视频第 3.5 秒帧"等用途丢失。
    from services.orchestrator.attachment_refs import resolve_attachments

    attachment_store = PgAttachmentStore(dsn)

    def runner(inp: Input, session_id: str, run_id: str) -> dict[str, object]:
        # 澄清重入：用户回答（如「4x4」）必须并入解释输入，否则规则层再次判缺
        # grid → 追问死循环（review B4）；拼接逻辑抽成纯函数以便真实测。
        return dict(run_deterministic(
            merge_clarification_into_description(inp),
            attachments=tuple(resolve_attachments(inp.attachments, attachment_store)),
            load_fn=load_capability,
            bundle_service=bundle_service,
            evidence_store=evidence,
            source_store=evidence,
            docker_runner=docker_runner,
            source_key=f"code/{session_id}/{run_id}/widget.tsx",
            user_id=inp.context_user_id or "",
            session_id=session_id,
            run_id=run_id,
            rework_llm=rework_llm,
            rework_llm_port=rework_port,
        ))

    return runner
