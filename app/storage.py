"""本地对象存储与HMAC短时下载链接。

仅LocalStorage已实现；delivery模块依赖本地分块复制、内容寻址与硬链接发布。
未来云适配器必须另行实现版本固定和完整性/签名验证，不能只换凭据或宣称路由无需改动。
源文件可由可信运营写入，快照路径（包括规范化别名）不允许put覆盖；
主机文件管理员仍可信，本地目录不是WORM。短链接在有效期内仍可转交。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from .config import settings


class StorageError(Exception):
    """存储后端未配置或不可用。"""


class Storage(Protocol):
    """存储后端策略。"""

    backend: str

    def exists(self, key: str) -> bool: ...

    def presigned_url(self, key: str, *, expires_in: int, order_no: str | None = None) -> str: ...


def sign_download(secret: str, key: str, expires: int, *, order_no: str | None = None) -> str:
    """v2以JSON数组签用途域、订单号、key、期限，杜绝跨订单/字段分隔歧义。

    不传order_no仅保留旧工具签名兼容；下载路由明确拒绝所有旧格式链接。
    """
    payload = f"{key}\n{expires}" if order_no is None else json.dumps(
        ['codemax-order-download-v2', order_no, key, expires], ensure_ascii=False, separators=(',', ':'))
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_download(secret: str, key: str, expires: int, signature: str, *, order_no: str | None = None) -> bool:
    """校验签名与过期时间。`compare_digest` 防时序侧信道。"""
    if expires < int(time.time()):
        return False
    return signature.isascii() and hmac.compare_digest(sign_download(secret, key, expires, order_no=order_no), signature)


class LocalStorage:
    """本地目录后端（开发与答辩演示用）。

    预签名 URL 指向本站 `/shop/dl`，端点验证订单签名与实时退款权益。
    不传order_no的旧工具链接不可下载；未来云适配也必须有等价撤权能力。
    """

    backend = "local"

    def __init__(self, root: str, base_url: str, secret: str):
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.secret = secret

    def _path(self, key: str) -> Path:
        """解析对象 key，并挡住目录穿越（`../../etc/passwd` 这种）。"""
        root = self.root.resolve()
        p = (self.root / key).resolve()
        if p != root and root not in p.parents:
            raise ValueError(f"非法的对象 key：{key!r}")
        return p

    def put(self, key: str, data: bytes) -> None:
        """写入对象。生产由运营上传，测试用来造商品文件。"""
        p = self._path(key)
        snapshots = self._path('.snapshots')
        if p == snapshots or snapshots in p.parents:
            raise StorageError('不能覆盖交付快照')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def read(self, key: str) -> bytes:
        """仅本地后端有：云端是客户端直连下载，不经过应用。"""
        return self._path(key).read_bytes()

    def local_path(self, key: str) -> Path:
        """仅本地后端有：返回**已通过目录穿越校验**的磁盘路径。

        单独开这个方法、而不是让路由自己拼 `root / key`，是为了保证任何下载出口
        都**必须**走 `_path` 的穿越校验 —— 绕过它就能读服务器上任意文件。
        调用方拿到路径后交给 `FileResponse` 分块流式发送，不必把整个文件读进内存。
        """
        return self._path(key)

    def presigned_url(self, key: str, *, expires_in: int, order_no: str | None = None) -> str:
        expires = int(time.time()) + expires_in
        signature = sign_download(self.secret, key, expires, order_no=order_no)
        return (
            f"{self.base_url}/shop/dl?key={quote(key)}&expires={expires}"
            + (f"&order_no={quote(order_no, safe='')}" if order_no is not None else "")
            + f"&signature={signature}"
        )


def build_storage(base_url: str) -> Storage:
    """按配置挑后端。每次都现读配置，方便测试改 settings 后立刻生效。

    未知后端**直接报错**而不是退回本地：静默降级等于把文件从对象存储挪到应用目录，
    是安全性的降级，宁可起不来。
    """
    backend = settings.STORAGE_BACKEND
    if backend == "local":
        return LocalStorage(settings.STORAGE_LOCAL_ROOT, base_url, settings.SECRET_KEY)
    raise StorageError(
        f"存储后端 {backend!r} 未实现：阿里云 OSS / 腾讯云 COS 需要先申请密钥"
        f"（S3-02-1 未完成，见 TECH_DECISIONS.md TD-128）"
    )
