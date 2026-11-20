"""M05 附件处理域包（原 D2）。

传输层校验 → 对象存储 → 按用户描述判定 role → 分派多模态处理器 → 产出带引用的
`AttachmentInsight`。本模块是唯一允许接触附件原文的域，且原文永不进公共语料。
"""

from __future__ import annotations
