"""复现指纹计算（M07 §3.4 G / §5 D4 验收）。

``repro_fingerprint = sha256(prompt_version + recipe_version + retrieval_snapshot
+ model + model_version + tier_rubric_version + temperature + seed + spec_version
+ preference_version)``。十个输入字段用具名 dataclass 收口（避免超 5 参数），
纯函数、同输入同输出；``preference_version`` 来自 ``UserPreference.version``
（I-M6），偏好变化属于可解释输入变化，不算复现失败。

各字段用固定分隔符拼接而非裸连接，避免 ``"a"+"bc"`` 与 ``"ab"+"c"`` 碰撞；
非字符串字段用 ``str`` 归一，保证跨调用确定性。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

# --- 具名常量（默认版本值，禁魔法字符串散落）--------------------------------

_DEFAULT_PROMPT_VERSION: str = "v1"
"""默认 prompt 版本。"""
_DEFAULT_RECIPE_VERSION: str = "v1"
"""默认生成配方版本。"""
_DEFAULT_RETRIEVAL_SNAPSHOT: str = "none"
"""默认检索快照标识（无检索时用 none 占位，非 magic 散落）。"""
_DEFAULT_MODEL: str = "standard"
"""默认模型标识（与 ModelTier cheap/standard/frontier 对齐）。"""
_DEFAULT_MODEL_VERSION: str = "v1"
"""默认模型版本。"""
_DEFAULT_TIER_RUBRIC_VERSION: str = "v1"
"""默认分档 rubric 版本。"""
_DEFAULT_TEMPERATURE: float = 0.0
"""默认采样温度（确定性生成取 0）。"""
_DEFAULT_SEED: int = 0
"""默认随机种子（确定性生成取 0）。"""
_DEFAULT_SPEC_VERSION: str = "v1"
"""默认规格 schema 版本。"""
_DEFAULT_PREFERENCE_VERSION: int = 1
"""默认偏好版本（与 ``PREFERENCE_VERSION_INITIAL`` 对齐）。"""
_FIELD_SEPARATOR: str = "|"
"""十字段拼接的固定分隔符（防相邻字段值串位碰撞）。"""


@dataclass(frozen=True, slots=True)
class ReproInputs:
    """复现指纹的十个具名字段（§3.4 G；参数对象化避免超 5 参数）。

    Attributes:
        prompt_version: prompt 模板版本。
        recipe_version: 生成规程版本。
        retrieval_snapshot: 检索索引快照标识（M10 提供）。
        model: 模型标识。
        model_version: 模型版本（配置中心的 model_config_version）。
        tier_rubric_version: 分档 rubric 版本。
        temperature: 采样温度（确定性生成取 0）。
        seed: 随机种子。
        spec_version: WidgetSpec schema 版本。
        preference_version: 用户偏好全局版本（来自 ``UserPreference.version``）。
    """

    prompt_version: str = _DEFAULT_PROMPT_VERSION
    recipe_version: str = _DEFAULT_RECIPE_VERSION
    retrieval_snapshot: str = _DEFAULT_RETRIEVAL_SNAPSHOT
    model: str = _DEFAULT_MODEL
    model_version: str = _DEFAULT_MODEL_VERSION
    tier_rubric_version: str = _DEFAULT_TIER_RUBRIC_VERSION
    temperature: float = _DEFAULT_TEMPERATURE
    seed: int = _DEFAULT_SEED
    spec_version: str = _DEFAULT_SPEC_VERSION
    preference_version: int = _DEFAULT_PREFERENCE_VERSION


def compute_repro_fingerprint(inputs: ReproInputs) -> str:
    """按 §3.4 G 的十字段拼接计算 sha256 复现指纹。

    Args:
        inputs: 十个具名字段组成的指纹输入；调用方可注入任意版本值。

    Returns:
        64 位十六进制 sha256 摘要；同输入恒同输出，任一字段变化即变化。
    """
    fields: tuple[str | int | float, ...] = (
        inputs.prompt_version,
        inputs.recipe_version,
        inputs.retrieval_snapshot,
        inputs.model,
        inputs.model_version,
        inputs.tier_rubric_version,
        inputs.temperature,
        inputs.seed,
        inputs.spec_version,
        inputs.preference_version,
    )
    raw = _FIELD_SEPARATOR.join(str(field) for field in fields)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
