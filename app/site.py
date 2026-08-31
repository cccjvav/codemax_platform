"""站点级清单（S2-02-1）：工具信息集中在这里，同时驱动路由、导航、sitemap 与 TDK。

新增一个工具页只需要往 TOOLS 里加一条 —— 路由、首页导航、sitemap 自动跟上。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.templating import Jinja2Templates

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
)

PAGES = (HOME, *TOOLS)  # 路由与 sitemap 的完整页面清单（首页 + 工具页）

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def page_title(tool: Tool) -> str:
    """首页用站名本身，工具页拼站名后缀。"""
    return tool.title if tool.key == "home" else f"{tool.title} - {SITE_NAME}"
