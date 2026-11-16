# Runbook · LLM Provider 切换与回滚演练（M20 交付物）

> **状态**：M20 §8.4 DoD 要求在 fixture 环境完成一次全流程演练留档；本文为演练骨架，随首次真实切换补充实测数据。
> **关联**：`docs/modules/M20-LLMProvider模型接入与切换.md`（流程定义）、`M19-评测与优化体系.md`（回归矩阵与实验卡）、`M11-治理观测与成本域.md`（灰度开关与回滚审计）。

## 1. 切换前置检查

- [ ] `model_providers.yaml` 新版本已过 schema 校验（`model_providers.schema.json`，M02 CI）
- [ ] 能力矩阵完整：新 provider 的每个 model_id 已登记 `context_window / max_output_tokens / json_mode / vision / prompt_caching / cost_per_1m / latency_class / sse_quirks`（缺任一项 fail-fast）
- [ ] per-adapter 录制回放契约测试全绿（CI，不真实调用 API）
- [ ] 回归范围矩阵圈定（`change_type=model_provider` → golden + replay(full) + adversarial + matrix + rag_golden + shadow，见 M19 §3.6A）
- [ ] 实验卡已创建（假设、五版本配置快照、dataset_rev），状态 `DRAFT`

## 2. 影子验证

1. M10 eval-runner 双跑：baseline（当前配置）vs candidate（新配置），同 dataset_rev 同输入。
2. 产出对比（M18 TraceExport）：spec_hash 一致率、断言结果一致率、成本差、时延差。
3. 判定：`GateVerdict = GATE_OK`（安全维度一票否决）；`INEFFECTIVE_OPTIMIZATION` / `GATE_FAIL` → 停止，记 `REJECT`。

## 3. 灰度放量（M11 开关）

| 阶段 | 范围 | 观察指标 | 回退触发 |
|---|---|---|---|
| 内部单用户 | 内部账号 | usage 归一正确、SSE 无断帧 | 任一异常即回退 |
| 限流扩展 | 5% → 25% → 50% 用户 | 成本 P95、错误率、回边率 | 成本 P95 超阈值或错误率突增 |
| 全量 | 100% | 持续观察一个完整周期 | — |

每阶段数据回填实验卡；`decide(ADOPT)` 后 M11 开关前置检查通过才允许下一阶段。

## 4. 回滚（任何阶段可执行）

1. M11 kill switch 按用户/比例切回旧 `model_config_version`（配置版本化，秒级生效）。
2. 在途会话：M04 checkpoint 保留，恢复后按旧配置继续（不重复扣费，幂等副作用表保证）。
3. embedding 切换的回滚：恢复旧 `retrieval_snapshot` 索引（旧快照保留，见 embedding-migration runbook）。
4. 写 `AuditEvent`（`action=MODEL_SWITCH_ROLLBACK`）并在实验卡记 `ROLLBACK`。

## 5. 演练留档

- [ ] fixture 环境：切换 → 影子 → 灰度（模拟开关）→ 回滚 全流程一次，附 trace 对比截图/报告引用
- [ ] 故障注入：429/5xx/超时/链耗尽/能力矩阵缺失/密钥缺失（M20 §8.2）
- [ ] 演练结论与数据补充到本文
