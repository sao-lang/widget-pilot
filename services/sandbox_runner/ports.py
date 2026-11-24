"""M08 沙箱运行端口与生产实现：证据对象存储（S3 兼容）+ Docker 一次性容器。

不 import control_plane（服务边界）：SigV4 以 httpx 直连 MinIO，缺配置 fail-fast；
Docker 宿主机直调，超时强杀并确认容器消失。测试替身只在 tests/support/m08.py。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from services.guardrails.output_ports import make_object_ref, parse_object_ref
from services.sandbox_runner import container_cli

__all__ = [
    "CommandResult",
    "DockerCliRunner",
    "DockerRunnerPort",
    "EvidenceStorePort",
    "ObjectRef",
    "S3EvidenceConfig",
    "S3EvidenceStore",
    "SandboxUnavailableError",
]

_REGION = "us-east-1"
_SERVICE = "s3"
_ENV_ENDPOINT = "S3_ENDPOINT"
_ENV_ACCESS = "S3_ACCESS_KEY"
_ENV_SECRET = "S3_SECRET_KEY"
_ENV_BUCKET = "S3_BUCKET"


class SandboxUnavailableError(RuntimeError):
    """沙箱基础设施不可用（Docker 缺失/镜像缺失/超时）；触发降级而非通过。"""


@dataclass(slots=True, frozen=True)
class ObjectRef:
    """写对象后的引用（key + sha256 + 字节数）。"""

    key: str
    hash: str
    size: int


@dataclass(slots=True, frozen=True)
class CommandResult:
    """Docker 命令结果。"""

    stdout: str
    stderr: str
    exit_code: int


@runtime_checkable
class EvidenceStorePort(Protocol):
    """证据存储：写对象返回带 hash 引用；读对象必须校验 hash。"""

    def put(self, key: str, data: bytes) -> ObjectRef:
        """写入并返回引用。"""
        ...

    def get(self, ref: str) -> bytes:
        """按引用读回并校验 hash；不符抛 ValueError。"""
        ...


@runtime_checkable
class DockerRunnerPort(Protocol):
    """一次性容器端口：执行 / 强杀 / 存在性确认。"""

    def run(self, cmd: Sequence[str], *, stdin: bytes, timeout_s: int) -> CommandResult:
        """前台运行容器；超时强杀后抛 SandboxUnavailableError。"""
        ...

    def kill(self, container_name: str) -> None:
        """强制销毁容器（成功/失败/超时/取消四条路径都调用）。"""
        ...

    def exists(self, container_name: str) -> bool:
        """容器是否仍存在（含已停止）；超时后必须为 False。"""
        ...


@dataclass(slots=True, frozen=True)
class S3EvidenceConfig:
    """12-factor 配置；生产缺任一变量即 fail-fast，不用开发默认值兜底。"""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str

    @classmethod
    def from_env(cls) -> S3EvidenceConfig:
        """从环境变量读取；缺配置抛 ValueError（不静默放行）。"""
        env = {
            "endpoint": os.getenv(_ENV_ENDPOINT, ""),
            "access_key": os.getenv(_ENV_ACCESS, ""),
            "secret_key": os.getenv(_ENV_SECRET, ""),
            "bucket": os.getenv(_ENV_BUCKET, ""),
        }
        missing = [name for name, value in env.items() if not value]
        if missing:
            raise ValueError(f"S3 evidence config missing: {', '.join(missing)}")
        return cls(**env)


class S3EvidenceStore:
    """httpx + AWS SigV4 直连 S3/MinIO；所有引用带 sha256，读时校验。"""

    def __init__(self, config: S3EvidenceConfig | None = None) -> None:
        self._config = config or S3EvidenceConfig.from_env()
        self._endpoint = self._config.endpoint.rstrip("/")
        self._client = httpx.Client(timeout=30.0)

    def put(self, key: str, data: bytes) -> ObjectRef:
        """PUT 对象并返回带 hash 的引用。"""
        payload = bytes(data)
        response = self._signed("PUT", key, payload)
        if response.status_code // 100 != 2:
            raise OSError(f"S3 evidence put failed: {response.status_code} {response.text[:200]}")
        return ObjectRef(key=key, hash=hashlib.sha256(payload).hexdigest(), size=len(payload))

    def get(self, ref: str) -> bytes:
        """GET 对象；hash 与引用不符即抛（A17 证据篡改）。"""
        key, expected = parse_object_ref(ref)
        response = self._signed("GET", key)
        if response.status_code // 100 != 2:
            raise OSError(f"S3 evidence get failed: {response.status_code} {response.text[:200]}")
        payload = response.content
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise ValueError("CONTRACT_MISMATCH: evidence hash mismatch")
        return payload

    def _signed(self, method: str, key: str, body: bytes = b"") -> httpx.Response:
        """SigV4 签名（path-style，适配 MinIO 与 S3）。"""
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        canonical_uri = "/" + self._config.bucket + ("/" + quote(key, safe="/-_.~") if key else "")
        host = self._endpoint.removeprefix("http://").removeprefix("https://")
        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
        canonical_request = (
            f"{method}\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )
        scope = f"{date_stamp}/{_REGION}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            ]
        )
        signing_key = hmac.new(
            ("AWS4" + self._config.secret_key).encode(), date_stamp.encode(), hashlib.sha256
        ).digest()
        for part in (_REGION, _SERVICE, "aws4_request"):
            signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self._config.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return self._client.request(
            method,
            f"{self._endpoint}{canonical_uri}",
            content=body,
            headers={**headers, "Authorization": authorization},
        )


class DockerCliRunner:
    """宿主机 Docker CLI 真实执行器；超时/缺失镜像都归基础设施不可用。"""

    def run(self, cmd: Sequence[str], *, stdin: bytes, timeout_s: int) -> CommandResult:
        """前台运行；15s 硬超时后强杀并确认容器消失。"""
        args = list(cmd)
        try:
            proc = subprocess.run(
                args,
                input=stdin,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self.kill(_container_name(args))
            raise SandboxUnavailableError("sandbox hard timeout") from exc
        except FileNotFoundError as exc:
            raise SandboxUnavailableError(f"{container_cli.cli()} CLI unavailable") from exc
        if proc.returncode != 0:
            raise SandboxUnavailableError(
                f"{container_cli.cli()} run failed: "
                f"{proc.returncode} {proc.stderr.decode(errors='replace')[:200]}"
            )
        return CommandResult(
            stdout=proc.stdout.decode("utf-8", errors="replace"),
            stderr=proc.stderr.decode("utf-8", errors="replace"),
            exit_code=proc.returncode,
        )

    def kill(self, container_name: str) -> None:
        """docker kill + rm -f 双保险；--rm 下 rm 为空操作。"""
        if not container_name:
            return
        for args in (
            container_cli.run_command(["kill", container_name]),
            container_cli.run_command(["rm", "-f", container_name]),
        ):
            try:
                subprocess.run(args, capture_output=True, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                continue

    def exists(self, container_name: str) -> bool:
        """容器是否仍存在（含停止态）。"""
        if not container_name:
            return False
        try:
            proc = subprocess.run(
                container_cli.run_command([
                    "ps", "-a", "--filter",
                    f"name=^{container_name}$", "--format", "{{.ID}}",
                ]),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True
        return bool(proc.stdout.strip())


def _container_name(cmd: Sequence[str]) -> str:
    """提取 --name 参数；无 --name 时返回空串（不误杀其他容器）。"""
    args = list(cmd)
    for index, arg in enumerate(args):
        if arg == "--name" and index + 1 < len(args):
            return args[index + 1]
    return ""


def build_object_ref(key: str, data: bytes) -> str:
    """兼容导出：引用格式与 output_ports 一致。"""
    return make_object_ref(key, data)
