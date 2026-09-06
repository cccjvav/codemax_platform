# tests/test_support_rag_perf.py
#
# RAG 检索不许堵住事件循环，也不许每次请求都重建索引（CODE_REVIEW_99662ca 的 P1-2）。
#
# 改动前 `_retrieve_articles()` 做了两件昂贵的事，且都在事件循环里同步做：
#   ① `select(Article)` 全表捞出**所有**文章（含完整正文）
#   ② 对每篇跑 jieba 分词 + 现建一个 BM25/余弦索引
#
# 实测：200 篇文章时检索墙钟 **242.6 ms**，同期 `asyncio.sleep(10ms)` 的
# 最大漂移 **232.3 ms** —— 那 0.23 秒内**全站所有请求**都排不上队。
# 而 `/support/ask` 是**不鉴权**的（只有 RATE_LIMIT_LLM 限流），
# 匿名用户就能反复触发。
#
# 这与 TD-159/183/186「重 CPU 不留在事件循环」是同一条原则，
# 也是 A-1（bcrypt 堵事件循环）的同类问题。
"""P1-2：RAG 检索的 CPU 开销不许留在事件循环，且索引要能复用。"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from app.models import Article
from app.tools import support

pytestmark = pytest.mark.asyncio(loop_scope="session")

# 事件循环漂移的阈值。实测改前是 232 ms、改后应在个位数 ms。
# 取 60 ms：单次 jieba 分词 200 篇就要 200 ms 量级，只要还留在事件循环里
# 就必然远超这个数；留余量是为了不在慢 CI 上抖。
_MAX_LOOP_DRIFT_MS = 60.0

_ARTICLES = 200


async def _seed(db) -> int:
    """造一批真实规模的文章，返回条数。"""
    for i in range(_ARTICLES):
        db.add(
            Article(
                url=f"https://example.com/a{i}",
                title=f"第{i}篇 部署与运维指南",
                content=(
                    "本文介绍如何在生产环境部署与排障，涵盖反向代理、"
                    "数据库连接池、日志采集与告警阈值配置等实践要点。" * 12
                ),
            )
        )
    await db.commit()
    return len((await db.execute(select(Article))).scalars().all())


async def test_rag_retrieval_does_not_block_the_event_loop(db):
    """检索期间事件循环必须还能按时醒来。

    这是本组最要紧的一条：`/support/ask` 不鉴权，匿名用户反复提问就能让
    **整站**（包括不需要鉴权的 ER 图、Mermaid 这些引流页）周期性卡顿。
    改前实测漂移 232 ms。
    """
    support.reset_article_index()
    n = await _seed(db)
    assert n == _ARTICLES, "前置条件：语料规模要够大才测得出差别"

    drift: list[float] = []
    stop = False

    async def probe():
        while not stop:
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            drift.append((time.perf_counter() - t0 - 0.01) * 1000)

    probe_task = asyncio.create_task(probe())
    await asyncio.sleep(0.05)  # 先让探针跑起来，否则测不到东西
    baseline = len(drift)
    assert baseline > 0, "探针没跑起来，这条用例会假绿"

    result = await support._retrieve_articles(db, "怎么配置反向代理")

    stop = True
    await asyncio.sleep(0.02)
    probe_task.cancel()  # 显式收尾，别留悬挂 task 污染后面的用例

    assert result is not None, "知识库应当可用"
    during = drift[baseline:]
    assert during, "检索期间探针一次都没计时 —— 说明事件循环被整段占住了"
    worst = max(during)
    assert worst < _MAX_LOOP_DRIFT_MS, (
        f"检索期间事件循环最大漂移 {worst:.1f} ms（阈值 {_MAX_LOOP_DRIFT_MS} ms）—— "
        "jieba 分词与建索引仍在事件循环里同步跑。"
    )


async def test_repeated_queries_reuse_the_index(db):
    """第二次查询必须明显快于第一次 —— 索引不该每次重建。

    改前每个请求都要「全表捞 → 逐篇分词 → 现建索引」，语料一大就是
    纯浪费：语料没变，算出来的索引也一模一样。
    """
    support.reset_article_index()
    await _seed(db)

    t0 = time.perf_counter()
    await support._retrieve_articles(db, "怎么配置反向代理")
    first = time.perf_counter() - t0

    t0 = time.perf_counter()
    await support._retrieve_articles(db, "怎么配置反向代理")
    second = time.perf_counter() - t0

    assert second < first / 3, (
        f"第二次 {second * 1000:.1f} ms 相比第一次 {first * 1000:.1f} ms 没有明显变快 —— "
        "索引在每次请求都重建。"
    )


async def test_index_is_invalidated_when_the_corpus_changes(db):
    """语料变了必须重建索引 —— 缓存不能变成「查不到新文章」。

    只测「第二次更快」是单侧断言：万一改成了永不过期的缓存，
    上面那条照样绿，而管理员新入库的文章永远搜不到。
    """
    support.reset_article_index()
    await _seed(db)

    before = await support._retrieve_articles(db, "灰度发布怎么做")

    db.add(
        Article(
            url="https://example.com/canary",
            title="灰度发布与回滚完整手册",
            content="灰度发布 金丝雀 回滚 流量切分 蓝绿部署 灰度发布 灰度发布 灰度发布",
        )
    )
    await db.commit()

    after = await support._retrieve_articles(db, "灰度发布怎么做")

    assert after is not None and before is not None
    titles_after = {t for t, _ in after}
    assert "灰度发布与回滚完整手册" in titles_after, (
        f"新入库的文章没被检索到，命中是 {sorted(titles_after)} —— 索引缓存没有失效机制。"
    )


async def test_retrieval_still_returns_relevant_results(db):
    """对照组：性能优化不能把检索质量弄坏。

    万一为了快改成「只取前 N 篇」或「不分词直接比字符串」，
    上面三条照样绿，而客服答非所问。
    """
    support.reset_article_index()
    # ⚠️ 这里刻意**不用** _seed()：那批种子文章的正文里也含「反向代理」，
    #    200 篇会一起参与竞争，第一名是谁就成了语料分布问题而不是检索质量问题。
    #    要让这条断言有意义，对照组语料必须与被测词无关。
    for i in range(50):
        db.add(
            Article(
                url=f"https://example.com/other{i}",
                title=f"第{i}篇 数据库索引与查询优化",
                content="本文讲 B+ 树索引、执行计划与慢查询排查，以及分页与连接池调优。" * 8,
            )
        )
    await db.commit()
    db.add(
        Article(
            url="https://example.com/nginx",
            title="Nginx 反向代理配置详解",
            content="反向代理 upstream proxy_pass 负载均衡 反向代理 反向代理 反向代理",
        )
    )
    await db.commit()

    hits = await support._retrieve_articles(db, "反向代理怎么配")
    assert hits, "应当检索到相关内容"
    assert hits[0][0] == "Nginx 反向代理配置详解", (
        f"最相关的应当是 Nginx 那篇，实际第一位是 {hits[0][0]!r}"
    )
