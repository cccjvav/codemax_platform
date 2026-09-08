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
import os
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.config import settings
from app.models import Base
from app.tools.sql_ddl import parse_ddl
from app.tools.word import build_data_dictionary
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
async def test_support_endpoint_survives_concurrency(perf_client):
    """20 路并发打客服接口，**全部 200 且能同时完成**（不死锁、不串行化排队失败）。

    ROADMAP S5-02-1 的「< 80 ms」指标由
    `test_faq_search_meets_80ms_budget_single_threaded` 承载（纯函数，实测均值
    0.049 ms，三个数量级余量，跨机器稳定）。

    **这里不断言延迟。** 本机实测（2 核，全量套件内，每次 20 路）：

    | 场景 | 客服 p95 | `/health` 基线 p95 | 比值 |
    | --- | --- | --- | --- |
    | 单跑本文件 | 19.5 ms | 8.1 ms | 2.4 |
    | 全量套件 run1 | 20.3 ms | **91.3 ms** | 0.22 |
    | 全量套件 run2 | **88.2 ms** | 9.3 ms | 9.5 |
    | 全量套件 run3 | **94.8 ms** | 9.7 ms | 9.8 |

    噪声落在哪个 20 路突发上、哪个就超线（run1 反而是**基线**超了），
    所以绝对阈值和「客服/基线」比值阈值**都守不住**：比值在 0.22~9.8 之间摆。
    根因是沙箱只有 2 个核，其余 380 个测试的余温会随机撞上某一次突发。
    延迟数字只作为观测记录，真正的回归防线是下面三条事件循环停顿测试
    （确定性判据，见 TD-186）。
    """
    lat = await _fan_out(
        perf_client, [("/support/ask", {"text": FAQ_QS[i % 3]}) for i in range(20)]
    )
    assert len(lat) == 20, "20 路请求应全部完成"


# ---------- 重活跑在哪儿：确定性判据 ----------
#
# 为什么不再断言延迟：见下面两条用例的 docstring，实测证据是 CI run 33792680165。
#
# 这两个 spy 必须是**模块顶层函数**：`build_data_dictionary` 走进程池，
# 函数要能被 pickle（按「模块 + 限定名」引用），嵌套函数/lambda 都不行。
_SEEN_THREADS: list[int] = []
_PID_FILE = Path(tempfile.gettempdir()) / "codemax_test_word_export_pid.txt"
_REAL_PARSE_DDL = parse_ddl
_REAL_BUILD = build_data_dictionary


def _spy_parse_ddl(ddl: str) -> dict:
    """记下自己被哪个线程执行，然后照常干活（不改变被测行为）。"""
    _SEEN_THREADS.append(threading.get_ident())
    return _REAL_PARSE_DDL(ddl)


def _spy_build_data_dictionary(graph) -> bytes:
    """记下自己被哪个**进程**执行。

    进程之间不共享内存，列表传不回来 —— 所以写文件。路径用模块常量而不是环境变量：
    `cpu_pool._executor` 是模块级单例，worker 可能在 setenv 之前就 fork 出来了，
    那样它读不到后设的变量；模块常量在 fork 与 spawn 两种启动方式下都一致。
    """
    with _PID_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{os.getpid()}\n")
    return _REAL_BUILD(graph)


@pytest.mark.asyncio
async def test_parse_ddl_runs_off_the_event_loop_thread(perf_client, monkeypatch):
    """**S5-02-2 的核心回归**：`parse_ddl` 必须跑在事件循环**之外**的线程里。

    这里刻意**不断言延迟**。原先断言「事件循环最大停顿 < 20 ms」，在 GitHub Actions
    上非确定性失败过（run `33792680165`，job `100772798400`，step `pytest`）：
    同一份代码的 push run 全绿、pull_request run 红，实测停顿 **74.7 ms**。

    同一份日志排除了「CI 机器慢」这个解释：CI 的 warmup 请求 **39.8 ms**，
    本机实测 **35.7~45.3 ms** —— **CPU 速度基本一样**。所以那 74.7 ms 是共享
    runner 的调度抖动，不是代码变慢。绝对阈值救不了；「停顿 / 同步耗时」的比值
    也救不了，因为分子是纯调度噪声（那次的比值高达 2.1，本机只有 0.30）。

    所以改成直接查代码**跑在哪个线程**：不在事件循环线程 = 通过。
    与机器快慢无关，不会抖。
    """
    loop_thread = threading.get_ident()
    _SEEN_THREADS.clear()
    monkeypatch.setattr("app.routers.tools.parse_ddl", _spy_parse_ddl)

    r = await perf_client.post("/tools/er-diagram", json={"ddl": BIG_DDL})
    assert r.status_code == 200, r.text[:200]

    assert _SEEN_THREADS, "spy 没被调用到 —— 打补丁的位置不对，这条用例会变成空测试"
    assert loop_thread not in _SEEN_THREADS, (
        f"parse_ddl 在事件循环线程（tid={loop_thread}）里同步执行了，"
        f"实测线程 {sorted(set(_SEEN_THREADS))}。满额 DDL 会独占循环约 35 ms。"
    )


@pytest.mark.asyncio
async def test_build_data_dictionary_runs_in_a_separate_process(perf_client, monkeypatch):
    """Word 导出的重活必须跑在**独立进程**里 —— 线程池不够（TD-160）。

    同样不断言延迟（理由见上一条）。这里查的是 PID，正好把 `cpu_pool` 的
    两种退化路径都盖住：

    | 实现 | 实测 PID | 本用例 |
    | --- | --- | --- |
    | 进程池（现状） | 子进程 PID | 通过 |
    | 线程池（`run_cpu_bound` 的兜底路径） | 与主进程同 PID | 失败 |
    | 直接在事件循环里同步跑 | 与主进程同 PID | 失败 |

    `build_data_dictionary` 本机实测约 470~500 ms，比 `parse_ddl` 贵一个数量级：
    线程池让得出事件循环却让不出 GIL。
    """
    _PID_FILE.unlink(missing_ok=True)
    monkeypatch.setattr("app.routers.tools.build_data_dictionary", _spy_build_data_dictionary)

    r = await perf_client.post("/tools/word-export", json={"ddl": BIG_DDL})
    assert r.status_code == 200, r.text[:200]

    assert _PID_FILE.exists(), "spy 没被调用到 —— 打补丁的位置不对，这条用例会变成空测试"
    pids = {int(x) for x in _PID_FILE.read_text(encoding="utf-8").split()}
    assert pids, "PID 文件是空的"
    assert os.getpid() not in pids, (
        f"build_data_dictionary 跑在本进程（pid={os.getpid()}）里，实测 {sorted(pids)}。"
        f"线程池让得出事件循环却让不出 GIL，必须用进程池（TD-160）。"
    )


@pytest.mark.asyncio
async def test_support_and_heaviest_endpoint_coexist(perf_client):
    """冒烟检查：最贵的端点在跑时，客服接口仍然能正常返回。

    **这里刻意不再断言 80 ms 的 SLA**，原因实测得很清楚（详见 TD-183）：

    1. 本机 2 核，正确实现下这条 p95 实测 **60~77 ms**，只有 0~25% 余量 —— 稍有
       额外负载就越线，于是随机变红。
    2. 更要紧的是它**测不到自己声称要守的退化**。`word_export` 先用线程池跑
       `parse_ddl`（33 ms）让出了事件循环，20 个客服请求在那道窗口里就跑完了，
       之后才发生 `build_data_dictionary` 的 ~460 ms 阻塞；而 `[1:]` 又把最慢的
       那条丢掉了。实测把 `build_data_dictionary` 改回同步（修复前的真 bug），
       这条 p95 是 **31~88 ms，3 次里 2 次照样通过**。
    3. 换成比值也不行：线程池 58~106 ms、进程池 60~77 ms，两者分布几乎完全重叠。

    「重活必须移出事件循环」由上面的 stall 测试守（两种退化都 3/3 抓住）。
    这条只保留"两个端点能同时正常返回"这个集成事实。
    """
    jobs = [("/tools/word-export", {"ddl": BIG_DDL})] + [
        ("/support/ask", {"text": FAQ_QS[i % 3]}) for i in range(20)
    ]
    lat = (await _fan_out(perf_client, jobs))[1:]
    p95 = sorted(lat)[int(len(lat) * 0.95) - 1]
    # 只防数量级退化（修复前客服 p50 是 526 ms），不是 SLA 断言
    assert p95 < 250, f"客服接口 p95={p95:.1f} ms，与最贵端点并发时出现数量级退化"


# ============================================================ S5-02-2 大文件渲染


def test_parse_ddl_scales_linearly_not_quadratically():
    """规模 ×4，耗时应约 ×4（O(n)）。变成 ×16 就是退化成 O(n²) 了。

    本机 2 核实测比值（同一份代码连续跑多轮，每轮取 rounds 次的最小值）：
    20→80 表稳定在 **×4.0~4.1**，干净的线性。

    绝对毫秒**不写进注释**：同一台机器不同负载下 20 表能在 12~16 ms 之间漂，
    记下来的数字下一轮就对不上，反而误导人。断言也因此只看比值。

    注：本 docstring 原先写的是「规模翻倍…实测环比 2.01x/2.00x/1.98x」，
    与下面的 `best_of(20), best_of(80)`（4 倍规模）不符 —— 那串数字是早期
    2 倍版本留下的（本机复测 20→40 确实是 ×2.09）。断言一直是对的，
    但记录的证据过期了，现已按实测更正。
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
