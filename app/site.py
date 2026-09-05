"""站点级清单（S2-02-1）：工具信息集中在这里，同时驱动路由、导航、sitemap 与 TDK。

新增一个工具页只需要往 TOOLS 里加一条 —— 路由、首页导航、sitemap 自动跟上。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .config import settings

SITE_NAME = "CodeMax 在线工具"


@dataclass(frozen=True)
class Tool:
    key: str
    title: str  # 导航文案 / <title> 主体
    path: str  # 页面 URL
    description: str  # <meta name="description">
    keywords: str  # <meta name="keywords">
    template: str


HOME = Tool(
    key="home",
    title=SITE_NAME,
    path="/",
    description="免费在线开发工具箱：SQL DDL 转 ER 图、自然语言生成 UML 类图、DDL 一键导出 Word 数据字典，无需注册即可使用。",
    keywords="在线工具,ER图,SQL解析,UML类图,Mermaid,数据字典,免费开发工具",
    template="index.html",
)

TOOLS = (
    Tool(
        key="er",
        title="SQL DDL 转 ER 图",
        path="/tools/er",
        description="粘贴 CREATE TABLE 语句，免费在线生成 ER 实体关系图，支持 MySQL 与 PostgreSQL 语法（含中文注释、复合主键、外键），无需注册。",
        keywords="SQL转ER图,DDL解析,实体关系图,D3.js,数据库设计,在线工具",
        template="er.html",
    ),
    Tool(
        key="mermaid",
        title="自然语言生成 UML 类图",
        path="/tools/mermaid",
        description="用一句话或一段代码，免费在线生成 UML 类图（Mermaid 语法），支持继承、组合、聚合、关联关系，源码可直接复制。",
        keywords="UML类图,Mermaid,AI生成类图,自然语言建模,在线工具",
        template="mermaid.html",
    ),
    Tool(
        key="drawio",
        title="Drawio 在线流程图",
        path="/tools/drawio",
        description="在线流程图编辑器，iframe 嵌入 Drawio，免安装直接画图；登录后可云端保存与继续编辑，也可下载 .drawio 文件到本地。",
        keywords="在线流程图,Drawio,流程图编辑器,Visio替代,免费画图",
        template="drawio.html",
    ),
)

SHOP = Tool(
    key="shop",
    title="毕设服务",
    path="/shop",
    description="计算机毕设全流程服务：选题、系统设计与实现、论文与答辩辅导。在线下单，微信扫码支付，支付后即可下载交付物。",
    keywords="计算机毕设,毕业设计服务,毕设辅导,系统实现,论文辅导",
    template="shop.html",
)

# 路由与 sitemap 的完整页面清单。
#
# ⚠️ SHOP 加进 PAGES 但**刻意不加进 TOOLS**：
#   - 进 PAGES ⇒ 自动获得路由、sitemap 条目、TDK（SEO 要收录它）
#   - 不进 TOOLS ⇒ 不会混进 base.html 的工具导航与首页的工具卡片
#     （那两处遍历的是 TOOLS，把商业页混在免费工具里会稀释工具页的定位）
PAGES = (HOME, *TOOLS, SHOP)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def page_title(tool: Tool) -> str:
    """首页用站名本身，工具页拼站名后缀。"""
    return tool.title if tool.key == "home" else f"{tool.title} - {SITE_NAME}"


def page_context(
    request: Request,
    *,
    title: str,
    description: str = "",
    keywords: str = "",
    canonical: str = "",
    auth_ui: bool = True,
    **extra,
) -> dict:
    """`base.html` 需要的公共上下文。**所有**渲染 base.html 的页面都必须走这里。

    为什么要收口成一个函数：base.html 依赖 `site_name` / `tools` / `shop_path` /
    `product_name` 以及 SEO 三件套（description / keywords / canonical）。
    曾经 `/shop/mock-pay` 自己拼了一份、只传 `title` 和 `order_no`，结果页面渲染出
    `<h1><a href="/"></a></h1>` 空品牌、`<nav></nav>` 空导航、CTA 的 `href=""` ——
    它照样返回 200，而当时的测试只断言了「页面能开 + 有订单号」，所以一直没被发现。

    用 `**extra` 而不是固定参数：各页面自己的业务字段（`order_no`、ER 图的初始数据…）
    形状各异，但**公共字段必须齐**。这样新增页面时漏传公共字段这件事在结构上就不可能发生。
    """
    ctx: dict = {
        "request": request,
        "title": title,
        "description": description,
        "keywords": keywords,
        "canonical": canonical,
        "site_name": SITE_NAME,
        "tools": TOOLS,
        "shop_path": SHOP.path,
        # 商业页要展示商品与价格，但 `Tool` 数据类里不该塞商品字段（那是 SEO 元信息的
        # 容器）。单一 SKU 的事实来源仍是 settings，模板里不写死价格，改配置就跟着变。
        "product_name": settings.SHOP_PRODUCT_NAME,
        "product_amount": settings.SHOP_PRODUCT_AMOUNT,
        "auth_ui": auth_ui,  # 只有 OAuth 同意页传 False（那页必须零脚本，TD-204）
    }
    ctx.update(extra)
    return ctx
