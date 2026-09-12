"""TD-138：管理员端点 —— 抓取 + 解析 + 入库（S4-01-4 的 HTTP 入口）。

**为什么必须挂在管理员鉴权后面**：这个端点会让服务器去访问调用方给的任意 URL，
并调用一次 LLM。公开出去就是两件事的组合：一个 SSRF 面（已由
`crawler.assert_public_url` 挡内网/环回/元数据地址）+ 一个**烧钱面**
（每次调用都花 LLM token，且攻击者可以无限刷）。所以除了鉴权还挂 LLM 档限流。

抓取与解析本身不在这个文件里 —— 复用 `app/tools/crawler.py` + `extract.py`
的服务层函数，它们各自有独立测试（SSRF、robots、骨架、选择器）。本端点只负责
「鉴权 + 错误映射 + 入库」，测试也按这个边界写（见 tests/test_admin_ingest.py）。
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import require_admin
from ..models import User
from ..ratelimit import rate_limit
from ..schemas import ArticleIngestIn
from ..tools.browser import BrowserUnavailable, render
from ..tools.crawler import CrawlError
from ..tools.extract import ExtractError, parse_article, parse_page, save_article
from ..tools.llm import LLMError, get_llm
from ..tools.politeness import RobotsDisallowed

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/articles/ingest",
    dependencies=[Depends(rate_limit("ingest", "RATE_LIMIT_LLM"))],
)
async def ingest_article(
    data: ArticleIngestIn,
    db: AsyncSession = Depends(get_db),
    llm=Depends(get_llm),
    admin: User = Depends(require_admin),
):
    """管理员抓取、模型提取并原子保存文章，返回摘要而非整篇正文。

    同 URL 更新原行。抓取/robots/目标网络失败 400；提取或字段宽度校验失败 422；
    ExtractError 的原因是 LLMError 时返回 502；动态浏览器停用返回 503。
    数据库基础设施故障不包装成输入错误。成功保存会由 save_article 提交事务。"""
    try:
        if data.dynamic:
            # 浏览器路径：渲染完再走**同一套**解析（parse_page），
            # 保证换个引擎不会换出一套不同的解析行为。
            page = await render(data.url)
            parsed = await parse_page(page.url, page.html, llm=llm)
        else:
            parsed = await parse_article(data.url, llm=llm)
        row = await save_article(db, parsed)
    except BrowserUnavailable as e:
        # 动态渲染停用或可选依赖缺失，不是调用方输入错误 → 503。
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    except RobotsDisallowed as e:
        # 独立于 CrawlError 的异常类型，漏接就会变成 500 —— 而这是调用方
        # 「给了一个不让抓的 URL」，不是本站出故障。
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"目标站不允许抓取：{e}") from e
    except CrawlError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"抓取失败：{e}") from e
    except httpx.HTTPError as e:
        # 目标站连不上 / 超时 / TLS 失败。同样不是本站故障，不该报 500。
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"抓取失败：{e}") from e
    except ExtractError as e:
        if isinstance(e.__cause__, LLMError):
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"大模型调用失败：{e.__cause__}"
            ) from e
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"内容提取失败：{e}") from e

    return {
        "id": row.id,
        "url": row.url,
        "title": row.title,
        "author": row.author,
        "published_at": row.published_at,
        "source_site": row.source_site,
        "content_length": len(row.content),
        "ingested_by": admin.username,
        "dynamic": data.dynamic,
    }
