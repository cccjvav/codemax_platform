"""TD-266 / ROADMAP O-01：下载出口的快照完整性校验按 inode 状态跳过重复全量哈希。

测量（2026-09-22，沙箱 2 核）：sha256 吞吐约 900 MiB/s，512 MiB 快照每次下载 0.53 s CPU；
合法链接持有者一分钟内允许的 30 次 Range 请求对 256 MiB 商品要烧 8 s CPU（线程池里），
改为「首次全量哈希，之后同一 inode 状态只 stat」后为 0.27 s。这里钉住的不是数字，而是语义：

- 首次与 inode 状态变化后必须全量哈希；未变时不读文件内容；
- 字节被改（哪怕大小不变、mtime 被回拨）必须重新哈希并判失败——ctime 用户态改不回去；
- 换成另一个同大小文件（inode 变）同样重新哈希；
- 缓存只覆盖"文件与记录一致"这一件事，退款/订单/签名判断仍每次执行（出口用例另证）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app import delivery
from app.delivery import _inode_state, file_digest, snapshot_product, verify_snapshot
from app.storage import LocalStorage


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    """每条用例一份空缓存、启用缓存（Windows 上默认关闭，这里显式打开以验证逻辑）、
    时钟拨到"文件写入 3 秒之后"——否则刚建的快照都在宽限期内，什么都不会被缓存。"""
    delivery._VERIFIED.clear()
    monkeypatch.setattr(delivery, "_VERIFY_CACHE_ENABLED", True)
    monkeypatch.setattr(delivery, "_now_ns", lambda: time.time_ns() + delivery._VERIFIED_GRACE_NS + 1_000_000_000)
    yield
    delivery._VERIFIED.clear()


@pytest.fixture
def snapshot(tmp_path):
    storage = LocalStorage(str(tmp_path), "http://test", "secret")
    (tmp_path / "product.zip").write_bytes(os.urandom(64 * 1024) + b"TAIL")
    snap = snapshot_product(storage, "product.zip")
    return storage, snap, storage.local_path(snap.key)


def tamper(path: Path, data: bytes) -> None:
    """改写文件并确保 ctime 落在**新的时间戳刻度**上。

    生产里能进缓存的状态至少 3 秒旧（宽限期），之后的任何写入必然在更晚的刻度；测试里快照刚建好，
    用假时钟绕过了宽限期，所以要显式等到刻度翻过去，否则是在测"同一刻度内改写"这个被宽限期排除的情形。
    """
    before = path.stat().st_ctime_ns
    deadline = time.monotonic() + 5
    while True:
        path.write_bytes(data)
        if path.stat().st_ctime_ns != before:
            return
        assert time.monotonic() < deadline, "文件系统时间戳 5 秒内没有推进"
        time.sleep(0.005)


def hashing_calls(monkeypatch) -> list[Path]:
    calls: list[Path] = []
    real = delivery.file_digest

    def spy(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(delivery, "file_digest", spy)
    return calls


def test_first_check_hashes_then_unchanged_inode_only_stats(snapshot, monkeypatch):
    storage, snap, path = snapshot
    calls = hashing_calls(monkeypatch)
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert calls == [path], "同一 inode 状态只在第一次读文件内容"
    assert delivery._VERIFIED[(snap.key, snap.digest)] == _inode_state(path)


def test_wrong_digest_or_size_or_key_never_passes_and_is_not_cached(snapshot):
    storage, snap, _ = snapshot
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size + 1) is False
    assert verify_snapshot(storage, snap.key, "0" * 64, snap.size) is False, "key 前缀与摘要不符"
    assert verify_snapshot(storage, "product.zip", snap.digest, snap.size) is False, "非快照 key"
    assert delivery._VERIFIED == {}
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size + 1) is False, "缓存命中也必须先核对 size"


def test_overwritten_bytes_are_rehashed_and_rejected(snapshot, monkeypatch):
    storage, snap, path = snapshot
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    calls = hashing_calls(monkeypatch)
    tamper(path, b"x" * snap.size)  # 同样大小，内容不同
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is False
    assert calls == [path], "内容变化后必须重新哈希"
    assert (snap.key, snap.digest) not in delivery._VERIFIED or delivery._VERIFIED[(snap.key, snap.digest)] != _inode_state(path)


def test_mtime_rollback_cannot_hide_tampering(snapshot, monkeypatch):
    """攻击者/误操作改了字节又把 mtime 改回去：ctime 仍然变化，必须重新哈希。"""
    storage, snap, path = snapshot
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    before = path.stat()
    tamper(path, b"y" * snap.size)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_mtime_ns == before.st_mtime_ns, "前提：mtime 确实被回拨"
    calls = hashing_calls(monkeypatch)
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is False
    assert calls == [path]


def test_replaced_file_with_same_bytes_is_rehashed_once_then_cached(snapshot, monkeypatch):
    """备份恢复：字节相同但 inode 不同 → 重新哈希一次，通过后继续按新 inode 缓存。"""
    storage, snap, path = snapshot
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    content = path.read_bytes()
    before = path.stat().st_ctime_ns
    deadline = time.monotonic() + 5
    while True:  # 与 tamper 同理：新文件要落在新的时间戳刻度上（内核可能复用刚释放的 inode 号）
        path.unlink()
        path.write_bytes(content)
        if path.stat().st_ctime_ns != before:
            break
        assert time.monotonic() < deadline
        time.sleep(0.005)
    calls = hashing_calls(monkeypatch)
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert calls == [path]


def test_cache_is_bounded(snapshot, monkeypatch):
    storage, snap, path = snapshot
    monkeypatch.setattr(delivery, "_VERIFIED_LIMIT", 2)
    delivery._VERIFIED[("a", "1")] = (0, 0, 0, 0, 0)
    delivery._VERIFIED[("b", "2")] = (0, 0, 0, 0, 0)
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert set(delivery._VERIFIED) == {(snap.key, snap.digest)}, "满了整体清空后只剩本次"


def test_missing_file_is_false_and_never_cached(snapshot):
    storage, snap, path = snapshot
    path.unlink()
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is False
    assert delivery._VERIFIED == {}


def test_file_digest_itself_is_unchanged(snapshot):
    """缓存只包在 verify_snapshot 外面；file_digest 仍是无状态的流式哈希。"""
    storage, snap, path = snapshot
    assert file_digest(path) == (snap.digest, snap.size)
    assert file_digest(path) == (snap.digest, snap.size)


def test_recent_writes_are_not_cached_until_the_timestamp_grace_passes(snapshot, monkeypatch):
    """同一时间戳刻度内的两次写入 ctime 相同（本机 ext4 实测 4 ms 粒度，有的文件系统 1–2 s），
    所以 ctime 距现在不足 3 秒的通过结果不缓存：下一次仍然全量哈希。"""
    storage, snap, path = snapshot
    monkeypatch.setattr(delivery, "_now_ns", time.time_ns)  # 真实时钟：快照刚写完，处于宽限期
    calls = hashing_calls(monkeypatch)
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert calls == [path, path], "宽限期内每次都要读文件"
    assert delivery._VERIFIED == {}
    monkeypatch.setattr(delivery, "_now_ns", lambda: time.time_ns() + delivery._VERIFIED_GRACE_NS)
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert len(calls) == 3, "宽限期过后第一次哈希，之后命中缓存"


def test_cache_is_disabled_where_ctime_is_not_a_change_signal(snapshot, monkeypatch):
    """非 POSIX（Windows 的 st_ctime 是创建时间）不启用缓存：每次都全量哈希，结果仍正确。"""
    storage, snap, path = snapshot
    monkeypatch.setattr(delivery, "_VERIFY_CACHE_ENABLED", False)
    calls = hashing_calls(monkeypatch)
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert verify_snapshot(storage, snap.key, snap.digest, snap.size) is True
    assert calls == [path, path] and delivery._VERIFIED == {}
    assert delivery._VERIFY_CACHE_ENABLED is False or os.name != "posix"


def test_default_switch_follows_the_platform():
    assert delivery._VERIFIED_GRACE_NS == 3_000_000_000
    assert delivery._VERIFIED_LIMIT == 256
