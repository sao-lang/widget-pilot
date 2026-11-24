"""M09 产物与包管理域（§5 D6 / FR-12/13）：成包、hash 链、版本回滚、状态机、
WidgetRuntime 渲染协议、evidence 聚合与 artifact 事件。

纪律：本模块只收纳 M08 校验结论与 M07 源码引用成包，**不产代码 / 不编译 /
不重测 / 不改 M08 结论 / 不写 SessionState / 不调模型**。生产实现只有 PG +
S3 兼容对象存储；测试用替身统一放 tests/support/。设计权威见
``docs/modules/M09-产物与包管理域.md``。
"""
from __future__ import annotations
