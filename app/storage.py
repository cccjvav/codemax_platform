"""云存储策略（S3-02-2）+ 预签名下载 URL（S3-02-3）。

策略接口只暴露两件事：**对象在不在**、**给一个带签名和过期时间的下载 URL**。
换云厂商就是换一个实现，业务代码（`app/routers/shop.py` 的下载接口）一行不用改。

目前只有 `LocalStorage` 能真正跑通。阿里云 OSS / 腾讯云 COS 需要密钥，而沙箱里没有
（S3-02-1 未完成），写出来的云适配器**无法验证签名是否正确**，所以刻意不写（TD-128）。
本地后端用的是与云厂商同构的机制 —— HMAC 签名 + 过期时间戳 —— 所以预签名、过期、
篡改、越权换 key 这几件事都是真实可测的；将来换成云后端，业务逻辑与安全性质不变。
"""
from __future__ import annotations

import hashlib
import hmac
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

    def presigned_url(self, key: str, *, expires_in: int) -> str: ...


def sign_download(secret: str, key: str, expires: int) -> str:
    """下载 URL 的签名：`HMAC-SHA256(secret, "<key>\\n<expires>")`。

    把 key 也签进去，否则拿到一个合法链接就能改 key 去下别的文件。
    """
    return hmac.new(secret.encode(), f"{key}\n{expires}".encode(), hashlib.sha256).hexdigest()


def verify_download(secret: str, key: str, expires: int, signature: str) -> bool:
    """校验签名与过期时间。`compare_digest` 防时序侧信道。"""
    if expires < int(time.time()):
        return False
    return signature.isascii() and hmac.compare_digest(sign_download(secret, key, expires), signature)


class LocalStorage:
    """本地目录后端（开发与答辩演示用）。

    预签名 URL 指向本站 `/shop/dl`，由该端点校验签名后再吐文件；
    云后端则是客户端直连对象存储，不经过本站。
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

    def presigned_url(self, key: str, *, expires_in: int) -> str:
        expires = int(time.time()) + expires_in
        signature = sign_download(self.secret, key, expires)
        return (
            f"{self.base_url}/shop/dl?key={quote(key)}&expires={expires}&signature={signature}"
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
