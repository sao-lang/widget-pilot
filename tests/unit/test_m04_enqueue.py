"""M04 提交描述触发任务入队单测（B1b：控制面 → 队列接线）。

验证 post_message 在会话迁移 RUNNING 后把任务推入队列，
携带 session_id/run_id/description/grid 供 dispatcher 消费。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from services.orchestrator.dispatcher import Task
from tests.support import (
    InMemorySessionStore,
    InMemoryUserStore,
    build_test_app,
    install_test_awaiting_route,
)

SECRET = "test-secret-not-production-32bytes-minimum"


class RecordingEnqueuer:
    """内存任务采集器（测试替身，验证 post_message 入队调用）。"""

    def __init__(self) -> None:
        self.tasks: list[Task] = []

    def __call__(self, task: Task) -> str:
        self.tasks.append(task)
        return "test-msg-id"


def _client(enqueuer: RecordingEnqueuer) -> TestClient:
    app = build_test_app(
        user_store=InMemoryUserStore(),
        session_store=InMemorySessionStore(),
        jwt_secret=SECRET,
        task_enqueuer=enqueuer,
    )
    install_test_awaiting_route(app)
    return TestClient(app)


def _register_and_session(client: TestClient) -> tuple[str, str]:
    """注册 + 建会话，返回 (token, session_id)。"""
    client.post(
        "/api/auth/register", json={"email": "a@x.com", "password": "Passw0rd!"}
    )
    token = client.post(
        "/api/auth/login", json={"email": "a@x.com", "password": "Passw0rd!"}
    ).json()["data"]["access_token"]
    sid = client.post(
        "/api/sessions",
        json={"title": "t", "grid": "4x4"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()["data"]["id"]
    return token, sid


class TestSubmitEnqueuesTask:
    """post_message 成功后入队，携带完整任务载荷。"""

    def test_submit_enqueues_task(self) -> None:
        """提交描述 → 队列收到 1 条含 session_id/run_id/description/grid 的任务。"""
        enqueuer = RecordingEnqueuer()
        client = _client(enqueuer)
        token, sid = _register_and_session(client)
        resp = client.post(
            f"/api/sessions/{sid}/messages",
            json={
                "description": "做一个日历",
                "grid": "4x4",
                "attachment_ids": ["att-1", "att-2"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202
        assert len(enqueuer.tasks) == 1
        task = enqueuer.tasks[0]
        assert task["session_id"] == sid
        assert task["user_id"]  # review B1：真实 owner 供挂起回写校验
        assert task["run_id"] == resp.json()["data"]["runId"]
        assert task["description"] == "做一个日历"
        assert task["grid"] == "4x4"
        # C1/A1（v1.8）：附件 id 随任务下传（此前为死参数），供校验链解析 AttachmentView。
        assert task["attachment_ids"] == ["att-1", "att-2"]

    def test_no_enqueue_on_conflict(self) -> None:
        """二次提交（状态冲突 400）不入队（不重复触发生成）。"""
        enqueuer = RecordingEnqueuer()
        client = _client(enqueuer)
        token, sid = _register_and_session(client)
        body = {"description": "d", "grid": "4x4", "attachment_ids": []}
        client.post(
            f"/api/sessions/{sid}/messages",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        client.post(
            f"/api/sessions/{sid}/messages",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(enqueuer.tasks) == 1  # 只入队一次

    def test_clarification_reenqueues_with_clarified_grid(self) -> None:
        """A2（v1.8）：澄清回答带 grid 后重入队，载荷含 clarified_grid。"""
        enqueuer = RecordingEnqueuer()
        client = _client(enqueuer)
        token, sid = _register_and_session(client)
        # 先提交 → 入队第 1 条；再把会话置为 AWAITING_INPUT（测试钩子）
        client.post(
            f"/api/sessions/{sid}/messages",
            json={"description": "做一个", "grid": "", "attachment_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        client.post(
            f"/api/sessions/{sid}/_test_awaiting",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = client.post(
            f"/api/sessions/{sid}/clarifications",
            json={"question_id": "q1", "answer": "4x4 日历", "grid": "4x4"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202
        assert len(enqueuer.tasks) == 2  # 提交 1 条 + 澄清重入队 1 条
        clarified = enqueuer.tasks[1]
        assert clarified["clarified_grid"] == "4x4"
        assert clarified["grid"] == "4x4"
        # R3-1（终审）：description 必须是首次提交的原始需求，不是 answer——
        # 否则澄清后模型只收到"4x4 日历"，丢失控件/特征需求
        assert clarified["description"] == "做一个"
        assert clarified["clarification_reply"] == "1"  # R2：澄清标记透传
        assert clarified["user_id"]  # 真实 owner 回写用

    def test_textual_grid_answer_resolved(self) -> None:
        """R2-1（复审）：纯文本规格回答 "四行四列" 经 resolve_grid 解析为 4x4 入队。"""
        enqueuer = RecordingEnqueuer()
        client = _client(enqueuer)
        token, sid = _register_and_session(client)
        client.post(
            f"/api/sessions/{sid}/messages",
            json={"description": "做一个", "grid": "", "attachment_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        client.post(
            f"/api/sessions/{sid}/_test_awaiting",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = client.post(
            f"/api/sessions/{sid}/clarifications",
            json={"question_id": "q1", "answer": "四行四列"},  # 未显式传 grid
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202
        task = enqueuer.tasks[1]
        assert task["clarified_grid"] == "4x4"  # 纯文本 → resolve_grid 别名解析
        assert task["clarification_reply"] == "1"

    def test_empty_answer_rejected(self) -> None:
        """R2-2（复审）：空回答 422 拒绝，不越过 R1 护栏。"""
        enqueuer = RecordingEnqueuer()
        client = _client(enqueuer)
        token, sid = _register_and_session(client)
        client.post(
            f"/api/sessions/{sid}/messages",
            json={"description": "做一个", "grid": "", "attachment_ids": []},
            headers={"Authorization": f"Bearer {token}"},
        )
        client.post(
            f"/api/sessions/{sid}/_test_awaiting",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = client.post(
            f"/api/sessions/{sid}/clarifications",
            json={"question_id": "q1", "answer": "", "grid": "4x4"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422  # min_length=1 拒绝空回答
        assert len(enqueuer.tasks) == 1  # 未入队（澄清未生效）
