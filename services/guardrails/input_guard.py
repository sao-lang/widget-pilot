"""进 LLM 护栏 L1（§3.2 step7-10）：注入/PII → 敏感词 → 意图 → 预算。

四类判据各自独立、命中不进任何模型（I1/I14）。注入/PII 走确定性规则表 +
成熟库真值校验；敏感词走 Aho-Corasick 分类词库（涉政词库外置）；意图是上游
级联给出的分类，越界意图（OUT_OF_SCOPE）在 L1 落 INTENT 命中。
"""

from __future__ import annotations

import re

from services.guardrails.constants import (
    GRID_HINT_PATTERNS,
    INTENT_ALLOWLIST,
    WIDGET_ANCHOR_PATTERNS,
    WIDGET_GENERATION_PATTERNS,
)
from services.guardrails.dangerous_call import detect_dangerous_calls
from services.guardrails.rule_registry import default_registry, scan
from services.guardrails.sensitive import (
    SensitiveWordDetector,
    default_sensitive_detector,
)
from services.guardrails.types import (
    BudgetView,
    GuardHitType,
    GuardResult,
    HitDecision,
    InputGuardHit,
)

_OUT_OF_SCOPE_RULE_ID = "INTENT_OUT_OF_SCOPE"
_BUDGET_RULE_ID = "BUDGET_EXHAUSTED"


def _has_explicit_generation(description: str) -> bool:
    """ADR-020 D2：显式生成请求正向判定（生成动词 + 宿主 + grid 三者齐全）。

    与 `_l1_rule_intent` 的正向优先、`RuleInterpreter` 的 `has_positive` 同口径；
    只用于豁免"意图类"规则命中，注入/PII/敏感词等安全红线不受影响。
    无 grid 的教程请求（如「卡片怎么添加到桌面」）不满足此判定，仍被 N5 拒绝。
    """
    return (
        any(re.search(p, description, re.IGNORECASE) for p, _ in GRID_HINT_PATTERNS)
        and any(re.search(p, description, re.IGNORECASE) for p in WIDGET_ANCHOR_PATTERNS)
        and any(re.search(p, description, re.IGNORECASE) for p in WIDGET_GENERATION_PATTERNS)
    )


# ADR-020 D1 强化：混排意图的语义分类。
# 切分描述后逐段判「生成 / 教程咨询 / 内容描述」，只有「生成 + 教程」才反问。
_SEGMENT_SPLIT = re.compile(r"[,，;；。!！?？\n]+")
_TUTORIAL_SEMANTIC = re.compile(
    r"(?:怎么|如何|怎样|该怎么).{0,6}(?:添加|生成|创建|新建|用|设置|做|加|放|弄|安装|配置)"
    r"|(?:添加|设置|配置).{0,4}(?:方法|教程|步骤)"
    r"|(?:教|告诉|叫|让)我.{0,6}(?:怎么|如何|怎样|将|把|帮我)?(?:添加|生成|创建|新建|放|设置|装|弄|加)"
)
_GENERATION_SEMANTIC = re.compile(
    # 「怎么/如何添加」是教程动词，不是生成动作——用否定后顾排除
    r"(?<![怎么如何])(?:做|生成|创建|新建|弄|搞|来个|来一|整个|整一个|加|添加|放|摆|挂|贴|塞)"
    r".{0,12}(?:桌面|主屏|锁屏|负一屏|小组件|组件|万能卡片|服务卡片|卡片|挂件|面板|Widget|widget|控件|界面)"
    r"|(?<![怎么如何])(?:桌面|主屏|锁屏|负一屏).{0,8}(?:放|摆|挂|加|显示)"
)


def classify_intent_mix(description: str) -> tuple[str, tuple[str, ...]]:
    """按片段语义分类混排意图（零模型、零副作用）。

    返回 (kind, segments)：
    - ``clarification``：既有生成片段、又有教程/操作咨询片段 → 反问，
      segments 为两个原话片段供用户确认选哪个。
    - ``out_of_scope``：只有教程咨询、无生成 → 越界硬拒。
    - ``generation``：其余（纯生成、或 widget 内容描述附在生成后）→ 放行。

    ADR-004 硬样本「桌面放个2x2天气卡片，显示今天天气怎么样」的后半段
    没有教程语义（是内容描述），故判 generation 放行——不被误反问。
    """
    segments = tuple(s.strip() for s in _SEGMENT_SPLIT.split(description) if s.strip())
    if not segments:
        return "out_of_scope", ()

    # 中文常无标点（「桌面放个2x2天气卡片怎么添加到桌面」）——标点切不开时，
    # 用生成/教程正则的**真实匹配边界**双向切分（教程可在前也可在后）。
    if len(segments) == 1:
        whole = segments[0]
        tutorial_match = _TUTORIAL_SEMANTIC.search(whole)
        if tutorial_match is not None:
            # 切掉教程片段后的「剩余部分」若仍有生成语义，才是真混排。
            # 教程尾部常与生成字面重叠（「添加到桌面桌面放个」），故用剩余
            # 子串判定而非原始 finditer 边界，避免纯教程「加到桌面」被误判。
            before = whole[:tutorial_match.start()]
            after = whole[tutorial_match.end():]
            gen_in_before = _GENERATION_SEMANTIC.search(before)
            gen_in_after = _GENERATION_SEMANTIC.search(after)
            if gen_in_before:
                return "clarification", (before.strip(), whole[tutorial_match.start():].strip())
            if gen_in_after:
                return "clarification", (whole[tutorial_match.end():].strip(), whole[:tutorial_match.end()].strip())
            return "out_of_scope", (whole,)

    # 一个片段若自身含教程语义（「怎么添加」），就不再算生成片段——
    # 「卡片怎么添加到桌面」整段是教程，不能因「添加到桌面」被误判成生成。
    tutorial_parts = tuple(s for s in segments if _TUTORIAL_SEMANTIC.search(s))
    has_generation = any(
        _GENERATION_SEMANTIC.search(s) for s in segments if s not in tutorial_parts
    )
    if tutorial_parts:
        if has_generation:
            # 带出全部片段，不丢第三段（S2）
            return "clarification", segments
        return "out_of_scope", tutorial_parts
    return "generation", segments


def check_input(
    description: str,
    extracted_texts: list[str],
    intent: str,
    budget: BudgetView,
    sensitive_detector: SensitiveWordDetector | None = None,
) -> GuardResult:
    """对描述与附件提取文本执行五类 L1 判定。

    Args:
        description: 用户描述。
        extracted_texts: 附件 / 检索片段的提取文本（按不可信数据扫描）。
        intent: 上游 L1 意图词表给出的意图分类。
        budget: M11 预算余量视图。
        sensitive_detector: 敏感词检测器；None 用进程级默认实现（内置词库 + 外置合规词库）。

    Returns:
        GuardResult；任一命中即 passed=False 且 short_circuit=True。
    """
    # 扫描对象 = 描述 + 附件提取文本（§6.5 只扫 extracted，不碰附件原文）
    joined = "\n".join([description, *extracted_texts])
    rule_hits = scan(joined, default_registry())
    # ADR-020 D1：混排反问优先于 INTENT 规则短路——无 grid 字面的混排
    # （「桌面放个天气卡片，卡片怎么添加到桌面」）否则会被 N5 抢先 REJECT，
    # 反问逻辑不可达（Round3 B1）。前提：命中全为 INTENT 且来自用户描述，
    # 注入/PII 命中（非全 INTENT）仍按安全红线硬拒。
    intent_only = all(h.hit_type is GuardHitType.INTENT for h in rule_hits)
    from_user = all(h.excerpt and h.excerpt in description for h in rule_hits)
    mix_kind, mix_segments = classify_intent_mix(description)

    # ── 安全红线（先于混排反问，防 AWAIT 绕过）─────────────────────────
    # 注入/PII 已在 rule_hits（非全 INTENT 时下面短路）；敏感词/危险调用在此。
    detector = sensitive_detector or default_sensitive_detector()
    sensitive = detector.scan(joined)
    if sensitive is not None:
        return GuardResult(
            passed=False,
            hits=(detector.to_guard_hit(sensitive),),
            short_circuit=True,
        )

    # ADR-010 §3：危险接口 / 高危设备 API / 危险动作（词表 + 形状层，零模型）。
    # 命中按 INJECTION 类型留证（未授权调用指令语义），不新增 ReasonCode。
    danger = detect_dangerous_calls(description, extracted_texts=tuple(extracted_texts))
    if danger.hit:
        hit = InputGuardHit(
            hit_type=GuardHitType.INJECTION,
            rule_id="DANGEROUS_CALL",
            judgement="危险接口/高危设备 API/危险动作：" + ";".join(danger.violations),
            excerpt=description[:80],
            decision=HitDecision.REJECT,
        )
        return GuardResult(passed=False, hits=(hit,), short_circuit=True)

    # 预算前置：余量 ≤ 0 确定性中断（不进模型、不自动重试）
    if budget.left <= 0:
        hit = InputGuardHit(
            hit_type=GuardHitType.BUDGET,
            rule_id=_BUDGET_RULE_ID,
            judgement="预算余量触顶，提交时确定性中断",
            excerpt="",
            decision=HitDecision.REJECT,
        )
        return GuardResult(passed=False, hits=(hit,), short_circuit=True)

    # ── 混排反问（安全红线全过后）─────────────────────────────────────
    # ADR-020 D1：混排优先于 INTENT 规则短路——无 grid 字面的混排否则
    # 会被 N5 抢先 REJECT，反问不可达（Round3 B1）。
    if rule_hits and intent_only and from_user and mix_kind == "clarification":
        return GuardResult(
            passed=False,
            hits=(
                InputGuardHit(
                    hit_type=GuardHitType.INTENT,
                    rule_id="INTENT_MIXED",
                    judgement="检测到生成与教程多个意图：" + " | ".join(mix_segments),
                    excerpt=" | ".join(mix_segments),
                    decision=HitDecision.AWAIT,
                ),
            ),
            short_circuit=True,
        )

    # ADR-020 D2：意图类规则的正向优先豁免——ADR-004 硬样本
    # 「桌面放个2x2天气卡片，显示今天天气怎么样」含宿主+生成动词+grid，
    # 但 N5「卡片…怎么」会误拒。此处只豁免 INTENT 类命中，且仅当用户
    # 描述本身（不是附件提取文本）满足显式生成请求三要素。
    if rule_hits and not (
        intent_only and from_user and _has_explicit_generation(description)
    ):
        return GuardResult(passed=False, hits=rule_hits, short_circuit=True)

    # 注入/PII/敏感词/危险调用/预算未命中，判意图白名单（只放行控件生成）
    if intent not in INTENT_ALLOWLIST:
        hit = InputGuardHit(
            hit_type=GuardHitType.INTENT,
            rule_id=_OUT_OF_SCOPE_RULE_ID,
            judgement=f"意图 {intent} 不在白名单，仅放行控件生成",
            excerpt=description[:80],
            decision=HitDecision.REJECT,
        )
        return GuardResult(passed=False, hits=(hit,), short_circuit=True)

    # ADR-020 D1：无 rule_hits 的混排（教程词未触发 N5）也要反问——
    # 复用前面算出的 mix_kind/mix_segments（安全红线已全部在此之前短路）。
    if mix_kind == "clarification":
        return GuardResult(
            passed=False,
            hits=(
                InputGuardHit(
                    hit_type=GuardHitType.INTENT,
                    rule_id="INTENT_MIXED",
                    judgement="检测到生成与教程多个意图：" + " | ".join(mix_segments),
                    excerpt=" | ".join(mix_segments),
                    decision=HitDecision.AWAIT,
                ),
            ),
            short_circuit=True,
        )
    if mix_kind == "out_of_scope":
        # 纯教程咨询（含无标点/「怎么生成」变体）：必须硬拒，
        # 不能因 N5 正则的正向豁免或 step0 的正向优先而静默通过（Round2 B1）。
        return GuardResult(
            passed=False,
            hits=(
                InputGuardHit(
                    hit_type=GuardHitType.INTENT,
                    rule_id=_OUT_OF_SCOPE_RULE_ID,
                    judgement="教程/操作咨询不属于控件生成：" + mix_segments[0][:80],
                    excerpt=mix_segments[0][:80],
                    decision=HitDecision.REJECT,
                ),
            ),
            short_circuit=True,
        )

    return GuardResult(passed=True)
