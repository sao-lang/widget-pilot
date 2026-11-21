"""PII 真值校验（ADR-005）：规则表出形状，成熟库做有效性判定。

避免"形状像但不是真号"的误报：手机号走 phonenumbers，身份证走
id-validator 校验位/地区码，邮箱走 email-validator 语法校验（不查 DNS）。
"""

from __future__ import annotations

import email_validator
import phonenumbers
from email_validator import EmailNotValidError
from id_validator.validator import is_valid as is_valid_cn_id
from phonenumbers import NumberParseException

_VALIDATORS = frozenset({"PII_PHONE_CN", "PII_EMAIL", "PII_ID_CN"})


def has_truth_validator(rule_id: str) -> bool:
    """该规则是否有成熟库真值校验。"""
    return rule_id in _VALIDATORS


def is_valid_pii(rule_id: str, value: str) -> bool:
    """形状命中后再做真值校验；无对应校验器一律保守返回 True。"""
    if rule_id == "PII_PHONE_CN":
        try:
            return phonenumbers.is_valid_number(phonenumbers.parse(value, "CN"))
        except NumberParseException:
            return False
    if rule_id == "PII_EMAIL":
        try:
            email_validator.validate_email(value, check_deliverability=False)
            return True
        except EmailNotValidError:
            return False
    if rule_id == "PII_ID_CN":
        return is_valid_cn_id(value)
    return True
