# 需求覆盖矩阵 · 多 agent 回退为多任务下单 agent

> 用户原话逐条抄录；决策四类：采用 / 否决 / 进Open / 范围外。规格见同目录 `f76a7264-rolelayer-rollback.spec.md`。

| 需求 | 决策 | FR | 验收 |
|---|---|---|---|
| R1 当前项目已被硬改成多 agent，要改回单 agent 架构 | 采用：worker 停止注入 role_runner + dispatcher 删角色分支，子任务直接走 run() | FR-1 | AC-1 `test_single_agent_path.py` 新增：无 role_runner 时 dispatcher 直调 run 产出 done（pytest 通过） |
| R2 保留"多任务"能力（多 widget 任务扇出 + 汇总） | 采用：planner/fanout/coordinator fan-in/task_turn 全部原样保留 | FR-2 | AC-2 现有 fan-out 集成测试 `test_m06_fanout_dispatcher_pg.py` 全通过（不修改） |
| R3 从"多任务下的多 agent"改成"多任务下的单 agent"——只去掉子任务内的角色协作 | 采用：角色层（roles/router/Generator/Reviewer/黑板）作为可选短路分支移除 | FR-1 | AC-3 dispatcher 中 `run_role_loop`/`Router`/`self._role_runner` 全仓零调用（grep 证据） |
| R4 先出新修改方案，再改代码 | 采用：S0–S2 出规格+契约并由用户确认后，S3 才改码（流程纪律，已完成） | FR-0 | AC-4 dispatcher 子任务失败 reason_code 落 terminal.reason_code（见契约 AC-4） |
| R5 （用户未明确，但隐含）改动要能验证、不引入回归 | 采用：TDD + 现有测试基线 + ruff/pyright/contracts 门禁 | FR-3 | AC-5 `.venv/Scripts/python.exe -m pytest -q` 全绿（删角色层测试后基线） |
| R6 （隐含）不要破坏契约/前端消费 | 采用（用户拍板删干净）：删三种角色 kind + role/agentId 字段，kind 21→18 四同步；turn 字段保留（任务层语义） | FR-4 | AC-6 `pnpm contracts:check` drift=[]（kind 18 且四生成物同步） |
| R7 （隐含）DB/迁移不制造新风险 | 采用（用户拍板删干净）：0018 drop 两表 + drop generation_task.role 列，保留 turn 列 | FR-5 | AC-7 `alembic upgrade head` → 0018 且 downgrade 可回 0017 |
| R8 （隐含）文档与架构决策同步 | 采用：ADR-007 与精读 02 标注废弃，新建回退 ADR，006/009/00 总览同步 | FR-6 | AC-8 全仓 grep ADR-007 处均有"已废弃/被取代"标注（除历史留痕段） |
