"""M08 真 Docker + 真 MinIO 集成测试（不冒充通过：不可用即 skip 并登记原因）。

前置：Docker daemon 可用（docker info）；MinIO 由 docker compose dev profile 提供
（S3_* 环境变量见 .env）。沙箱镜像缺失时尝试真实构建，构建被网络阻断则 skip。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from dotenv import load_dotenv

from services.sandbox_runner.entry import run_sandbox
from services.sandbox_runner.ports import DockerCliRunner, S3EvidenceStore
from tests.support.m08 import FakeSourceStore

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROOT = Path(__file__).resolve().parents[2]
_IMAGE = "wp-sandbox:dev"
_DOCKERFILE = _ROOT / "infra/sandbox/Dockerfile"
_SRC = "export default function Widget() { return <div/>; }"
_REF = "code/sess/it/widget.tsx#sha256:" + hashlib.sha256(_SRC.encode()).hexdigest()


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="Docker daemon 不可用（docker info 失败）"
)


@pytest.fixture(scope="module")
def sandbox_image() -> Iterator[str]:
    """镜像缺失时真构建；网络阻断则 skip，不伪造 passed。"""
    if subprocess.run(
        ["docker", "image", "inspect", _IMAGE], capture_output=True, timeout=10, check=False
    ).returncode == 0:
        yield _IMAGE
        return
    build = subprocess.run(
        ["docker", "build", "-t", _IMAGE, "-f", str(_DOCKERFILE), str(_DOCKERFILE.parent)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if build.returncode != 0:
        pytest.skip(f"沙箱镜像构建被阻断：{build.stderr[-400:]}")
    yield _IMAGE


def test_container_network_none_blocks_outbound(sandbox_image: str) -> None:
    """真容器验证 --network none：无法访问宿主 MinIO。"""
    js = (
        "fetch('http://127.0.0.1:9000/minio/health/live')"
        ".then(()=>process.exit(0)).catch(()=>process.exit(7))"
    )
    cmd = [
        "docker", "run", "--rm", "--network", "none",
        "--entrypoint", "node", sandbox_image, "-e", js,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 7, proc.stderr[-300:]


def test_timeout_kills_container_without_residue(sandbox_image: str) -> None:
    """真起死循环容器，超时强杀后容器列表为空。"""
    name = "wp-m08-it-" + uuid.uuid4().hex[:10]
    cmd = [
        "docker", "run", "--rm", "--name", name, "--network", "none",
        "--entrypoint", "node", sandbox_image, "-e", "setInterval(()=>{},1000)",
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=4, check=False)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, timeout=20, check=False)
    listing = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    ).stdout.strip()
    assert listing == "", f"残留容器：{listing}"


def test_runner_emits_staged_report_inside_sandbox(sandbox_image: str) -> None:
    """真容器内 esbuild 编译 + jsdom 渲染 + 断言，产出结构化 JSON。"""
    payload = json.dumps(
        {
            "source": _SRC,
            "manifest": {"grid": "2x2", "capabilities": ["date"]},
            "limits": {"render_timeout_ms": 5000},
        }
    ).encode()
    cmd = [
        "docker", "run", "--rm", "-i", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "10001:10001",
        "--memory", "512m", "--cpus", "1", "--pids-limit", "64", "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", sandbox_image,
    ]
    proc = subprocess.run(cmd, input=payload, capture_output=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr[-500:]
    report = json.loads(proc.stdout)
    stages = report["stages"]
    assert stages["compile"]["compiled"]
    assert stages["render"]["width_px"] == 160
    assert len(report["interactions"]) == 4


def test_real_minio_evidence_hash_roundtrip() -> None:
    """真 MinIO：写证据、按 hash 读回、篡改拒绝。"""
    load_dotenv(_ROOT / ".env")
    store = S3EvidenceStore()
    key = f"evidence/m08-it/{uuid.uuid4().hex[:8]}/check_report.json"
    stored = store.put(key, b'{"passed":true}')
    ref = f"{stored.key}#sha256:{stored.hash}"
    assert store.get(ref) == b'{"passed":true}'
    store.put(key, b'{"passed":false}')
    with pytest.raises(ValueError):
        store.get(ref)


def test_end_to_end_sandbox_with_real_docker_and_minio(sandbox_image: str) -> None:
    """真 Docker + 真 MinIO 跑完整 run_sandbox。"""
    load_dotenv(_ROOT / ".env")
    run_id = "it-" + uuid.uuid4().hex[:8]
    result = run_sandbox(
        run_id=run_id,
        source_ref=_REF,
        manifest={"grid": "2x2", "capabilities": ["date"]},
        limits={"sandbox_hard_timeout_ms": 15000, "render_timeout_ms": 5000},
        source_store=FakeSourceStore({_REF: _SRC.encode()}),
        evidence_store=S3EvidenceStore(),
        docker_runner=DockerCliRunner(),
        image=sandbox_image,
        prefix=f"evidence/sess/{run_id}",
    )
    assert result.passed is True, f"{result.reason_code}: {result.evidence_ref}"
    assert len(result.cases) >= 9
