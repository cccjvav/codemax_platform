"""跨后端的时间归一化（TD-146 / TD-155）。

只有两处逻辑，但两处都必须在**读**的时候做：
- PostgreSQL 的 `TIMESTAMPTZ` 读回来带时区；
- SQLite 不支持时区，SQLAlchemy 读回来是**裸值**。

写入侧一律是 UTC，所以裸值按 UTC 解读即可，两种后端的比较语义就一致了。
不做这层归一化，SQLite 上一比较就抛
`TypeError: can't compare offset-naive and offset-aware datetimes` ——
而这个错**只在 SQLite 上出现**，真库测试反而看不见（TD-146 就是这么漏的）。
"""
from __future__ import annotations

from datetime import datetime, timezone


def as_utc(dt: datetime) -> datetime:
    """把从数据库读回来的时间统一成带时区的 UTC。已带时区的原样返回。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
