# Runbook · Embedding Provider 切换与索引迁移（M20 交付物）

> **状态**：M20 §8.4 DoD 要求在 fixture 环境完成一次全流程演练留档；本文为演练骨架。
> **关联**：`docs/modules/M20-LLMProvider模型接入与切换.md`（embedding 配置段）、`M10-知识检索与评测域.md`（索引快照与入库门禁）、`docs/runbook/provider-switch.md`（通用切换流程）。

## 1. 为什么 embedding 切换更危险

M10 的检索索引与 embedding 版本**强绑定**：向量库里的每条向量都是用某个 embedding 模型算的。新旧 embedding 混用会导致检索质量不可解释地劣化——这是 Provider 切换里最容易踩的坑。因此规则是：**切换 embedding 必须全量重建索引并升版 `retrieval_snapshot`，旧快照完整保留可回滚**。

## 2. 迁移步骤

1. **前置**：`embedding_providers` 配置段登记新模型（`name / model_id / dim / cost`，M20 §6.2）；确认 `dim` 与现向量库 schema 兼容性。
2. **dim 不变的迁移**：后台全量重算向量（M10 `POST /api/corpus/index/rebuild`，scope=all）→ 生成新 `retrieval_snapshot` → 旧快照保留。
3. **dim 变化的迁移**：新建并行向量集合（双写期）→ 全量回填 → 金标 RAG 评测（M19：hit@k / MRR / nDCG 对照）→ 通过后切读新集合 → 观察一个周期 → 下线旧集合。
4. **评测门禁**：RAG 金标 + D2 全量回放（M19 回归矩阵：retrieval/embedding 行）；判定规则含「一次通过率↑但沙箱通过率↓ = 无效优化」。
5. **生效**：`retrieval_snapshot` 升版写入 manifest（M09）；缓存键含 `retrieval_snapshot`，自然失效旧缓存。
6. **回滚**：manifest/配置指回旧 `retrieval_snapshot`（旧索引未删除，秒级回滚）；审计留证。

## 3. 演练留档

- [ ] fixture 环境：dim 不变与 dim 变化两种迁移各演练一次
- [ ] RAG 金标对照报告引用（baseline vs candidate）
- [ ] 回滚演练：切回旧快照并验证检索结果一致
