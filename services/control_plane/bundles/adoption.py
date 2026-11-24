"""M09 → M07 采纳信号的生产适配（D-10 第 4 类信号，零 LLM）。

用户安装产物时，把 WidgetSpec.palette / recipe_version / widget_type 作为结构化
信号写入 M07 偏好学习（``learn_from_adopted_artifact``），bundle_ref 写进情节证据供
删除/回滚时精确撤销（M09 §9.0 义务 1/2）。不读自由文本、不调模型。
"""
from __future__ import annotations

from services.control_plane.bundles.ports import ArtifactAdoptionPort
from services.orchestrator.memory.ports import PreferenceStorePort, ProcedureStorePort
from services.orchestrator.memory.preference_learning import learn_from_adopted_artifact


class M07ArtifactAdoption:
    """生产采纳信号实现（委托 M07 真实入口，零 LLM）。"""

    def __init__(
        self,
        preference_store: PreferenceStorePort,
        procedure_store: ProcedureStorePort | None,
    ) -> None:
        """注入 M07 真实存储端口（生产 PgPreferenceStore/PgProcedureStore）。"""
        self._prefs = preference_store
        self._procs = procedure_store

    def on_artifact_adopted(
        self,
        user_id: str,
        bundle_id: str,
        palette: str | None,
        recipe_id: str,
        widget_type: str,
        trace_id: str,
    ) -> None:
        """写 adopted_artifact 偏好 + 程序记忆采纳（bundle_ref 精确撤销锚点）。"""
        learn_from_adopted_artifact(
            self._prefs,
            self._procs,
            user_id,
            palette,
            recipe_id,
            widget_type,
            trace_id,
            bundle_ref=bundle_id,
        )


def make_artifact_adoption(
    preference_store: PreferenceStorePort,
    procedure_store: ProcedureStorePort | None,
) -> ArtifactAdoptionPort:
    """生产工厂：构造采纳信号适配（缺 preference_store 由调用方 fail-fast）。"""
    return M07ArtifactAdoption(preference_store, procedure_store)
