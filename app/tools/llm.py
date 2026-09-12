"""LLM 客户端与 Mermaid 类图生成（S2-01-2）。

LLM 客户端**必须可注入**，否则测试会真打网络：
    async def generate_mermaid(text: str, llm: LLMClient = default_llm) -> str

走 OpenAI 兼容的 /chat/completions，因此 OpenAI / 通义千问兼容模式 / DeepSeek /
智谱 / 本地 Ollama 都能直接用，只换 .env 里的 base_url 与 model。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import httpx

from ..config import settings

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
    """LLM 调用失败（未配置密钥 / 网络 / 非 200 / 返回体缺字段 / 内容不是 Mermaid）。"""


@dataclass
class LLMClient:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    # 向量化必须单独指定模型：chat 用的 gpt-4o-mini 这类对话模型**不能**打 /embeddings，
    # 拿它去请求会得到 400 model_not_supported。换本地 Ollama 时这里要换成
    # 例如 nomic-embed-text，两个字段是独立的。
    embed_model: str = "text-embedding-3-small"
    timeout: float = 60.0
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)  # 测试注入 MockTransport

    async def chat(self, system: str, user: str) -> str:
        if not self.api_key:
            raise LLMError("未配置 LLM_API_KEY，无法调用大模型")
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            try:
                resp = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "temperature": 0.2,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"调用大模型失败：{exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"大模型返回 {resp.status_code}：{resp.text[:200]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
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
            raise LLMError("未配置 LLM_API_KEY，无法调用向量化接口")
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            try:
                resp = await client.post(
                    f"{self.base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.embed_model, "input": texts},
                )
            except httpx.HTTPError as exc:
                raise LLMError(f"调用向量化接口失败：{exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"向量化接口返回 {resp.status_code}：{resp.text[:200]}")
        try:
            rows = resp.json()["data"]
            if len(rows) != len(texts):
                raise ValueError(f"向量化接口返回 {len(rows)} 条，输入是 {len(texts)} 条")
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
        raise LLMError(f"大模型未返回 Mermaid 图，实际输出：{diagram[:120]}")
    return diagram


def _strip_fence(reply: str) -> str:
    """去掉模型爱加的 ```mermaid 围栏，只留图代码。"""
    m = _FENCE.search(reply)
    return (m.group(1) if m else reply).strip()
