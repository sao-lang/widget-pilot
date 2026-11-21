"""注入模式库（§3.4 B）：每条规则带 rule_id + 正则 + 判据。"""

from __future__ import annotations

from services.guardrails.types import GuardHitType, PatternRule

RULES: tuple[PatternRule, ...] = (
    PatternRule(
        rule_id="INJ_IGNORE_RULES",
        hit_type=GuardHitType.INJECTION,
        regex=r"忽略.{0,8}(之前|上面|以上|所有).{0,6}(规则|指令|提示)|ignore\s+(all\s+)?previous\s+instructions",
        judgement="疑似提示注入：试图让模型忽略既有规则",
    ),
    PatternRule(
        rule_id="INJ_SYSTEM_PROMPT",
        hit_type=GuardHitType.INJECTION,
        regex=r"(输出|显示|打印|reveal|print).{0,10}(系统提示|system\s+prompt)",
        judgement="疑似提示泄露：试图套取系统提示",
    ),
    PatternRule(
        rule_id="INJ_EXTERNAL_CALL",
        hit_type=GuardHitType.INJECTION,
        regex=r"fetch\s*\(|XMLHttpRequest|外联|外部请求",
        judgement="疑似外联注入：试图生成对外网络请求代码",
    ),
    PatternRule(
        rule_id="INJ_JAILBREAK_ROLE",
        hit_type=GuardHitType.INJECTION,
        regex=r"(?:你现在是|扮演|假装你是)\s*(?:DAN|无限制|不受约束|developer\s+mode)|不要遵守(?:任何|以上|所有)限制|jailbreak",
        judgement="疑似越狱注入：试图让模型扮演无限制角色",
    ),
    PatternRule(
        rule_id="INJ_ENCODED_EXFIL",
        hit_type=GuardHitType.INJECTION,
        regex=r"(?:把|将).{0,12}(?:系统提示|指令|规则).{0,12}(?:base64|编码|hex|加密).{0,8}(?:发|输出|给我)|encode.{0,12}(?:system\s+prompt|instructions)",
        judgement="疑似编码外泄：试图以编码方式带出系统提示",
    ),
    PatternRule(
        rule_id="INJ_DELIMITER_INJECT",
        hit_type=GuardHitType.INJECTION,
        regex=r"(?:-{3,}|={3,}|\[/?system\]|<\|).{0,8}(?:system|assistant|用户|指令)|(?:system|assistant).{0,8}(?:-{3,}|={3,})",
        judgement="疑似分隔符注入：伪造系统/助手消息边界",
    ),
    PatternRule(
        rule_id="INJ_INDIRECT_MARKER",
        hit_type=GuardHitType.INJECTION,
        regex=r"<!--.{0,40}(?:ignore|忽略|system|指令)|\[INST\].{0,40}(?:ignore|忽略|system)",
        judgement="疑似间接注入：附件/检索文本内嵌指令标记",
    ),
    PatternRule(
        rule_id="INJ_SECRET_EXFIL",
        hit_type=GuardHitType.INJECTION,
        regex=r"(?:输出|打印|发给我|reveal).{0,12}(?:api\s*key|secret|token|私钥|密码|凭证)|(?:api\s*key|secret|token|私钥|密码|凭证).{0,12}(?:输出|发给我|打印|reveal)",
        judgement="疑似凭据外泄：试图让模型输出密钥或凭证",
    ),
)
