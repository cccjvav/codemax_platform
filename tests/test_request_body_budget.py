"""请求体预算中间件（TD-260，接手复核 B1）。

替换 `tests/audit_handoff_probes.py` 里的「分块请求读完/422 回显」诊断：修复后这里是
默认套件的保护性回归。全部用进程内 ASGI + 合成字节流，不起真实服务器、不打网络。

边界说明：中间件只限制**应用读到的字节**，不是反向代理的 `client_max_body_size`；
生产仍应在网关设置一层上限。
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.middleware import (
    BODY_LIMIT_DEFAULT,
    BODY_LIMIT_DIAGRAMS,
    RequestBodyBudgetMiddleware,
    body_limit_for,
)
from tests.test_download import auth_headers

MIB = 1024 * 1024
JSON = {"Content-Type": "application/json"}


class CountingStream(httpx.AsyncByteStream):
    """按 64 KiB 分块发送、记录实际被消费字节数的请求体（不给 Content-Length）。"""

    def __init__(self, prefix: bytes, padding: int, suffix: bytes):
        self.chunks = [prefix, *(b"x" * 65536 for _ in range(padding // 65536)), b"x" * (padding % 65536), suffix]
        self.read = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if chunk:
                self.read += len(chunk)
                yield chunk


def test_limits_are_the_agreed_values():
    """1 MiB 默认、/diagrams 2 MiB（drawio XML 50 万字符 UTF-8 可到 ~1.5 MB）；支付回调路径不接管。"""
    assert BODY_LIMIT_DEFAULT == 1 * MIB
    assert BODY_LIMIT_DIAGRAMS == 2 * MIB
    assert body_limit_for("/tools/mermaid") == BODY_LIMIT_DEFAULT
    assert body_limit_for("/diagrams") == body_limit_for("/diagrams/12") == BODY_LIMIT_DIAGRAMS
    assert body_limit_for("/diagramsx") == BODY_LIMIT_DEFAULT  # 前缀必须是完整路径段
    assert body_limit_for("/shop/pay/notify") is None
    assert body_limit_for("/shop/refunds/notify") is None


async def test_declared_oversize_body_is_refused_before_reading(client):
    """Content-Length 超限：直接 413，一个字节都不读。"""
    body = CountingStream(b'{"text":"', 2 * MIB, b'"}')
    r = await client.post("/tools/mermaid", content=body, headers={**JSON, "Content-Length": str(2 * MIB + 11)})
    assert r.status_code == 413
    assert body.read == 0
    assert r.json() == {"detail": "请求体超过上限（1048576 字节）"}
    assert r.headers.get("connection") == "close"
    assert r.headers["X-Content-Type-Options"] == "nosniff"  # 仍套在安全头中间件之内


async def test_chunked_oversize_body_stops_at_the_budget(client):
    """无 Content-Length 的分块上传：读到预算就停，不再消费、也不回显剩余正文。"""
    body = CountingStream(b'{"text":"', 2 * MIB, b'"}')
    r = await client.post("/tools/mermaid", content=body, headers=JSON)
    assert r.status_code == 413
    assert body.read <= BODY_LIMIT_DEFAULT + 65536 + 9  # 至多多读一个块
    assert len(r.content) < 200


async def test_lying_content_length_does_not_bypass_the_budget(client):
    """声明很小、实际很大：按实际字节计数，同样 413。"""
    body = CountingStream(b'{"text":"', 2 * MIB, b'"}')
    r = await client.post("/tools/mermaid", content=body, headers={**JSON, "Content-Length": "16"})
    assert r.status_code == 413
    assert body.read <= BODY_LIMIT_DEFAULT + 65536 + 9


async def test_validation_errors_no_longer_echo_the_offending_input(client):
    """字段超限（≤1 MiB 但 > max_length）仍是 422，但错误里不再原样回显输入。"""
    text = "x" * 20000
    r = await client.post("/tools/mermaid", json={"text": text})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail and all("input" not in item for item in detail)
    assert all(text not in str(item) for item in detail)
    assert detail[0]["loc"] == ["body", "text"] and detail[0]["msg"]
    assert len(r.content) < 2000


async def test_small_bodies_are_untouched(client):
    r = await client.post("/tools/er-diagram", json={"ddl": "CREATE TABLE t (id INT PRIMARY KEY);"})
    assert r.status_code == 200 and r.json()["tables"]
    await client.post("/auth/register", json={"username": "budget_user", "password": "secret123"})
    r = await client.post("/auth/login", data={"username": "budget_user", "password": "secret123"})
    assert r.status_code == 200 and r.json()["access_token"]


def browser_json(payload: dict) -> bytes:
    """浏览器 `JSON.stringify` + fetch 发送的是未转义的 UTF-8；httpx 的 `json=` 会把非 ASCII 转成 \\uXXXX（6 字节/字）。"""
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


async def test_diagrams_get_the_larger_allowance(client):
    """DiagramIn 允许的最大正文（50 万字符 ≈ 1.5 MB UTF-8）在 /diagrams 通过预算，到别的路由则 413。"""
    headers = {**await auth_headers(client, "budget_drawio"), **JSON}
    content = "图" * 500000  # 150 万 UTF-8 字节：> 1 MiB 默认预算，< 2 MiB 的 /diagrams 预算
    r = await client.post("/diagrams", headers=headers, content=browser_json({"name": "big", "content": content}))
    assert r.status_code == 201
    assert (await client.get(f"/diagrams/{r.json()['id']}", headers=headers)).json()["content"] == content
    r = await client.post("/tools/mermaid", headers=headers, content=browser_json({"text": content}))
    assert r.status_code == 413
    r = await client.post("/diagrams", headers=headers, content=browser_json({"name": "too big", "content": "图" * 700000}))
    assert r.status_code == 413  # 2.1 MB：预算先于 max_length 校验拒绝，不会读完再 422


async def test_payment_callbacks_keep_their_own_streaming_budget(client, monkeypatch):
    """支付/退款回调不经过通用预算：它们自己的 64 KiB 流式上限与固定 FAIL 应答不变。"""
    from types import SimpleNamespace

    from app.routers import shop

    monkeypatch.setattr(shop, "pay_config", lambda: SimpleNamespace(notify_ready=True, platform_cert="t", api_v3_key="t", appid="t", mchid="t"))
    r = await client.post("/shop/pay/notify", content=b"x" * 65537)
    assert r.status_code == 413 and r.json() == {"code": "FAIL", "message": "回调报文过大"}
    body = CountingStream(b"", 2 * MIB, b"")
    r = await client.post("/shop/pay/notify", content=body)
    assert r.status_code == 413 and r.json()["code"] == "FAIL"
    assert body.read <= 65536 * 2  # 路由自己在 64 KiB 处停止读取


async def test_get_without_body_is_not_affected(client):
    """无请求体或 Content-Length: 0 的请求不受影响，非 http scope 直接透传。"""
    assert (await client.get("/healthz", headers={"Content-Length": "0"})).status_code == 200
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await RequestBodyBudgetMiddleware(inner)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


async def test_middleware_refuses_bad_or_negative_content_length():
    """畸形 Content-Length：非数字按实际字节计，负数直接 400，不会触发异常变 500。"""
    sent = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    app = RequestBodyBudgetMiddleware(inner)
    await app({"type": "http", "method": "POST", "path": "/x", "headers": [(b"content-length", b"-1")]}, receive, send)
    assert sent[0]["status"] == 400
    sent.clear()
    await app({"type": "http", "method": "POST", "path": "/x", "headers": [(b"content-length", b"abc")]}, receive, send)
    assert sent[0]["status"] == 200


@pytest.mark.parametrize("path", ["/tools/mermaid", "/support/ask", "/auth/register"])
async def test_every_json_route_refuses_2mib_bodies(client, path):
    r = await client.post(path, content=b'{"text":"' + b"x" * (2 * MIB) + b'"}', headers=JSON)
    assert r.status_code == 413
