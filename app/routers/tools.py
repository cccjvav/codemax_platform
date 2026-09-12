from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from ..cpu_pool import CPUQueueFull, run_cpu_bound
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
    """公开但限流的 DDL → Word 数据字典接口，返回 DOCX 附件。

    DDL 解析在线程池，DOCX 生成进入有界 CPU 执行器（优先进程池）。
    无表 400；超过 100 表或合计 400 字段 413；任务槽满或响应等待超时 503 + Retry-After。"""
    graph = await run_in_threadpool(parse_ddl, data.ddl)
    if not graph["tables"]:
        raise HTTPException(400, "未解析到任何 CREATE TABLE 语句")
    if len(graph["tables"]) > 100 or sum(len(t["columns"]) for t in graph["tables"]) > 400:
        raise HTTPException(413, "导出最多支持 100 张表、400 个字段，请拆分 DDL")
    try:
        document = await run_cpu_bound(build_data_dictionary, graph)
    except CPUQueueFull as e:
        raise HTTPException(503, str(e), headers={"Retry-After": "5"}) from e
    return Response(
        # 生成 docx 走**进程**池而不是线程池：它要几百毫秒纯 Python 计算，
        # 线程池让得出事件循环却让不出 GIL。**注意「并发 p95 更快」不是选进程池的
        # 依据** —— 本机 2 核实测线程池 58~106ms、进程池 60~77ms，分布完全重叠，
        # 延迟量不出差别（TD-183/186）。真正的依据是
        # `test_build_data_dictionary_runs_in_a_separate_process`：直接查它跑在哪个
        # **进程**里 —— 线程池/同步都会落在本进程，判据确定，不受机器快慢影响（TD-193）。
        document,
        media_type=MIME_DOCX,
        headers={"Content-Disposition": f'attachment; filename="{FILENAME}"'},
    )
