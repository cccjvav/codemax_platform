"""页面路由与 SEO（S2-02-1）：Jinja2 SSR 出 HTML 外壳与 TDK，图表仍客户端渲染。

页面路由由 `app/site.py` 的 PAGES 清单生成，sitemap / robots 也读同一份清单，
避免"加了页面忘了进 sitemap"这类漂移。
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import settings
from ..site import PAGES, SITE_NAME, Tool, page_context, page_title, templates

router = APIRouter(tags=["站点页面"])


def _base() -> str:
    return settings.SITE_BASE_URL.rstrip("/")


def _page_view(tool: Tool):
    async def view(request: Request):
        # ⚠️ Starlette 1.x 的签名是 TemplateResponse(request, name, context)。
        # 旧签名 (name, context) 会把 context 字典当成模板名，Jinja 的模板缓存
        # 拿 dict 当 key 直接抛 TypeError: unhashable type: 'dict'。
        return templates.TemplateResponse(
            request,
            tool.template,
            page_context(
                request,
                title=page_title(tool),
                description=tool.description,
                keywords=tool.keywords,
                canonical=_base() + tool.path,
                # 可见的页面级标题（`<h2>`）。此前工具页只有顶栏的站点名 `<h1>`，
                # 读屏用户按标题跳转时看不到「这一页是干什么的」；首页不需要重复，
                # 所以首页为空、模板按有值才渲染。名字只有 `Tool.title` 一处来源。
                page_heading="" if tool.key == "home" else tool.title,
            ),
        )

    view.__name__ = f"page_{tool.key}"  # 路由名可读，便于 url_path_for 与排错
    return view


for _tool in PAGES:
    router.add_api_route(_tool.path, _page_view(_tool), methods=["GET"], include_in_schema=False)


async def not_found_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """没有路由匹配时，浏览器看到站内 404 页而不是 `{"detail":"Not Found"}`（TD-333）。

    只在三个条件同时成立时出 HTML，其余一律交回 FastAPI 默认处理（JSON，行为不变）：
    ① GET / HEAD —— 地址栏打开页面只会是这两种；
    ② Accept 含 text/html —— 浏览器导航会带，页面脚本的 fetch 默认是 `*/*`，curl 与 API 客户端也不带；
    ③ detail 是默认的 "Not Found" —— 即没有路由匹配（或静态目录里没有这个文件）。
       业务代码抛的 404 都写了中文原因（「流程图不存在」等），前端要读 detail，保持 JSON。
    """
    if (
        exc.status_code == 404
        and exc.detail == "Not Found"
        and request.method in ("GET", "HEAD")
        and "text/html" in request.headers.get("accept", "")
    ):
        return templates.TemplateResponse(
            request, "not_found.html", page_context(request, title=f"页面不存在 - {SITE_NAME}"), status_code=404
        )
    return await http_exception_handler(request, exc)


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap() -> Response:
    urls = "\n".join(f"  <url><loc>{_base()}{p.path}</loc></url>" for p in PAGES)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n",
        media_type="application/xml",
    )


@router.get("/robots.txt", include_in_schema=False)
async def robots() -> Response:
    return Response(
        f"User-agent: *\nAllow: /\n\nSitemap: {_base()}/sitemap.xml\n",
        media_type="text/plain",
    )
