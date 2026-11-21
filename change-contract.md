# Change: ADR-004 + ADR-005 / 2026-11-21 / confirmed（级联去类型化 + 进护栏强化）

## Why
用户要求「不要限定死 widget 类型」→ 级联整条去类型化；随后指出进护栏注入/PII 两表力度不够且缺黄/暴/政敏感词校验，并要求把残余的 `_HINT_PATTERNS` 类型映射也清掉。现状 PII 只匹配形状会误报、注入仅 3 条、无敏感词检测、`intent_hints._HINT_PATTERNS` 仍从描述推断类型。必须在同一变更中完成检测强化与类型彻底清除。

## Delta
- 替换 `WIDGET_GENERATION_PATTERNS` / `OUT_OF_SCOPE_PATTERNS` 为语义族联合条件；`patterns/intent.py` 从 `INTENT_GUARD_PATTERNS` 同源派生。
- 替换 `WIDGET_HINTS`/`WIDGET_TYPICAL_GRIDS` 为锚点/开放特征词与 `DENSITY_HINTS`；L2 样本/词表 v2 去类型；`change_gate` 增词表/样本版本指纹。
- 删除 `validate_chain._infer_widget` 与 `intent_hints._HINT_PATTERNS`（含 `intent_hints.py` 模块）；`ValidationResult.widget_type` 字段保留但值恒 `WidgetType.CUSTOM`。
- 扩展 `patterns/injection.py` 3→8 条规则（越狱角色/编码外泄/分隔符注入/间接注入/凭据外泄）。
- 新增 `validators.py`：PII 形状命中后经 `phonenumbers`/`id-validator`/`email-validator` 真值校验；`rule_registry.scan` 串联。
- 新增 `sensitive.py` + `patterns/sensitive_lexicon.json`：`SensitiveWordDetector` 用 `pyahocorasick` Aho-Corasick 扫描 PORN/VIOLENCE/POLITICAL；POLITICAL 默认空，由合规方经 `WIDGETPILOT_SENSITIVE_LEXICON_PATH` 外置；命中只留 `<sensitive:CATEGORY>`。
- 新增 `GuardHitType.SENSITIVE`；`check_input` 五类判定（注入/PII → 敏感词 → 意图 → 预算），新增可注入 `sensitive_detector` 参数。
- 依赖：`pyproject.toml` + `uv.lock` 新增 4 个运行期库；`typings/ahocorasick.pyi` 与 `typings/id_validator/*.pyi`（零 `type: ignore`）。
- 待删符号：`WIDGET_HINTS`、`WIDGET_TYPICAL_GRIDS`、`_detect_widget`、`_infer_widget`、`intent_hints._HINT_PATTERNS`、`services.guardrails.intent_hints`（模块）。

## Invariants
- L1 零模型调用、正向优先、返回 `None` 可继续级联；I14 拒绝类恒出自 L1（三处校验原样保留）。
- `IntentCategory`/`RouteLayer`/ReasonCode/事件 schema/SDK 契约/widget 类型枚举不改；`ValidationResult.widget_type` 字段保留（值恒 `custom`）；六个 fallback 归因串原样保留。
- 硬样本：`今天天气怎么样`→拒；三条含天气/聊天/翻译控件请求→放行；`能帮我在桌面加个天气吗？` L1 与 check_input 均放行；`给我整一个能显示日期的小玩意`→L1 未命中。
- PII 留证永远掩码；敏感词留证只写类别，不写词原文；涉政词库不进代码仓库。
- 不新增模型调用；不因依赖缺失静默跳过检测（外置词库非法即 fail-fast）。

## Affected
- 代码：`services/guardrails/{constants,input_guard,rule_registry,semantic,grid_resolver,semantic_router,change_gate,sensitive,validators,types}.py`、`patterns/{injection,intent,sensitive_lexicon.json}`、`services/orchestrator/validate_chain.py`；删除 `intent_hints.py`。
- 测试：`tests/unit/test_m06_intent_decoupled.py`、`test_m06_input_guard_hardened.py`（新增）、`test_m06_acceptance.py`、`test_m06_completeness.py` 等。
- 文档：`docs/adr/ADR-004`、`docs/adr/ADR-005`（新增）、`设计方案.md`、`docs/modules/M06-输入校验护栏与路由域.md`、`docs/M06-代码阅读指南.md`、`docs/modules/M06-模型依赖与测试缺口标注.md`、`docs/M4-M6-全文件速查表.md`、`docs/modules/00-总览与模块地图.md`。
- 影响面：`impact_analysis.py` 扫过级联符号；`widget_type` 无生产决策消费者；recipes frontmatter 零消费者。

## Acceptance
- `pytest -q` 退出码 0（当前 **429 passed / 49 skipped**）；新增行为测试覆盖 FR-1～FR-13 与 ADR-005 §6。
- `ruff check .`、`npx pyright`、`pnpm contracts:check` drift=[]、`contract_check.py`、`commitment_ledger.py --strict`、`removal_check.py`、`source_purity.py --strict`、`review_gate.py` 全退出 0。
- 新增/删除符号由 `removal_check.py` 核对；`importlib.util.find_spec("services.guardrails.intent_hints") is None`。
- 依赖变更后 `uv.lock` 含 4 个新库；镜像重建待 Docker 可用（环境阻断登记）。

## Open
- 无（涉政词库为空是 ADR-005 明示设计：须由合规方外置；未出现用户否决项）。
