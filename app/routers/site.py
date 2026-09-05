"""页面路由与 SEO（S2-02-1）：Jinja2 SSR 出 HTML 外壳与 TDK，图表仍客户端渲染。

页面路由由 `app/site.py` 的 PAGES 清单生成，sitemap / robots 也读同一份清单，
避免"加了页面忘了进 sitemap"这类漂移。
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..config import settings
from ..site import PAGES, Tool, page_context, page_title, templates

router = APIRouter(tags=["站点页面"])


def _base() -> str:
    return settings.SITE_BASE_URL.rstrip("/")


def _page_view(tool: Tool):
    async def view(request: Request):
        return templates.TemplateResponse(
            tool.template,
            page_context(
                request,
                title=page_title(tool),
                description=tool.description,
                keywords=tool.keywords,
                canonical=_base() + tool.path,
            ),
        )

    view.__name__ = f"page_{tool.key}"  # 路由名可读，便于 url_path_for 与排错
    return view


for _tool in PAGES:
    router.add_api_route(_tool.path, _page_view(_tool), methods=["GET"], include_in_schema=False)


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
