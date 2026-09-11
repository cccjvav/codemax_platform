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
    """抓一个 URL，解析成文章并入库。同一 URL 重复抓是**更新**而不是新增。

    错误分三档，对应三种不同的责任方：
    - **400** URL 本身抓不了：`CrawlError`（非 http/https、内网地址、DNS 失败、
      目标非 200、页面过大）、`RobotsDisallowed`（目标站 robots 不允许）、
      `httpx.HTTPError`（目标站连不上/超时）。都是调用方给的东西有问题，不是本站故障。
    - **503** `dynamic=true` 但服务端浏览器不可用（没装 playwright 或没下浏览器二进制）。
      这是**本站能力缺失**，不是调用方的错，所以既不报 400 也不报 500。

    `dynamic=true` 时用无头浏览器渲染后再解析（TD-191）；渲染与静态抓取**共用同一套**
    `parse_page`，所以解析行为不会因引擎而变。SSRF 与 robots 校验在启动浏览器**之前**
    就跑完了 —— 浏览器同样会去访问调用方给的地址，这一步省不得。
    - **422** 提取失败：抓到了但提不出正文（选择器匹配不到、必需字段为空）。
      页面结构不适合，换个 URL 或改选择器提示词。
    - **502** 上游大模型不可用。不是调用方的错，也不该让他重试打本站。

    ⚠️ 502 这一档**不能**写成 `except LLMError`：`extract.identify_selectors`
    会把 `LLMError` 包成 `ExtractError` 再抛（`raise ExtractError(str(e)) from e`），
    所以大模型故障到这里时**已经是 ExtractError 了**，写成 `except LLMError`
    是永不可达的死分支，实测会把「未配置 LLM_API_KEY」报成
    「内容提取失败」（422）—— 诊断和状态码都错。改为顺着 `__cause__` 认回去。
    """
    try:
        if data.dynamic:
            # 浏览器路径：渲染完再走**同一套**解析（parse_page），
            # 保证换个引擎不会换出一套不同的解析行为。
            page = await render(data.url)
            parsed = await parse_page(page.url, page.html, llm=llm)
        else:
            parsed = await parse_article(data.url, llm=llm)
    except BrowserUnavailable as e:
        # 服务端能力缺失（没装 playwright / 没下浏览器），不是调用方的错 → 503。
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

    row = await save_article(db, parsed)
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
