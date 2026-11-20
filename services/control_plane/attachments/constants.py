"""M05 传输层与处理硬上限常量（§6.3 固化表，代码常量无在线关闭开关）。

所有值为【推导·需评审】建议初值，影子期校准；集中一处供断言与审计引用，
禁止散落裸数字（编码规则 common §2）。
"""

from __future__ import annotations

ALLOWED_KINDS: frozenset[str] = frozenset({"image", "video", "text"})
"""附件类别白名单（§5.1 AttachmentKind）。"""

MAX_ATTACHMENT_BYTES: int = 10 * 1024 * 1024
"""单附件大小上限：10 MiB。"""

MAX_ATTACHMENTS_PER_SESSION: int = 5
"""单会话附件数量上限。"""

ROLE_CLARIFY_THRESHOLD: float = 0.6
"""role 判定置信度阈值，低于此值产出澄清信号。"""

SNIFF_HEAD_BYTES: int = 16
"""magic number 嗅探读取的文件头字节数。"""

PROCESSOR_TIMEOUT_S: int = 20
"""单个处理器（ffmpeg / OCR）超时秒数。"""

UPLOAD_TIMEOUT_S: int = 30
"""单次上传接收超时秒数。"""

MAX_KEYFRAMES: int = 3
"""视频关键帧抽取上限。"""

MAX_TEXT_ROWS: int = 1000
"""`data_source` 解析行数上限。"""
