"""M05 S3 兼容对象存储（生产实现，§6.5 key 规范 / M01 S3_ENDPOINT）。

用 httpx + AWS SigV4 签名直连 MinIO（无 boto3 依赖，减少运行期重量）；
写原文/洞察/派生件到 att/{user_id}/{session_id}/{attachment_id}/* 前缀，
读必校验 sha256。与 InMemoryObjectStore 同方法集，可插拔（编码规则 8/9）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

import httpx

from services.control_plane.attachments.stores import ObjectRef

_ENV_ENDPOINT = "S3_ENDPOINT"
_ENV_KEY = "S3_ACCESS_KEY"
_ENV_SECRET = "S3_SECRET_KEY"
_ENV_BUCKET = "S3_BUCKET"
_DEFAULT_ENDPOINT = "http://127.0.0.1:9000"
_DEFAULT_BUCKET = "wp-dev"
_REGION = "us-east-1"
_SERVICE = "s3"
_XML_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


@dataclass(slots=True, frozen=True)
class ObjectStoreConfig:
    """S3 连接配置（12-factor：环境变量注入，禁止硬编码密钥）。"""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str


class S3ObjectStore:
    """S3 兼容对象存储：httpx + SigV4 签名 + sha256 读校验。"""

    def __init__(self, config: ObjectStoreConfig | None = None) -> None:
        """构造 HTTP 客户端；None 时从环境变量读取（生产入口）。"""
        cfg = config or self._from_env()
        self._endpoint = cfg.endpoint.rstrip("/")
        self._bucket = cfg.bucket
        self._access = cfg.access_key
        self._secret = cfg.secret_key
        self._client = httpx.Client(timeout=30.0)

    @staticmethod
    def _from_env() -> ObjectStoreConfig:
        """从环境变量读 S3 配置（本地 MinIO 有默认值）。"""
        return ObjectStoreConfig(
            endpoint=os.getenv(_ENV_ENDPOINT, _DEFAULT_ENDPOINT),
            access_key=os.getenv(_ENV_KEY, "wpdev"),
            secret_key=os.getenv(_ENV_SECRET, "wpdev-secret"),
            bucket=os.getenv(_ENV_BUCKET, _DEFAULT_BUCKET),
        )

    def put_original(
        self, user_id: str, session_id: str, attachment_id: str, data: bytes
    ) -> ObjectRef:
        """写原文到 att/*/original，返回带 sha256 的引用。"""
        return self._put(f"att/{user_id}/{session_id}/{attachment_id}/original", data)

    def put_insight(
        self, user_id: str, session_id: str, attachment_id: str, data: bytes
    ) -> ObjectRef:
        """写洞察到 att/*/insight.json，返回引用。"""
        return self._put(f"att/{user_id}/{session_id}/{attachment_id}/insight.json", data)

    def get_with_hash_verify(self, key: str, expected_hash: str) -> bytes:
        """按 key 读取并校验 sha256，篡改即抛（§6.4 读时校验）。"""
        data = self._get(key)
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise ValueError("CONTRACT_MISMATCH: object hash mismatch")
        return data

    def get_raw(self, key: str) -> bytes | None:
        """按裸 key 读对象（洞察解析用；404 返回 None）。"""
        try:
            return self._get(key)
        except KeyError:
            return None

    def bump_version(self, attachment_id: str, prefix_hint: str = "") -> int:
        """洞察版本号 +1：列出该附件前缀下 insight.v*.json 取最大 +1。"""
        keys = self._list(prefix_hint or "att/")
        versions: list[int] = []
        for key in keys:
            name = key.rsplit("/", 1)[-1]
            if name.startswith("insight.v") and name.endswith(".json"):
                try:
                    versions.append(int(name[len("insight.v") : -len(".json")]))
                except ValueError:
                    continue
        return max(versions, default=0) + 1

    def archive_insight(self, insight_ref: str, version: int) -> None:
        """把旧洞察复制为 insight.v{n}.json（GET + PUT，只追加不物理删）。"""
        old_key = insight_ref.partition("#sha256:")[0]
        data = self._get(old_key)
        self._put(old_key.replace("insight.json", f"insight.v{version}.json"), data)

    def versions(self, attachment_id: str) -> list[str]:
        """列出该附件历史洞察版本 key（验收断言用）。"""
        return [k for k in self._list("att/") if "insight.v" in k]

    def _put(self, key: str, data: bytes) -> ObjectRef:
        """PUT 对象 + 返回 hash 引用（SigV4 签名的真实 HTTP 写）。"""
        digest = hashlib.sha256(data).hexdigest()
        resp = self._signed("PUT", key, data)
        if resp.status_code // 100 != 2:
            raise OSError(f"S3 put failed: {resp.status_code} {resp.text[:200]}")
        return ObjectRef(key=key, hash=digest, size=len(data))

    def _get(self, key: str) -> bytes:
        """GET 对象；404 抛 KeyError（由 get_raw 折叠为 None）。"""
        resp = self._signed("GET", key)
        if resp.status_code == 404:
            raise KeyError(key)
        if resp.status_code // 100 != 2:
            raise OSError(f"S3 get failed: {resp.status_code} {resp.text[:200]}")
        return resp.content

    def _list(self, prefix: str) -> list[str]:
        """ListObjectsV2：返回匹配前缀的 key 列表（SigV4 签名）。"""
        query = urlencode({"list-type": "2", "prefix": prefix})
        resp = self._signed("GET", "", query=query)
        if resp.status_code // 100 != 2:
            return []
        root = ET.fromstring(resp.content)
        return [e.text or "" for e in root.iter(f"{_XML_NS}Key")]

    def _signed(
        self, method: str, key: str, body: bytes = b"", query: str = ""
    ) -> httpx.Response:
        """AWS SigV4 签名请求（MinIO/S3 通用，无第三方 SDK）。

        签名：method + canonical_uri + query + 规范化 headers + payload hash；
        密钥派生链 secret → date → region → service → aws4_request。
        """
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        # path-style S3：URI 必须以 /{bucket} 开头（MinIO 本地端点必需）
        canonical_uri = "/" + self._bucket + ("/" + quote(key, safe="/-_.~") if key else "")
        host = self._endpoint.removeprefix("http://").removeprefix("https://")
        headers = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        # SigV4 规范化请求：六段以换行分隔（协议格式，保持 join 以便逐段核对）
        canonical_request = (
            f"{method}\n{canonical_uri}\n{query}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
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
        signature = hmac.new(
            self._signing_key(date_stamp), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        auth = (
            f"AWS4-HMAC-SHA256 Credential={self._access}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = f"{self._endpoint}{canonical_uri}" + (f"?{query}" if query else "")
        return self._client.request(
            method, url, content=body, headers={**headers, "Authorization": auth}
        )

    def _signing_key(self, date_stamp: str) -> bytes:
        """派生 SigV4 签名密钥（HMAC 链，避免依赖 botocore）。"""
        key = ("AWS4" + self._secret).encode()
        for part in (date_stamp, _REGION, _SERVICE, "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        return key
