"""S2-01-2 测试：LLM → Mermaid 类图。

LLM 客户端全部注入（假客户端 / httpx.MockTransport），**测试不会真打网络**。
"""
import json
import re

import httpx
import pytest

from app.tools.llm import LLMClient, LLMError, default_llm, generate_mermaid, get_llm
from main import app
from tests.conftest import iter_app_routes


class FakeLLM:
    """假客户端：记录收到的 prompt，返回预设内容或抛预设异常。"""

    def __init__(self, reply: str = "classDiagram\nclass User", exc: Exception | None = None):
        self.reply = reply
        self.exc = exc
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.exc:
            raise self.exc
        return self.reply


def _mock_transport(handler):
    return httpx.MockTransport(handler)


# ---------- generate_mermaid ----------


async def test_generate_mermaid_strips_fence_and_keeps_user_text():
    llm = FakeLLM("```mermaid\nclassDiagram\nclass User\n```")
    diagram = await generate_mermaid("用户和订单", llm=llm)
    assert diagram == "classDiagram\nclass User"
    system, user = llm.calls[0]
    assert user == "用户和订单"  # 用户原文必须进 prompt
    assert "classDiagram" in system


async def test_generate_mermaid_accepts_bare_diagram():
    diagram = await generate_mermaid("x", llm=FakeLLM("  classDiagram\nclass A --> B  "))
    assert diagram == "classDiagram\nclass A --> B"


async def test_generate_mermaid_accepts_unclosed_fence():
    """模型经常忘记收尾的 ``` —— 这种情况不能把合法类图判成失败。"""
    diagram = await generate_mermaid("x", llm=FakeLLM("```mermaid\nclassDiagram\nclass A\nclass B"))
    assert diagram == "classDiagram\nclass A\nclass B"


async def test_generate_mermaid_rejects_non_mermaid_reply():
    with pytest.raises(LLMError, match="未返回 Mermaid"):
        await generate_mermaid("x", llm=FakeLLM("抱歉，我无法完成这个请求。"))


async def test_generate_mermaid_propagates_llm_error():
    with pytest.raises(LLMError, match="大模型返回 500"):
        await generate_mermaid("x", llm=FakeLLM(exc=LLMError("大模型返回 500")))


# ---------- LLMClient（真实 HTTP 代码路径，用 MockTransport 不出网） ----------


async def test_client_posts_openai_compatible_request():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "classDiagram\nclass A"}}]})

    llm = LLMClient(
        api_key="sk-test",
        base_url="https://llm.test/v1/",  # 尾部斜杠不该拼出 //
        model="test-model",
        transport=_mock_transport(handler),
    )
    assert await llm.chat("系统提示", "用户输入") == "classDiagram\nclass A"
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "test-model"
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert seen["body"]["messages"][1]["content"] == "用户输入"


async def test_client_raises_on_http_error():
    llm = LLMClient(
        api_key="k",
        transport=_mock_transport(lambda req: httpx.Response(500, text="boom")),
    )
    with pytest.raises(LLMError, match="大模型返回 500"):
        await llm.chat("s", "u")


async def test_client_raises_on_unexpected_payload():
    llm = LLMClient(api_key="k", transport=_mock_transport(lambda req: httpx.Response(200, json={})))
    with pytest.raises(LLMError, match="不符合预期"):
        await llm.chat("s", "u")


async def test_client_refuses_without_api_key():
    """没配密钥就直接报错，不会偷偷发请求。"""
    with pytest.raises(LLMError, match="未配置 LLM_API_KEY"):
        await LLMClient(api_key="").chat("s", "u")


def test_dependency_returns_default_client():
    assert get_llm() is default_llm


# ---------- 接口 ----------


async def test_mermaid_endpoint_returns_diagram(client):
    fake = FakeLLM("classDiagram\nclass Order")
    app.dependency_overrides[get_llm] = lambda: fake
    try:
        r = await client.post("/tools/mermaid", json={"text": "用户下单"})
    finally:
        app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 200
    assert r.json() == {"mermaid": "classDiagram\nclass Order"}
    assert fake.calls[0][1] == "用户下单"


async def test_mermaid_endpoint_maps_llm_error_to_502(client):
    app.dependency_overrides[get_llm] = lambda: FakeLLM(exc=LLMError("大模型返回 429：限流"))
    try:
        r = await client.post("/tools/mermaid", json={"text": "用户下单"})
    finally:
        app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 502
    assert "限流" in r.json()["detail"]


async def test_mermaid_endpoint_rejects_empty_text(client):
    r = await client.post("/tools/mermaid", json={"text": ""})
    assert r.status_code == 422


# ---------- 前端页面 ----------


async def test_mermaid_page_served_and_wired(client):
    r = await client.get("/tools/mermaid")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="text-input"' in r.text

    # 调用的接口地址必须是真实注册过的路由，且读的是接口真正返回的字段
    urls = re.findall(r'"(/tools/[\w-]+)"', r.text)
    assert urls, "页面里没有调用任何 /tools 接口"
    registered = {getattr(route, "path", None) for route in iter_app_routes(app.routes)}
    assert set(urls) <= registered, f"{sorted(set(urls) - registered)} 不是已注册路由"

    app.dependency_overrides[get_llm] = lambda: FakeLLM("classDiagram\nclass A")
    try:
        payload = (await client.post("/tools/mermaid", json={"text": "x"})).json()
    finally:
        app.dependency_overrides.pop(get_llm, None)
    for key in payload:
        assert f"data.{key}" in r.text, f"页面没读接口返回的 {key} 字段"
