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


# TD-266：已完整哈希过且此后 inode 元数据未变的快照，不再对每次下载重新读整个文件。
# 键 = (key, digest)；值 = 上次全量校验通过时、哈希开始前取的 (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)。
# ctime 由内核在任何写入/元数据变更时更新，用户态改不回去（utime 只能改 mtime/atime），
# 所以字节被换过、或被换成另一个 inode，下次都会重新哈希。两条边界：
# ① 时间戳有粒度（本沙箱 ext4 实测 4 ms，某些文件系统 1–2 s）：同一刻度内的两次写入 ctime 相同，
#    所以 ctime 距现在不足 _VERIFIED_GRACE_NS 的校验结果**不缓存**，下次仍全量哈希；
# ② Windows 的 st_ctime 是创建时间，改写不更新，不能当变更凭据 → 非 POSIX 不启用缓存。
# 进程内状态，重启即空；只缓存"文件与记录一致"这一件事，不缓存任何权益/退款判断。
_VERIFIED: dict[tuple[str, str], tuple[int, int, int, int, int]] = {}
_VERIFIED_LOCK = threading.Lock()
_VERIFIED_LIMIT = 256  # 键数上限（一个商品一个键），满了清空重来而不是无界增长
_VERIFIED_GRACE_NS = 3_000_000_000
_VERIFY_CACHE_ENABLED = os.name == 'posix'


def _now_ns() -> int:
    return time.time_ns()


def _inode_state(path: Path) -> tuple[int, int, int, int, int]:
    st = path.stat()
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def verify_snapshot(storage: LocalStorage, key: str, digest: str, size: int) -> bool:
    """Check content identity and bytes; no current-product fallback.

    First check (and any check after the file's inode state changed) streams the whole file
    through sha256. Later checks of an unchanged, settled inode only stat() it. Revocation and
    order checks are the caller's job and run on every request regardless of this cache.
    """
    try:
        if not key.startswith(f'.snapshots/{digest}/'):
            return False
        path = storage.local_path(key)
        now = _now_ns()  # 取在 stat 之前：之后的任何改写都落在比记录值更晚的刻度上
        state = _inode_state(path)
        if _VERIFY_CACHE_ENABLED and state[2] == size:
            with _VERIFIED_LOCK:
                if _VERIFIED.get((key, digest)) == state:
                    return True
        if file_digest(path) != (digest, size):
            return False
        if _VERIFY_CACHE_ENABLED and now - state[4] >= _VERIFIED_GRACE_NS:
            with _VERIFIED_LOCK:
                if len(_VERIFIED) >= _VERIFIED_LIMIT:
                    _VERIFIED.clear()
                _VERIFIED[(key, digest)] = state
        return True
    except (OSError, ValueError, StorageError):
        return False
