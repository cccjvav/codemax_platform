"""重 CPU 任务的执行池（S5-02-2 / S5-03）。

## 为什么需要进程池，线程池不够

线程池能让出**事件循环**，但让不出 **GIL**：Python 字节码在任一时刻只有一个
线程在跑。满额 20000 字符 DDL 导 Word（`build_data_dictionary` 单次约 340 ms）是几百毫秒的
纯 Python 计算，放在线程里仍与事件循环线程争同一把 GIL；放进子进程才真正不占本进程的解释器
（TD-160 的解法）。对 33 ms 的 `parse_ddl`，线程池已经够。

⚠️ 这里原先列了一组「并发轻量请求 p95：线程池 76.59 ms / 进程池 35.75 ms」作为依据（TD-327 删除）。
TD-186 查明并发延迟 p95 在单线程事件循环下**结构性失效**（重活阻塞循环时别的请求在排队，量到的
只是排队之后的一小段），后来本机复测两者分布完全重叠。选进程池的依据是确定性判据：
`tests/test_perf.py::test_build_data_dictionary_runs_in_a_separate_process` 直接查任务跑在哪个
**进程**里（TD-193），不受机器快慢影响。

## 为什么保留线程池兜底

`ProcessPoolExecutor` 在 Windows 上用 spawn 启动子进程，子进程要重新导入模块；
容器里可能受进程数/内存限制；某些环境 fork 不安全。这些都会让进程池**创建或
提交任务时抛异常**。真出问题时退化成线程池（慢但不坏）远好过让导出功能直接 500。
兜底路径由 `test_process_pool_failure_falls_back_to_threadpool` 覆盖。
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("codemax.cpu")

_executor: ProcessPoolExecutor | None = None
_broken = False  # 进程池坏过一次就别再试了，避免每个请求都付一次失败开销


def _get_executor() -> ProcessPoolExecutor | None:
    global _executor, _broken
    if _broken:
        return None
    if _executor is None:
        try:
            # max_workers=1：导出是低频操作且已限流，一个 worker 足够；
            # 多开只是多占内存，并不会更快（GIL 换成多进程后瓶颈变成 CPU 核数）。
            _executor = ProcessPoolExecutor(max_workers=1)
        except Exception as e:  # 兜底路径必须吞掉所有创建期异常
            logger.warning("进程池不可用，退化到线程池：%s", e)
            _broken = True
            return None
    return _executor


async def _execute_cpu(fn, *args):
    """在进程池里跑重 CPU 任务；进程池不可用时退化到线程池。

    注意 `fn` 与其参数、返回值都必须能被 pickle —— 也就是函数要定义在模块顶层，
    参数/返回值是普通数据结构。`build_data_dictionary(graph) -> bytes` 满足。
    """
    ex = _get_executor()
    if ex is None:
        return await run_in_threadpool(fn, *args)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(ex, fn, *args)
    except (BrokenProcessPool, OSError) as e:  # infrastructure failure only; task errors propagate
        global _broken
        logger.warning("进程池任务失败，退化到线程池：%s", e)
        _broken = True
        return await run_in_threadpool(fn, *args)


def shutdown() -> None:
    """应用退出时回收子进程，否则会留下孤儿进程。"""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False)
        _executor = None


class CPUQueueFull(RuntimeError):
    """No admission slot available, or admitted work exceeded its response deadline."""


_inflight = 0


async def run_cpu_bound(fn, *args):
    global _inflight
    if _inflight >= 2:
        raise CPUQueueFull("导出任务繁忙，请稍后重试")
    _inflight += 1
    task = asyncio.create_task(_execute_cpu(fn, *args))

    def finished(done):
        global _inflight
        _inflight -= 1
        if not done.cancelled():
            done.exception()  # retrieve a late error after the client has gone away

    task.add_done_callback(finished)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=30)
    except TimeoutError as e:
        raise CPUQueueFull("导出超时，请稍后重试") from e
