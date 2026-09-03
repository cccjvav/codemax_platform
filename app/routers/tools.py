from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from ..cpu_pool import run_cpu_bound
from ..deps import get_current_user
from ..models import User
from ..ratelimit import rate_limit
from ..schemas import ErDiagramIn, MermaidIn
from ..tools.llm import LLMClient, LLMError, generate_mermaid, get_llm
from ..tools.sql_ddl import parse_ddl
from ..tools.word import FILENAME, MIME_DOCX, build_data_dictionary

router = APIRouter(prefix="/tools", tags=["工具平台"])


@router.get("/ping")
async def ping(_: User = Depends(get_current_user)):
    """工具平台受保护端点：与商业平台共享同一登录态（SSO）。"""
    return {"platform": "tools", "message": "pong"}


@router.post("/er-diagram", dependencies=[Depends(rate_limit("er", "RATE_LIMIT_TOOLS"))])
async def er_diagram(data: ErDiagramIn) -> dict:
    """S2-01-1：解析 SQL DDL，返回 D3.js 可直接渲染的 ER 图数据。

    引流工具，故不设鉴权（便于 SEO 收录与游客直接使用）。

    `parse_ddl` 是**同步 CPU 密集**代码，必须丢到线程池里跑（S5-02-2）。
    直接在 `async def` 里调用会独占事件循环：实测满额 20000 字符的 DDL 单次
    33 ms，5 个并发时最后一个要等 169 ms（≈5×33，完全串行），期间**全站**
    请求都卡住 —— 智能客服的 <80ms 指标就是这么被打穿的（TD-159）。
    """
    graph = await run_in_threadpool(parse_ddl, data.ddl)
    if not graph["tables"]:
        raise HTTPException(400, "未解析到任何 CREATE TABLE 语句")
    return graph


@router.post("/mermaid", dependencies=[Depends(rate_limit("mermaid", "RATE_LIMIT_LLM"))])
async def mermaid(data: MermaidIn, llm: LLMClient = Depends(get_llm)) -> dict:
    """S2-01-2：自然语言/代码 → Mermaid 类图。

    同为引流工具，不设鉴权；LLM 客户端通过依赖注入，测试不会真打网络。
    """
    try:
        diagram = await generate_mermaid(data.text, llm=llm)
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"mermaid": diagram}


@router.post("/word-export", dependencies=[Depends(rate_limit("word", "RATE_LIMIT_TOOLS"))])
async def word_export(data: ErDiagramIn) -> Response:
    """S2-01-4：把 DDL 解析结果导出为 Word 数据字典（python-docx）。

    两步都要进线程池：`build_data_dictionary` 比 `parse_ddl` 贵得多 ——
    实测 28 表时 parse 32 ms、生成 docx **454 ms**，是前者的 13 倍。
    也就是说**一个** Word 导出请求就能把整个事件循环占住半秒（TD-159）。
    """
    graph = await run_in_threadpool(parse_ddl, data.ddl)
    if not graph["tables"]:
        raise HTTPException(400, "未解析到任何 CREATE TABLE 语句")
    return Response(
        # 生成 docx 走**进程**池而不是线程池：它要几百毫秒纯 Python 计算，
        # 线程池让得出事件循环却让不出 GIL，实测并发 p95 76.6ms vs 进程池 35.8ms
        await run_cpu_bound(build_data_dictionary, graph),
        media_type=MIME_DOCX,
        headers={"Content-Disposition": f'attachment; filename="{FILENAME}"'},
    )
