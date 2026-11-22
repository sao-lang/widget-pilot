"""ADR-010 §4 会话修正单测（撤销违法合并 + RUNNING/PAUSED 改需求重跑）。

TDD RED→GREEN：纯逻辑与端点分支分离验证。测试桩（SpyEnqueuer / FakeTaskStore）
仅在本文件内联，生产源码保持纯净（无 Fake/Stub/type ignore）。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from packages.contracts.enums import SessionState
from services.control_plane.api.guard import ForbiddenError
from services.control_plane.api.server import create_app
from services.control_plane.sessions.revision import (
    default_revision_phrases,
    detect_revocation_intent,
    merge_revised_description,
    strip_dangerous_segments,
)
from services.control_plane.sessions.service import (
    REVISION_ACTION,
    REVISION_DECISION_MERGE,
    REVISION_DECISION_RERUN,
    ConflictError,
    SessionService,
)
from services.orchestrator.dispatcher import Task
from services.orchestrator.tasks.pg_stores import GenerationTaskData, TaskTurnData
from services.orchestrator.tasks.ports import TaskStorePort
from tests.support import (
    AuditLedger,
    InMemorySessionStore,
    InMemoryUserStore,
    SSEWindow,
    build_test_app,
    install_test_awaiting_route,
)

_JWT_SECRET = "test-only-secret-32-bytes-minimum"
_PHRASES = default_revision_phrases()


# ---- 测试桩（仅本文件内联） ------------------------------------------------


class SpyEnqueuer:
    """记录入队任务的测试桩（生产为 RedisTaskQueue.enqueue）。"""

    def __init__(self) -> None:
        """初始化空队列。"""
        self.items: list[Task] = []

    def __call__(self, task: Task) -> str:
        """记录任务并返回稳定消息 id。"""
        self.items.append(task)
        return "msg-id"


class FakeTaskStore:
    """generation_task 内存桩，满足 TaskStorePort（生产为 PgGenerationTaskStore）。"""

    def __init__(self, tasks: Sequence[GenerationTaskData]) -> None:
        """按 task id 索引初始化。"""
        self._tasks: dict[str, GenerationTaskData] = {t.id: t for t in tasks}

    def create_many(self, tasks: Sequence[GenerationTaskData]) -> None:
        """幂等插入多条任务。"""
        for task in tasks:
            self._tasks[task.id] = task

    def get(self, task_id: str) -> GenerationTaskData | None:
        """按 id 读一行。"""
        return self._tasks.get(task_id)

    def list_by_session(self, session_id: str) -> list[GenerationTaskData]:
        """列出会话内任务。"""
        return [t for t in self._tasks.values() if t.session_id == session_id]

    def allocate_turn(self, task_id: str) -> int:
        """分配迭代序号（桩固定返回 1）。"""
        return 1

    def update_state(
        self,
        task_id: str,
        state: str,
        *,
        run_id: str | None = None,
        result_ref: str | None = None,
        reason_code: str | None = None,
        evidence_ref: str | None = None,
        current_bundle_ref: str | None = None,
        last_turn_index: int | None = None,
        turn: int | None = None,
        role: str | None = None,
    ) -> None:
        """更新任务状态（桩只改 state，其余参数忽略但签名对齐端口）。"""
        current = self._tasks.get(task_id)
        if current is None:
            return
        self._tasks[task_id] = replace(current, state=state)

    def append(self, turn: TaskTurnData) -> None:
        """task_turn 追加（桩空实现，本测试不触发）。"""
        return

    def list_by_task(self, task_id: str) -> list[TaskTurnData]:
        """task_turn 列表（桩空实现）。"""
        return []


# ---- 测试辅助 --------------------------------------------------------------


def _running_task(session_id: str, task_id: str = "task-1") -> GenerationTaskData:
    """构造一条 RUNNING 的 generation_task 行。"""
    return GenerationTaskData(
        id=task_id,
        session_id=session_id,
        parent_run_id="parent-1",
        original_description="做一个时钟",
        description="做一个时钟",
        task_index=1,
        task_total=1,
        state="RUNNING",
        created_at=datetime.now(UTC),
    )


def _register_and_token(client: TestClient, email: str = "rev@example.com") -> str:
    """注册用户并返回 access token。"""
    resp = client.post(
        "/api/auth/register", json={"email": email, "password": "password123"}
    )
    assert resp.status_code == 200
    return str(resp.json()["data"]["access_token"])


def _create_session(client: TestClient, token: str) -> str:
    """新建会话返回 session_id。"""
    resp = client.post(
        "/api/sessions",
        json={"title": "rev", "grid": "4x4"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    return str(resp.json()["data"]["id"])


def _post_message(
    client: TestClient, token: str, session_id: str, description: str
) -> tuple[int, dict[str, object]]:
    """提交一条 message，返回状态码与 data。"""
    resp = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"description": description},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = resp.json()
    return resp.status_code, dict(body.get("data") or {})


def _build_api_app(
    enqueuer: SpyEnqueuer,
    *,
    ledger: AuditLedger | None = None,
    store: InMemorySessionStore | None = None,
    task_store: TaskStorePort | None = None,
) -> TestClient:
    """用测试替身装配 create_app（仅注入本次需要的依赖）。"""
    app = create_app(
        InMemoryUserStore(),
        store or InMemorySessionStore(),
        _JWT_SECRET,
        event_source=SSEWindow(window_n=10),
        attachment_service=None,
        audit=ledger or AuditLedger(),
        task_enqueuer=enqueuer,
        task_store=task_store,
    )
    return TestClient(app)


# ---- 场景 1：撤销违法输入合并（§4.1）纯逻辑 -------------------------------


class TestRevocationPureLogic:
    """撤销意图识别 + 违法片段剥离 + 合并，全部确定性零模型。"""

    def test_revocation_intent_detected(self) -> None:
        """撤销句式命中：不进行/取消/改成合法 等。"""
        assert detect_revocation_intent("不进行违法动作了", _PHRASES.revocations) is True
        assert detect_revocation_intent("改成合法的时钟", _PHRASES.revocations) is True
        assert detect_revocation_intent("去掉那些违规操作", _PHRASES.revocations) is True

    def test_no_revocation_intent_for_normal_request(self) -> None:
        """普通控件请求不带撤销意图，防止误伤正常提交。"""
        assert detect_revocation_intent("做一个时钟", _PHRASES.revocations) is False

    def test_strip_dangerous_clauses_keeps_safe_intent(self) -> None:
        """命中违法词的整句被剥离，剩余有效意图保留，返回命中的词供审计。"""
        stripped, hits = strip_dangerous_segments(
            "做一个时钟，顺便窃取数据，绕过沙箱后群发短信",
            _PHRASES.dangerous_actions,
        )
        assert stripped == "做一个时钟"
        assert "窃取数据" in hits
        assert "绕过沙箱" in hits

    def test_merge_preserves_followup_and_drops_illegal(self) -> None:
        """合并结果 = T1 合法片段 + T2 意图，且不含任何违法词。"""
        merged = merge_revised_description(
            "做一个时钟，顺便窃取数据",
            "不进行违法动作了，改成合法的时钟",
            dangerous_phrases=_PHRASES.dangerous_actions,
            revocation_phrases=_PHRASES.revocations,
        )
        assert merged.revoked is True
        assert "窃取数据" not in merged.description
        assert "做一个时钟" in merged.description
        assert "改成合法的时钟" in merged.description
        assert "窃取数据" in merged.stripped_phrases

    def test_merge_all_dangerous_original_falls_back_to_followup(self) -> None:
        """T1 全是违法内容（剥离后为空）时合并退化为 T2 意图。"""
        merged = merge_revised_description(
            "窃取数据并绕过沙箱",
            "改成合法的四则运算控件",
            dangerous_phrases=_PHRASES.dangerous_actions,
            revocation_phrases=_PHRASES.revocations,
        )
        assert merged.description == "改成合法的四则运算控件"

    def test_merge_is_deterministic(self) -> None:
        """同输入两次合并结果逐字段一致（纯函数）。"""
        kwargs = {
            "dangerous_phrases": _PHRASES.dangerous_actions,
            "revocation_phrases": _PHRASES.revocations,
        }
        first = merge_revised_description("做一个时钟，窃取数据", "不做违法动作", **kwargs)
        second = merge_revised_description("做一个时钟，窃取数据", "不做违法动作", **kwargs)
        assert first == second

    def test_lexicon_is_shared_with_guard_detect(self) -> None:
        """词表从 §3 同一 dangerous_actions.json 加载，避免双份事实来源。"""
        assert "窃取数据" in _PHRASES.dangerous_actions
        assert "不进行" in _PHRASES.revocations


# ---- SessionService.submit_revision（新增方法） ---------------------------


class TestSubmitRevisionService:
    """会话修正重入：状态机、original_description 槽位、审计留痕。"""

    def test_awaiting_input_merge_keeps_original(self) -> None:
        """撤销合并：AWAITING_INPUT→RUNNING，T1 原文不覆盖，新 run_id。"""
        store = InMemorySessionStore()
        ledger = AuditLedger()
        svc = SessionService(store, audit=ledger)
        row = svc.create("u1", "rev", "4x4")
        submitted = svc.submit_message(row.id, "u1", "做一个时钟，窃取数据", [])
        old_run = str(submitted.run_id)
        svc.mark_awaiting_input(row.id, "u1", "GUARD_INPUT_HIT")

        revised = svc.submit_revision(
            row.id,
            "u1",
            "做一个时钟，改成合法的时钟",
            keep_original=True,
            revision=REVISION_DECISION_MERGE,
            detail={"stripped_phrases": "窃取数据"},
        )
        assert revised.state is SessionState.RUNNING
        assert revised.run_id != old_run
        assert store.get(row.id).original_description == "做一个时钟，窃取数据"  # type: ignore[union-attr]
        audits = [e for e in ledger.by_session(row.id) if e.action == REVISION_ACTION]
        assert len(audits) == 1
        assert audits[0].decision == REVISION_DECISION_MERGE
        assert audits[0].payload["stripped_phrases"] == "窃取数据"

    def test_running_rerun_overwrites_description_and_keeps_checkpoint(self) -> None:
        """改需求重跑：RUNNING→RUNNING，新描述入槽，checkpoint_rev 不丢。"""
        store = InMemorySessionStore()
        ledger = AuditLedger()
        svc = SessionService(store, audit=ledger)
        row = svc.create("u1", "rev", "4x4")
        submitted = svc.submit_message(row.id, "u1", "做一个时钟", [])
        old_run = str(submitted.run_id)
        checked = svc.interrupt(row.id, "u1")  # RUNNING→PAUSED，checkpoint_rev+1
        rev = checked.checkpoint_rev

        revised = svc.submit_revision(
            row.id, "u1", "改成农历日历", revision=REVISION_DECISION_RERUN
        )
        assert revised.state is SessionState.RUNNING
        assert revised.run_id != old_run
        assert revised.checkpoint_rev == rev
        assert store.get(row.id).original_description == "改成农历日历"  # type: ignore[union-attr]
        assert any(e.action == "interrupt" for e in ledger.by_session(row.id))

    def test_submit_revision_rejects_terminal(self) -> None:
        """终态会话不可重入（与 submit_message 同一保护）。"""
        store = InMemorySessionStore()
        svc = SessionService(store, audit=AuditLedger())
        row = svc.create("u1", "rev", "4x4")
        svc.submit_message(row.id, "u1", "做一个时钟", [])
        svc.complete(row.id, "bundle-1")
        with pytest.raises(ConflictError):
            svc.submit_revision(row.id, "u1", "改成农历")

    def test_submit_revision_rejects_foreign_user(self) -> None:
        """租户隔离：非 owner 重入抛 ForbiddenError。"""
        store = InMemorySessionStore()
        svc = SessionService(store, audit=AuditLedger())
        row = svc.create("u1", "rev", "4x4")
        svc.submit_message(row.id, "u1", "做一个时钟", [])
        with pytest.raises(ForbiddenError):
            svc.submit_revision(row.id, "u2", "改成农历")


# ---- 端点分支：场景 1 端到端 ----------------------------------------------


class TestScenario1Endpoint:
    """AWAITING_INPUT + 撤销意图 → 合并后作为新 message 入队，T1 原文不删。"""

    def test_revoke_merge_endpoint(self) -> None:
        """T1 违法被挂起 → T2 撤销 → 合并描述入队 + original_description 保留 T1。"""
        enqueuer = SpyEnqueuer()
        ledger = AuditLedger()
        store = InMemorySessionStore()
        app = build_test_app(
            session_store=store,
            audit=ledger,
            task_enqueuer=enqueuer,
        )
        install_test_awaiting_route(app)
        client = TestClient(app)
        token = _register_and_token(client, "s1@example.com")
        sid = _create_session(client, token)

        status, first = _post_message(client, token, sid, "做一个时钟，顺便窃取数据")
        assert status == 202
        await_resp = client.post(
            f"/api/sessions/{sid}/_test_awaiting",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert await_resp.status_code == 200
        assert await_resp.json()["data"]["state"] == "AWAITING_INPUT"

        status, second = _post_message(
            client, token, sid, "不进行违法动作了，改成合法的时钟"
        )
        assert status == 202
        assert second["runId"] != first["runId"]
        assert second["state"] == "RUNNING"

        merged_task = enqueuer.items[-1]
        # T1 违法片段被剥离；T2 撤销+改合法意图原文保留（连接词归一）。
        assert merged_task["description"] == "做一个时钟，不进行违法动作了，改成合法的时钟"
        assert "窃取数据" not in str(merged_task["description"])
        assert merged_task["revision"] == REVISION_DECISION_MERGE
        assert store.get(sid).original_description == "做一个时钟，顺便窃取数据"  # type: ignore[union-attr]
        merge_audits = [e for e in ledger.by_session(sid) if e.action == REVISION_ACTION]
        assert len(merge_audits) == 1
        assert merge_audits[0].decision == REVISION_DECISION_MERGE

    def test_awaiting_input_without_revocation_still_rejected(self) -> None:
        """无撤销意图的 AWAITING_INPUT 提交保持原行为（400），不误入合并分支。"""
        enqueuer = SpyEnqueuer()
        app = build_test_app(task_enqueuer=enqueuer)
        install_test_awaiting_route(app)
        client = TestClient(app)
        token = _register_and_token(client, "s2@example.com")
        sid = _create_session(client, token)
        _post_message(client, token, sid, "做一个时钟")
        client.post(
            f"/api/sessions/{sid}/_test_awaiting",
            headers={"Authorization": f"Bearer {token}"},
        )
        status, _ = _post_message(client, token, sid, "再做一个日历")
        assert status == 400
        assert len(enqueuer.items) == 1


# ---- 端点分支：场景 2 改需求重跑 ------------------------------------------


class TestScenario2Endpoint:
    """RUNNING/PAUSED + 新描述 → interrupt 幂等 + 旧任务 CANCELLED + 新 run_id。"""

    def test_running_rerun_cancels_old_task(self) -> None:
        """RUNNING 改需求：旧任务取消，新 run 入队，审计/检查点保留。"""
        enqueuer = SpyEnqueuer()
        ledger = AuditLedger()
        store = InMemorySessionStore()
        # session_id 建会话后才确定：先占位，再迁到真实 session_id（桩仅内联）。
        task_store = FakeTaskStore([_running_task("placeholder")])
        client = _build_api_app(
            enqueuer, ledger=ledger, store=store, task_store=task_store
        )
        token = _register_and_token(client, "s3@example.com")
        sid = _create_session(client, token)
        # 把预置任务迁到真实 session_id 上（桩仅内联，生产由 PG 落库）。
        old = task_store.get("task-1")
        assert old is not None
        task_store.create_many([replace(old, session_id=sid)])

        status, first = _post_message(client, token, sid, "做一个时钟")
        assert status == 202
        status, second = _post_message(client, token, sid, "改成农历日历")
        assert status == 202
        assert second["runId"] != first["runId"]
        assert second["state"] == "RUNNING"

        cancelled = task_store.get("task-1")
        assert cancelled is not None
        assert cancelled.state == "CANCELLED"

        rerun_task = enqueuer.items[-1]
        assert rerun_task["description"] == "改成农历日历"
        assert rerun_task["run_id"] == second["runId"]
        assert rerun_task["revision"] == REVISION_DECISION_RERUN
        assert store.get(sid).checkpoint_rev == 1  # type: ignore[union-attr]
        audits = ledger.by_session(sid)
        assert any(e.action == "interrupt" for e in audits)
        assert any(
            e.action == REVISION_ACTION and e.decision == REVISION_DECISION_RERUN
            for e in audits
        )

    def test_duplicate_description_while_running_keeps_400(self) -> None:
        """描述未变不算"改需求"：重复提交仍是 400 且不入队（M04 回归）。"""
        enqueuer = SpyEnqueuer()
        client = _build_api_app(enqueuer)
        token = _register_and_token(client, "s7@example.com")
        sid = _create_session(client, token)
        _post_message(client, token, sid, "做一个时钟")
        status, _ = _post_message(client, token, sid, "做一个时钟")
        assert status == 400
        assert len(enqueuer.items) == 1

    def test_paused_rerun_is_idempotent_on_interrupt(self) -> None:
        """已 PAUSED 的会话重入：interrupt 幂等，不重复加 checkpoint。"""
        enqueuer = SpyEnqueuer()
        ledger = AuditLedger()
        store = InMemorySessionStore()
        client = _build_api_app(enqueuer, ledger=ledger, store=store)
        token = _register_and_token(client, "s4@example.com")
        sid = _create_session(client, token)
        _post_message(client, token, sid, "做一个时钟")
        pause = client.post(
            f"/api/sessions/{sid}/interrupt",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert pause.status_code == 200

        status, _ = _post_message(client, token, sid, "改成深色主题")
        assert status == 202
        assert store.get(sid).checkpoint_rev == 1  # type: ignore[union-attr]
        assert len([e for e in ledger.by_session(sid) if e.action == "interrupt"]) == 1

    def test_no_task_store_rerun_still_succeeds(self) -> None:
        """未注入 task_store 时重跑不被阻断（任务取消是可选增强）。"""
        enqueuer = SpyEnqueuer()
        client = _build_api_app(enqueuer)
        token = _register_and_token(client, "s5@example.com")
        sid = _create_session(client, token)
        _post_message(client, token, sid, "做一个时钟")
        status, _ = _post_message(client, token, sid, "改成农历")
        assert status == 202

    def test_normal_draft_message_unchanged(self) -> None:
        """零回归：DRAFT 正常提交不进入任何修正分支，载荷字段与旧版一致。"""
        enqueuer = SpyEnqueuer()
        client = _build_api_app(enqueuer)
        token = _register_and_token(client, "s6@example.com")
        sid = _create_session(client, token)
        status, data = _post_message(client, token, sid, "做一个时钟和一个日历")
        assert status == 202
        task = enqueuer.items[-1]
        assert task["description"] == "做一个时钟和一个日历"
        assert task["planning"] == "1"
        assert task["target_task_id"] == ""
        assert "revision" not in task
        assert "runId" in data
