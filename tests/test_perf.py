"""S5-02 性能与压力测试。

## 这里测的是什么，不测什么

延迟数字**不适合当断言**：CI 机器快慢差好几倍，写死「必须 < 80ms」只会变成
随机红的噪音。所以本文件的断言全部是**相对量**或**增长阶**：

- `p50_有干扰 / p50_无干扰` 的比值 —— 机器慢则两边一起慢，比值稳定；
- 规模翻倍时耗时的增长倍数 —— 抓的是算法复杂度退化成 O(n²)，与机器无关。

绝对数字（本机实测）写在注释里供参考，不作为断言。

## 一个必须记住的测量陷阱

`time.perf_counter()` 要在**提交请求之前**取，不能在协程里取。
同步 CPU 代码独占事件循环时，后来的请求在排队；若从协程真正开始执行才起表，
量到的只是它自己的服务时间，排队等待被完全隐藏 —— 于是「事件循环被阻塞」
这个现象会测不出来（本项目实测踩过，第一版基准就是这么错的）。
"""
import asyncio
import time

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.config import settings
from app.models import Base
from app.tools.sql_ddl import parse_ddl
from main import app


def make_ddl(n_tables: int, cols: int = 12) -> str:
    out = []
    for t in range(n_tables):
        c = ["    id INT PRIMARY KEY AUTO_INCREMENT"]
        c += [f"    col_{i} VARCHAR({50 + i}) NOT NULL COMMENT '字段{i}'" for i in range(cols)]
        c += ["    prev_id INT,", f"    FOREIGN KEY (prev_id) REFERENCES t{t - 1}(id)"]
        out.append(f"CREATE TABLE t{t} (\n" + ",\n".join(c) + "\n) ENGINE=InnoDB;")
    return "\n\n".join(out)


def biggest_allowed_ddl(limit: int = 20000) -> str:
    """造一个刚好卡在 ErDiagramIn.max_length 之下的 DDL —— 端点能吃进去的最大负载。"""
    lo, hi = 1, 200
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(make_ddl(mid)) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return make_ddl(lo)


BIG_DDL = biggest_allowed_ddl()
FAQ_QS = ["毕业设计服务怎么收费", "可以开发票吗", "支持哪些数据库"]


def p50(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[len(xs) // 2]


@pytest.fixture
async def perf_client():
    """独立的内存库 + 关掉限流：限流会把并发请求挡成 429，测的就不是延迟了。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with session() as s:
            yield s

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    old_enabled, old_ttl = settings.RATE_LIMIT_ENABLED, settings.DOWNLOAD_URL_TTL
    settings.RATE_LIMIT_ENABLED = False
    app.dependency_overrides[database.get_db] = override
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://perf", timeout=120
    ) as c:
        yield c
    settings.RATE_LIMIT_ENABLED = old_enabled
    settings.DOWNLOAD_URL_TTL = old_ttl
    app.dependency_overrides.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _fan_out(client, jobs: list[tuple[str, dict]]) -> list[float]:
    """并发发出所有请求，返回**从提交时刻算起**的每个请求耗时（ms）。

    t0 必须在 gather 之前取 —— 见模块 docstring 里的测量陷阱。
    """
    latencies: list[float] = []

    async def one(path: str, payload: dict):
        r = await client.post(path, json=payload)
        assert r.status_code == 200, f"{path} 返回 {r.status_code}: {r.text[:200]}"
        latencies.append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*[one(p, pl) for p, pl in jobs])
    return latencies


# ============================================================ S5-02-1 客服并发


@pytest.mark.asyncio
async def test_faq_search_meets_80ms_budget_single_threaded():
    """FAQ 检索本身的 <80ms 指标（ROADMAP S4-02-1 / S5-02-1 明确要求）。

    实测均值 0.049 ms、p99 0.089 ms —— 余量约三个数量级。
    这里仍然写死 80ms，因为它测的是纯函数、不含网络与调度，跨机器足够稳定。
    """
    from app.tools.faq import search

    search("预热", k=3)
    samples = []
    for i in range(300):
        t = time.perf_counter()
        search(FAQ_QS[i % len(FAQ_QS)], k=3)
        samples.append((time.perf_counter() - t) * 1000)
    mean = sum(samples) / len(samples)
    assert mean < 80, f"FAQ 平均延迟 {mean:.3f} ms 超过 80ms 预算"
    assert max(samples) < 80, f"最慢一次 {max(samples):.3f} ms 也超了"


@pytest.mark.asyncio
async def test_support_endpoint_p95_under_concurrency(perf_client):
    """20 路并发打客服接口，p95 仍在 80ms 预算内（无任何干扰负载）。

    实测 p50 ≈ 14 ms、p95 ≈ 15 ms。检索本身只占 0.05 ms，
    剩下的全是 HTTP/鉴权/DB 会话开销 —— 这也是为什么瓶颈不在算法。
    """
    lat = await _fan_out(
        perf_client, [("/support/ask", {"text": FAQ_QS[i % 3]}) for i in range(20)]
    )
    p95 = sorted(lat)[int(len(lat) * 0.95) - 1]
    assert p95 < 80, f"客服接口 20 路并发 p95={p95:.2f} ms 超过 80ms 预算"


async def _max_event_loop_stall(client, path: str, payload: dict) -> float:
    """在重请求进行期间，事件循环最长多久没被调度到（秒）。

    这是**确定性**判据，不依赖机器快慢：
    - CPU 代码跑在事件循环里 → 后台任务全程饿死，间隔≈该次计算的总时长；
    - 挪到线程/进程里 → GIL 每 5 ms 切一次（`sys.getswitchinterval()`），
      后台任务能持续拿到时间片，间隔只有毫秒级。

    早先用「有干扰 p50 / 无干扰 p50」的比值断言，但 clean p50 只有 6~14 ms 时，
    任何几十毫秒的绝对开销都会显示成好几倍，比值抖得没法当阈值 —— 所以换成这个。
    """
    ticks: list[float] = []
    stop = False

    async def ticker():
        while not stop:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)  # 先让 ticker 稳定跳起来
    r = await client.post(path, json=payload)
    assert r.status_code == 200, r.text[:200]
    done = time.perf_counter()
    stop = True
    await task
    # **必须补记请求结束的时刻**。否则 ticker 恢复时先判 `while not stop` 就退出了，
    # 最长的那段停顿（正是我们要抓的）永远不会进入 ticks —— 第一版就是这么写空的：
    # 全同步变异下 word-export 实测 369 ms，测出来却是「最大间隔 5.2 ms」。
    ticks.append(done)

    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    return max(gaps) if gaps else float("inf")


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_big_ddl(perf_client):
    """**S5-02-2 的核心回归**：解析大 DDL 时事件循环不能被独占。

    `parse_ddl` 满额输入约 33 ms。若直接在 `async def` 里同步调用，
    这 33 ms 内所有协程都拿不到时间片 —— 后台 ticker 的间隔就会≈33 ms。
    挪进线程池后，GIL 每 5 ms 让一次，间隔应在毫秒级。
    """
    stall = await _max_event_loop_stall(perf_client, "/tools/er-diagram", {"ddl": BIG_DDL})
    assert stall < 0.020, (
        f"解析大 DDL 期间事件循环卡了 {stall * 1000:.0f} ms。"
        f"parse_ddl 是否又回到事件循环里同步执行了？"
    )


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_word_export(perf_client):
    """Word 导出是最贵的端点（`build_data_dictionary` 约 340~450 ms），单独守一条。

    这个量级下**线程池不够**：它让得出事件循环却让不出 GIL，实测并发轻量请求
    p95 线程池 76.6 ms vs 进程池 35.8 ms。所以走的是进程池（TD-160）。
    """
    stall = await _max_event_loop_stall(perf_client, "/tools/word-export", {"ddl": BIG_DDL})
    assert stall < 0.020, (
        f"导出 Word 期间事件循环卡了 {stall * 1000:.0f} ms。"
        f"build_data_dictionary 必须跑在进程池里，线程池都不够。"
    )


@pytest.mark.asyncio
async def test_faq_p95_survives_concurrent_heaviest_endpoint(perf_client):
    """真正的 SLA：最贵的端点在跑时，客服 p95 仍须在 80 ms 预算内。

    这条用绝对值，因为 80 ms 是 ROADMAP 写死的**需求**而不是本机测量值。
    实测：修复前客服 p50 526 ms（完全打穿），进程池后 p95 约 36 ms。
    """
    jobs = [("/tools/word-export", {"ddl": BIG_DDL})] + [
        ("/support/ask", {"text": FAQ_QS[i % 3]}) for i in range(20)
    ]
    lat = (await _fan_out(perf_client, jobs))[1:]
    p95 = sorted(lat)[int(len(lat) * 0.95) - 1]
    assert p95 < 80, f"最贵端点并发时客服 p95={p95:.1f} ms，超出 80 ms 预算"


# ============================================================ S5-02-2 大文件渲染


def test_parse_ddl_scales_linearly_not_quadratically():
    """规模翻倍，耗时应约翻倍（O(n)）。变成 4 倍就是退化成 O(n²) 了。

    实测环比：2.01x / 2.01x / 2.00x / 2.05x / 1.98x —— 干净的线性。
    用比值而非绝对耗时断言，跨机器稳定。
    """
    def best_of(n: int, rounds: int = 3) -> float:
        ddl = make_ddl(n)
        parse_ddl(ddl)  # 预热
        ts = []
        for _ in range(rounds):
            t = time.perf_counter()
            parse_ddl(ddl)
            ts.append((time.perf_counter() - t) * 1000)
        return min(ts)

    small, big = best_of(20), best_of(80)  # 4 倍规模
    growth = big / small
    assert growth < 8.0, (
        f"规模 ×4 而耗时 ×{growth:.2f}：线性应≈4，平方是 16。"
        f"解析器里可能混进了 O(n²) 的扫描（20 表 {small:.1f} ms → 80 表 {big:.1f} ms）"
    )


def test_parse_ddl_handles_max_size_input():
    """接口允许的最大输入必须能正确解析完，不能截断或漏表。"""
    graph = parse_ddl(BIG_DDL)
    n = BIG_DDL.count("CREATE TABLE")
    assert len(graph["tables"]) == n, f"应解析出 {n} 张表，实际 {len(graph['tables'])}"
    assert len(graph["edges"]) == n - 1, "每张表一条外键，边数应为表数减一"


@pytest.mark.asyncio
async def test_concurrent_big_ddl_requests_all_succeed(perf_client):
    """5 个满额 DDL 并发，全部 200 且结果一致 —— 压力下的正确性，不只是快慢。

    实测总耗时约 170 ms（≈5×33 ms）：线程池让出了事件循环，但 GIL 仍使
    CPU 代码串行，所以总时间不会因为并发而变长，也不会变短（TD-160）。
    """
    results = await asyncio.gather(
        *[perf_client.post("/tools/er-diagram", json={"ddl": BIG_DDL}) for _ in range(5)]
    )
    assert [r.status_code for r in results] == [200] * 5
    first = results[0].json()
    for r in results[1:]:
        assert r.json() == first, "同样输入必须给出同样结果"
    assert len(first["tables"]) == BIG_DDL.count("CREATE TABLE")


@pytest.mark.asyncio
async def test_rejects_oversized_ddl_before_parsing(perf_client):
    """超上限的输入应在**解析之前**就被挡掉（pydantic 422），不消耗 CPU。

    这条同时解释了为什么事件循环阻塞的上限是可控的：接口把输入限制在
    20000 字符，单次 parse 最多约 33 ms，不会有人拿 100 万字符的 DDL 打进来。
    """
    r = await perf_client.post("/tools/er-diagram", json={"ddl": make_ddl(160)})
    assert r.status_code == 422
    assert len(make_ddl(160)) > 20000
