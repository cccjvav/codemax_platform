from fastapi import APIRouter, Depends, HTTPException, Response

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
    """
    graph = parse_ddl(data.ddl)
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
    """S2-01-4：把 DDL 解析结果导出为 Word 数据字典（python-docx）。"""
    graph = parse_ddl(data.ddl)
    if not graph["tables"]:
        raise HTTPException(400, "未解析到任何 CREATE TABLE 语句")
    return Response(
        build_data_dictionary(graph),
        media_type=MIME_DOCX,
        headers={"Content-Disposition": f'attachment; filename="{FILENAME}"'},
    )
