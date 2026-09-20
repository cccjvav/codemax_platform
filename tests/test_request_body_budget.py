"""A-01 保护性回归：解析前的请求体预算与 422 不回显输入。

背景（审计 F-01 / 报告 P2-9）：FastAPI 在字段校验之前会把整个 JSON 体读进内存，
pydantic 的 `max_length` 只在读完之后才起作用；默认 422 还会把 `input` 原样回显，
一个 2 MiB 的匿名请求会换来 2 MiB 的响应。这里钉住的是修复后的行为：

- 声明超限的 Content-Length 在读任何字节之前就 413；
- 无长度（分块）与伪造小 Content-Length 的请求按实际字节计数，越过预算即 413，
  且不再继续消费上游字节；
- 读体超过时间预算 408；
- 每类路由的预算能装下 schema 允许的最大合法负载（含 JSON `\\uXXXX` 转义的最坏情况）；
- 微信回调仍由处理函数自己在 64 KiB 处按微信格式拒绝，中间件只是更外层的兜底；
- 422 只返回 loc/msg/type，不回显 input。

全部用 ASGI 合成请求；真实 uvicorn/h11 与反向代理的行为另行在部署环境核对，
这里的 200/413 只证明应用自身的策略，不证明代理层配置。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from annotated_types import MaxLen

from app import schemas
from app.config import settings
from app.middleware import (
    NOTIFY_BODY_LIMIT,
    RequestBodyBudgetMiddleware,
    body_budget_for,
)
from app.routers.messages import MessageIn
from main import app
from tests.test_e2e import signup

JSON = {"Content-Type": "application/json"}
CHUNK = b"x" * 65536


class CountingBody(httpx.AsyncByteStream):
    """按块产出请求体并记录上游实际被消费了多少字节；块之间可插入延迟。"""

    def __init__(self, chunks, delay: float = 0.0):
        self.chunks = chunks
        self.delay = delay
        self.read = 0

    async def __aiter__(self):
        for i, chunk in enumerate(self.chunks):
            if i and self.delay:
                await asyncio.sleep(self.delay)
            self.read += len(chunk)
            yield chunk


def _max_len(model, field: str) -> int:
    return next(m.max_length for m in model.model_fields[field].metadata if isinstance(m, MaxLen))


def _worst_case_json_bytes(*char_limits: int) -> int:
    # JSON 允许把任何字符写成 \\uXXXX（6 字节）；Python json.dumps 默认对非 ASCII 就是这么做的。
    # 再留 4 KiB 给键名、结构符和其它短字段。
    return sum(n * 6 for n in char_limits) + 4096


# ---------------------------------------------------------------- 预算表本身


def test_budgets_cover_the_largest_legal_payload_of_each_route():
    """预算不能小于 schema 允许的最大合法体，否则 413 会先于 422 挡住合法用户。"""
    assert _worst_case_json_bytes(_max_len(schemas.DiagramIn, "content"), _max_len(schemas.DiagramIn, "name")) <= body_budget_for("/diagrams")
    assert body_budget_for("/diagrams/12") == body_budget_for("/diagrams")
    assert _worst_case_json_bytes(_max_len(schemas.ErDiagramIn, "ddl")) <= body_budget_for("/tools/er-diagram")
    assert _worst_case_json_bytes(_max_len(schemas.ErDiagramIn, "ddl")) <= body_budget_for("/tools/word-export")
    assert _worst_case_json_bytes(_max_len(schemas.MermaidIn, "text")) <= body_budget_for("/tools/mermaid")
    assert _worst_case_json_bytes(_max_len(schemas.SupportIn, "text")) <= body_budget_for("/support/ask")
    assert _worst_case_json_bytes(_max_len(MessageIn, "body")) <= body_budget_for("/support/messages")
    assert _worst_case_json_bytes(_max_len(schemas.RegisterIn, "username"), _max_len(schemas.RegisterIn, "password")) <= body_budget_for("/auth/register")


def test_default_budget_is_the_small_one_and_diagram_budget_is_route_specific():
    assert body_budget_for("/tools/mermaid") == settings.MAX_REQUEST_BODY_BYTES
    assert body_budget_for("/no/such/route") == settings.MAX_REQUEST_BODY_BYTES
    assert body_budget_for("/diagrams") == settings.MAX_DIAGRAM_BODY_BYTES > settings.MAX_REQUEST_BODY_BYTES
    # 前缀不能放宽：/diagramsX、/diagrams/1/restore 都不是大体路由
    assert body_budget_for("/diagramsX") == settings.MAX_REQUEST_BODY_BYTES
    assert body_budget_for("/diagrams/1/restore") == settings.MAX_REQUEST_BODY_BYTES
    assert body_budget_for("/tools/er-diagram") == settings.MAX_TOOL_BODY_BYTES
    # 回调处理函数自己在 64 KiB 处拒绝；中间件兜底必须留出余量，否则微信格式的 413 会被 detail 格式抢先
    assert body_budget_for("/shop/pay/notify") == body_budget_for("/shop/refunds/notify") == NOTIFY_BODY_LIMIT > 65536


def test_middleware_is_installed_inside_logging_and_security_headers():
    """预算中间件必须在栈里，且位于日志/安全头之内：413 也要带安全头并被记录。"""
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "RequestBodyBudgetMiddleware" in names
    # user_middleware 列表顺序 = 后加在前（最外层在前）
    assert names.index("RequestLoggingMiddleware") < names.index("SecurityHeadersMiddleware") < names.index("RequestBodyBudgetMiddleware")


# ---------------------------------------------------------------- 声明长度


@pytest.mark.asyncio
async def test_declared_oversize_body_is_rejected_before_any_byte_is_read(client):
    body = CountingBody([CHUNK] * 4)
    r = await client.post("/tools/mermaid", content=body, headers={**JSON, "Content-Length": str(50 * 1024 * 1024)})
    assert r.status_code == 413
    assert body.read == 0, "声明超限就该拒绝，不该先读体"
    assert r.headers["connection"] == "close"
    assert r.headers.get("x-content-type-options") == "nosniff", "413 也要经过安全头中间件"
    assert "x-request-id" in r.headers
    assert len(r.content) < 512


@pytest.mark.asyncio
async def test_malformed_content_length_is_400(client):
    body = CountingBody([b'{"text":"hi"}'])
    r = await client.post("/tools/mermaid", content=body, headers={**JSON, "Content-Length": "12abc"})
    assert r.status_code == 400 and body.read == 0


@pytest.mark.asyncio
async def test_declared_length_within_budget_still_reaches_the_route(client):
    r = await client.post("/tools/mermaid", content=json.dumps({"text": ""}), headers=JSON)
    assert r.status_code == 422, "预算内的请求照常进入字段校验"


# ---------------------------------------------------------------- 实际字节计数


@pytest.mark.asyncio
async def test_chunked_body_without_length_is_capped_and_not_echoed(client):
    """这是原诊断探针的反向：以前 2 MiB 全被读入并全被回显。"""
    body = CountingBody([b'{"text":"', *[CHUNK] * 32, b'"}'])
    r = await client.post("/tools/mermaid", content=body, headers=JSON)
    assert r.status_code == 413
    assert body.read <= settings.MAX_REQUEST_BODY_BYTES + len(CHUNK), f"越过预算后仍消费了 {body.read} 字节"
    assert len(r.content) < 512, "不回显大输入"


@pytest.mark.asyncio
async def test_forged_small_content_length_is_still_capped(client):
    body = CountingBody([b'{"text":"', *[CHUNK] * 8, b'"}'])
    r = await client.post("/tools/mermaid", content=body, headers={**JSON, "Content-Length": "10"})
    assert r.status_code == 413
    assert body.read <= settings.MAX_REQUEST_BODY_BYTES + len(CHUNK)


@pytest.mark.asyncio
async def test_form_login_body_is_also_budgeted(client):
    body = CountingBody([b"username=", *[CHUNK] * 4, b"&password=x"])
    r = await client.post("/auth/login", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert body.read <= settings.MAX_REQUEST_BODY_BYTES + len(CHUNK)


@pytest.mark.asyncio
async def test_slow_body_hits_time_budget(client, monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_BODY_TIMEOUT_SECONDS", 0.3)
    body = CountingBody([b'{"text":"', b"x" * 10, b'"}'], delay=5.0)
    r = await asyncio.wait_for(client.post("/tools/mermaid", content=body, headers=JSON), timeout=3)
    assert r.status_code == 408
    assert r.headers["connection"] == "close"


# ---------------------------------------------------------------- 大体路由与回调保持可用


@pytest.mark.asyncio
async def test_diagram_route_accepts_schema_maximum_and_rejects_over_by_422_not_413(client):
    h = await signup(client, username="drawer", password="secret123")
    ok = await client.post("/diagrams", json={"name": "big", "content": "x" * _max_len(schemas.DiagramIn, "content")}, headers=h)
    assert ok.status_code == 201, ok.text
    too_long = await client.post("/diagrams", json={"name": "big", "content": "x" * (_max_len(schemas.DiagramIn, "content") + 1)}, headers=h)
    assert too_long.status_code == 422, "超过字符上限应由字段校验拒绝，而不是被字节预算误伤"
    assert "xxxxxxxx" not in too_long.text, "422 不回显输入"


@pytest.mark.asyncio
async def test_er_diagram_accepts_cjk_ddl_near_schema_maximum(client):
    # 6000 个汉字 = 18000 字符 < 20000，UTF-8 下 54 KB；JSON 转义最坏 108 KB —— 必须仍在工具预算内
    ddl = "CREATE TABLE t (" + "字" * 18000 + " INT);"
    r = await client.post("/tools/er-diagram", json={"ddl": ddl}, headers=JSON)
    assert r.status_code != 413


@pytest.mark.asyncio
async def test_payment_notify_keeps_its_own_wechat_format_413(client, monkeypatch):
    from tests.test_wechat_pay import _ENV

    for key, value in _ENV.items():
        monkeypatch.setattr(settings, key, value)
    r = await client.post("/shop/pay/notify", content=b"x" * 65537, headers=JSON)
    assert r.status_code == 413
    assert r.json().get("code") == "FAIL", "回调超限仍是处理函数的微信格式，不是中间件的 detail 格式"
    beyond = CountingBody([CHUNK] * 8)
    r2 = await client.post("/shop/pay/notify", content=beyond, headers=JSON)
    assert r2.status_code == 413
    assert beyond.read <= NOTIFY_BODY_LIMIT + len(CHUNK)


# ---------------------------------------------------------------- 422 不回显


@pytest.mark.asyncio
async def test_validation_error_drops_input_echo_but_keeps_location_and_message(client):
    r = await client.post("/tools/mermaid", json={"text": "y" * 20000}, headers=JSON)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail and set(detail[0]) == {"loc", "msg", "type"}
    assert detail[0]["loc"] == ["body", "text"]
    assert "yyyyyyyy" not in r.text
    assert len(r.content) < 1024


@pytest.mark.asyncio
async def test_invalid_json_422_has_no_input_field_either(client):
    r = await client.post("/tools/mermaid", content=b'{"text": ' + b"z" * 30000, headers=JSON)
    assert r.status_code == 422
    assert all("input" not in err and "ctx" not in err for err in r.json()["detail"])
    assert "zzzzzzzz" not in r.text


# ---------------------------------------------------------------- 纯 ASGI 合成（不经 httpx）


@pytest.mark.asyncio
async def test_pure_asgi_oversize_declared_length_gets_413_without_calling_app():
    called = False

    async def inner(scope, receive, send):
        nonlocal called
        called = True

    mw = RequestBodyBudgetMiddleware(inner)
    sent = []
    scope = {"type": "http", "method": "POST", "path": "/tools/mermaid", "headers": [(b"content-length", b"99999999")]}

    async def receive():  # pragma: no cover - 不应被调用
        raise AssertionError("不该读体")

    async def send(message):
        sent.append(message)

    await mw(scope, receive, send)
    assert not called
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 413
    assert sent[1]["type"] == "http.response.body"
    assert json.loads(sent[1]["body"])["detail"].startswith("请求体超过限制")
    assert (b"connection", b"close") in sent[0]["headers"]


@pytest.mark.asyncio
async def test_pure_asgi_non_http_scopes_pass_through_untouched():
    seen = {}

    async def inner(scope, receive, send):
        seen["scope"] = scope

    mw = RequestBodyBudgetMiddleware(inner)
    await mw({"type": "lifespan"}, None, None)
    assert seen["scope"] == {"type": "lifespan"}
