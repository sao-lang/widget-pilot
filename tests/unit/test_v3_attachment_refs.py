"""P0 附件生成侧：attachment id → 富对象（工具链可执行）。"""
from __future__ import annotations

from services.control_plane.attachments.stores import AttachmentRow
from services.orchestrator.attachment_refs import resolve_attachments


class _Store:
    def __init__(self, rows: dict[str, AttachmentRow]) -> None:
        self._rows = rows

    def get(self, attachment_id: str) -> AttachmentRow | None:
        return self._rows.get(attachment_id)


def _row(aid: str, *, key: str = "att/u1/s/original.mp4", digest: str = "a" * 64) -> AttachmentRow:
    return AttachmentRow(
        id=aid, user_id="u1", session_id="s1", kind="video", mime="video/mp4",
        size=100, object_key=key, hash=digest,
    )


def test_resolve_attachments_builds_source_ref() -> None:
    """带 hash 的 source_ref 从 store 行拼出（工具链真可执行）。"""
    store = _Store({"a1": _row("a1")})
    resolved = resolve_attachments(["a1"], store)
    assert len(resolved) == 1
    assert resolved[0].id == "a1"
    assert resolved[0].source_ref == "att/u1/s/original.mp4#sha256:" + "a" * 64
    assert resolved[0].kind == "video"


def test_resolve_attachments_skips_missing() -> None:
    """查不到的 id 跳过（不造假绿），不阻断其他附件。"""
    store = _Store({"a1": _row("a1")})
    resolved = resolve_attachments(["missing", "a1"], store)
    assert [item.id for item in resolved] == ["a1"]


def test_embed_assets_injects_image_data_url() -> None:
    """derived_refs 的图片 ref → base64 data URL 写进 spec（沙箱无网络可渲染）。"""
    import base64

    from services.deterministic.compiler import compile_spec, embed_assets
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(
        intent_id="widget.generic", instance_id="h:0",
        slots={"grid": "4x2", "data": "待办", "host": "桌面",
               "derived_refs": {"frame_at": "derived/a/frame.webp#sha256:" + "b" * 64}},
    )
    spec = compile_spec(fill)

    class _Reader:
        def get(self, ref: str) -> bytes:
            assert ref.startswith("derived/a/frame.webp#sha256:")
            return b"\x89PNG\r\n\x1a\n" + b"x" * 20  # 小图

    embed_assets(spec, fill.slots["derived_refs"], _Reader())
    slots_list = spec["layout_slots"]
    assert isinstance(slots_list, list)
    slot = slots_list[0]
    assert isinstance(slot, dict)
    url = slot["image_data_url"]
    assert isinstance(url, str) and url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG")


def test_embed_assets_skips_oversize_and_non_image() -> None:
    """超大图与非图片派生（csv）不注入，保持纯文本不炸产物。"""
    from services.deterministic.compiler import compile_spec, embed_assets
    from services.guardrails.interpreter.types import SlotFill

    fill = SlotFill(intent_id="widget.generic", instance_id="h:0",
                    slots={"grid": "4x2", "data": "待办", "host": "桌面"})
    spec = compile_spec(fill)

    class _Reader:
        def get(self, ref: str) -> bytes:
            return b"x" * 200_000

    embed_assets(spec, {"frame_at": "derived/a/frame.webp#sha256:" + "b" * 64,
                        "rows": "derived/a/rows.json#sha256:" + "c" * 64}, _Reader())
    slots_list2 = spec["layout_slots"]
    assert isinstance(slots_list2, list)
    slot2 = slots_list2[0]
    assert isinstance(slot2, dict) and "image_data_url" not in slot2
