"""Local content-addressed delivery snapshots. Only trusted operators supply source files.

Two concurrent copies, bounded bytes/time and chunked I/O; callers offload to a thread. No
checkout may mutate published snapshots. Filesystem administrators remain trusted: this is
not WORM storage, a backup service or an external object-store implementation.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .storage import LocalStorage, StorageError

MAX_PRODUCT_BYTES = 512 * 1024 * 1024
_COPY_SLOTS = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class Snapshot:
    key: str
    digest: str
    size: int


def file_digest(path: Path) -> tuple[str, int]:
    """Bounded streaming integrity check; never loads a full product into memory."""
    digest, size, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_PRODUCT_BYTES or time.monotonic() > deadline:
                raise StorageError('商品超过512MiB或本地读取超时')
            digest.update(chunk)
    return digest.hexdigest(), size


def snapshot_product(storage: LocalStorage, key: str) -> Snapshot:
    """Copy to a same-filesystem temp, hash, then publish without replacing an existing object.

    Failed DB checkout may leave a valid unreferenced snapshot; keep it, never delete possibly
    purchased objects during error recovery. Source changes during copy cause rejection.
    """
    if not _COPY_SLOTS.acquire(blocking=False):
        raise StorageError('商品准备繁忙，请稍后重试')
    temporary = None
    try:
        source = storage.local_path(key)
        if not source.is_file():
            raise StorageError('商品文件尚未准备，不能先收款')
        before = source.stat()
        if before.st_size > MAX_PRODUCT_BYTES:
            raise StorageError('商品文件超过512MiB')
        directory = storage.local_path('.snapshots')
        directory.mkdir(parents=True, exist_ok=True)
        digest, size, deadline = hashlib.sha256(), 0, time.monotonic() + 30
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as target:
            temporary = Path(target.name)
            with source.open('rb') as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_PRODUCT_BYTES or time.monotonic() > deadline:
                        raise StorageError('商品复制超限或超时')
                    digest.update(chunk)
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise StorageError('商品在复制时发生变更，请重新发布后重试')
        hexdigest = digest.hexdigest()
        result = Snapshot(f'.snapshots/{hexdigest}/{source.name}', hexdigest, size)
        if len(result.key) > 512:
            raise StorageError('商品对象名过长')
        destination = storage.local_path(result.key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(temporary, destination)  # atomic no-clobber publication on the same volume
        except FileExistsError:
            if file_digest(destination) != (hexdigest, size):
                raise StorageError('已有商品快照损坏，停止收款并从备份恢复') from None
        if os.name == 'posix':
            for parent in (destination.parent, directory, storage.root):
                descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        return result
    except (OSError, ValueError) as exc:
        raise StorageError('商品本地存储不可用') from exc
    finally:
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        finally:
            _COPY_SLOTS.release()


def verify_snapshot(storage: LocalStorage, key: str, digest: str, size: int) -> bool:
    """Check both content identity and bytes before issuing a link; no current-product fallback."""
    try:
        return (key.startswith(f'.snapshots/{digest}/')
                and file_digest(storage.local_path(key)) == (digest, size))
    except (OSError, ValueError, StorageError):
        return False
