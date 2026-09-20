"""可注入的 OpenAI 兼容模型客户端与 Mermaid 文本生成。

默认 Agnes 2.5 Flash；向量增强默认关闭，启用前仍需验证供应商协议。测试使用替身，不默认调用真实服务。
结构校验不证明模型输出事实正确；Mermaid 前缀检查也不是完整语法解析。"""
from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass, field

import httpx

from ..config import settings

# 上游响应体上限（解压后字节）。正常聊天回答几 KiB、12 条 FAQ 向量几十 KiB；1 MiB 之外只可能是
# 无关填充或异常上游，读到这里就停并归类 oversize，不再把整段响应缓冲进内存（TD-260）。
RESPONSE_LIMIT = 1 * 1024 * 1024

# Mermaid 认可的图类型首关键字（LLM 有时会给 erDiagram 而非 classDiagram，都放行）
_DIAGRAM_TYPES = (
    "classDiagram",
    "erDiagram",
    "graph",
    "flowchart",
    "sequenceDiagram",
    "stateDiagram",
    "mindmap",
)

SYSTEM_PROMPT = """你是 UML 建模助手，把用户给的自然语言描述或代码转成一张 Mermaid 类图。

硬性要求：
1. 只输出 Mermaid 代码，第一行必须是 classDiagram
2. 不要任何解释、前后缀文字，也不要 Markdown 代码围栏
3. 类名用英文大驼峰；关系用 Mermaid 标准语法（<|-- 继承、*-- 组合、o-- 聚合、--> 关联）
4. 属性写成 `+类型 名称`；能识别出主键/外键时在类内用 <<PK>> / <<FK>> 注解标出
5. 信息不足时按常见业务语义合理补全，不要反问"""

# ``` 围栏：闭合与**未闭合**都要能吃下（模型经常忘记收尾的 ```）
_FENCE = re.compile(r"```(?:mermaid|md)?[ \t]*\n(.*?)(?:```|\Z)", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """安全的错误摘要与分类；不把提供方正文/凭证写进日志或 HTTP 错误。"""

    def __init__(self, message: str, *, category: str = "response", status_code: int | None = None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


@dataclass
class LLMClient:
    api_key: str = ""
    base_url: str = "https://apihub.agnes-ai.com/v1"
    model: str = "agnes-2.5-flash"
    # 不假设 Agnes 提供向量模型，调用时必须明确填写支持的 ID。
    embed_model: str = ""
    timeout: float = 60.0
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)  # 测试注入 MockTransport

    async def _call(self, path: str, payload: dict, what: str):
        """一次 POST → 有界读取 → JSON。整次调用（连接、等待、读取）受 `timeout` 总时限约束。

        `timeout` 同时作为 httpx 的分项超时和 `asyncio.timeout` 的总时限：分项超时管不住
        "每块都赶在读超时之内慢慢滴字节" 的上游，总时限管得住。非 200 只取状态码，不读正文。
        """
        url = f"{self.base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with asyncio.timeout(self.timeout):
                async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                    async with client.stream("POST", url, headers=headers, json=payload) as resp:
                        if resp.status_code != 200:
                            raise LLMError(f"{what}返回 {resp.status_code}", category="http", status_code=resp.status_code)
                        return await _json_within_budget(resp, what)
        except httpx.HTTPError as exc:
            raise LLMError(f"调用{what}失败（{type(exc).__name__}）", category="network") from exc
        except TimeoutError as exc:
            raise LLMError(f"调用{what}超过 {self.timeout} 秒总时限", category="network") from exc

    async def chat(self, system: str, user: str) -> str:
        if not self.api_key:
            raise LLMError("未配置 LLM_API_KEY，无法调用大模型", category="configuration")
        data = await self._call(
            "/chat/completions",
            {
                "model": self.model,
                "temperature": 0.2,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            "大模型",
        )
        try:
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip() or len(content) > 100000:
                raise ValueError("content 必须是有界非空字符串")
            return content
        except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
            raise LLMError(f"大模型返回体不符合预期：{exc}") from exc

    async def embeddings(self, texts: list[str]) -> list[list[float]]:
        """文本 → 向量，走 OpenAI 兼容的 `/embeddings`。

        返回顺序**严格对应**输入顺序：OpenAI 只保证每条带 `index` 字段，
        并不保证数组本身有序，所以这里按 index 排序后再取 —— 语料和向量
        错一位，检索结果就全错，而且不会报错，属于最坏的一类 bug。

        空输入直接返回空列表，不打网络（启动预热会在语料为空时被调到）。
        """
        if not texts:
            return []
        if not self.api_key:
            raise LLMError("未配置 LLM_API_KEY，无法调用向量化接口", category="configuration")
        if not self.embed_model.strip():
            raise LLMError("未配置 LLM_EMBED_MODEL，不能调用向量化接口", category="configuration")
        data = await self._call("/embeddings", {"model": self.embed_model, "input": texts}, "向量化接口")
        try:
            rows = data["data"]
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ValueError("data 必须是对象数组")
            if len(rows) != len(texts):
                raise ValueError(f"向量化接口返回 {len(rows)} 条，输入是 {len(texts)} 条")
            # `type is int`：bool 是 int 的子类、0.0 == 0，等值比较会把 False/0.0 当成合法 index
            if any(type(r.get("index")) is not int for r in rows):
                raise ValueError("embedding index 必须是整数")
            ordered = sorted(rows, key=lambda r: r["index"])
            if [r["index"] for r in ordered] != list(range(len(texts))):
                raise ValueError("embedding index 必须完整且唯一")
            vectors = [r["embedding"] for r in ordered]
            if any(v == [] for v in vectors):
                raise ValueError("向量化接口返回了空向量")
            if any(not isinstance(v, list) or len(v) != len(vectors[0]) or not v or any(
                type(x) not in (int, float) or not math.isfinite(x) for x in v
            ) for v in vectors):
                raise ValueError("embedding 必须是同维、有限数值向量")
        except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
            raise LLMError(f"向量化接口返回体不符合预期：{exc}") from exc
        if len(vectors) != len(texts):
            raise LLMError(f"向量化接口返回 {len(vectors)} 条，输入是 {len(texts)} 条")
        if not vectors or not vectors[0]:
            raise LLMError("向量化接口返回了空向量")
        return vectors


async def _json_within_budget(resp: httpx.Response, what: str):
    """把已打开的流式响应读进内存并解析 JSON；超过 RESPONSE_LIMIT 立即停读。

    计数的是 httpx 解压后的字节，所以压缩炸弹也算解压体积。`json.loads` 对上千层嵌套
    抛 RecursionError（C 层递归保护，不会真的溢出栈），这里连同编码/语法错误一并归为
    LLMError，调用方的 `except LLMError` 才接得住。
    """
    declared = resp.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > RESPONSE_LIMIT:
        raise LLMError(f"{what}响应超过 {RESPONSE_LIMIT} 字节上限", category="oversize")
    raw = bytearray()
    async for chunk in resp.aiter_bytes():
        if len(raw) + len(chunk) > RESPONSE_LIMIT:
            raise LLMError(f"{what}响应超过 {RESPONSE_LIMIT} 字节上限", category="oversize")
        raw.extend(chunk)
    try:
        return json.loads(bytes(raw))
    except (ValueError, RecursionError) as exc:  # JSONDecodeError/UnicodeDecodeError 都是 ValueError
        raise LLMError(f"{what}返回体不是可解析的 JSON（{type(exc).__name__}）") from exc


default_llm = LLMClient(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    model=settings.LLM_MODEL,
    embed_model=settings.LLM_EMBED_MODEL,
)


def get_llm() -> LLMClient:
    """FastAPI 依赖：测试用 app.dependency_overrides 换成假客户端即可，不碰网络。"""
    return default_llm


async def generate_mermaid(text: str, llm: LLMClient = default_llm) -> str:
    """自然语言/代码 → Mermaid 类图源码。"""
    reply = await llm.chat(SYSTEM_PROMPT, text)
    diagram = _strip_fence(reply)
    if not diagram.startswith(_DIAGRAM_TYPES):
        raise LLMError("大模型未返回 Mermaid 图的认可类型前缀")
    return diagram


def _strip_fence(reply: str) -> str:
    """去掉模型爱加的 ```mermaid 围栏，只留图代码。"""
    m = _FENCE.search(reply)
    return (m.group(1) if m else reply).strip()
