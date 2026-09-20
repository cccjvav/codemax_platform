"""LLM 客户端响应边界（TD-260，接手复核 B2）。

替换 `tests/audit_handoff_probes.py` 里「LLM 大无关字段」「深嵌套 JSON 异常」「bool/float 向量索引」
三项诊断：修复后这里是默认套件的保护性回归。全部用 MockTransport/合成字节流，不访问真实模型。

边界：这些断言只覆盖客户端**读取与解析**上游响应的资源上限和异常分类，
不证明模型输出正确、供应商实际行为或真实网络超时表现。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.tools.llm import RESPONSE_LIMIT, LLMClient, LLMError

MIB = 1024 * 1024
CHAT_OK = b'{"choices":[{"message":{"content":"ok"}}]}'


class Upstream(httpx.AsyncByteStream):
    """按 64 KiB 分块的上游响应体，记录客户端实际读走了多少字节。"""

    def __init__(self, prefix: bytes, padding: int, suffix: bytes):
        self.chunks = [prefix, *(b"x" * 65536 for _ in range(padding // 65536)), b"x" * (padding % 65536), suffix]
        self.read = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if chunk:
                self.read += len(chunk)
                yield chunk


def chat_client(handler, **kw) -> LLMClient:
    return LLMClient(api_key="test-only-key", embed_model="test-embedding", transport=httpx.MockTransport(handler), **kw)


def test_response_limit_is_one_mib():
    assert RESPONSE_LIMIT == 1 * MIB


async def test_chat_stops_reading_oversize_upstream_body():
    """短合法回答 + 2 MiB 无关 padding：读到 1 MiB 就停，分类 oversize，不返回 ok。"""
    body = Upstream(b'{"choices":[{"message":{"content":"ok"}}],"padding":"', 2 * MIB, b'"}')
    with pytest.raises(LLMError) as result:
        await chat_client(lambda r: httpx.Response(200, stream=body)).chat("s", "u")
    assert result.value.category == "oversize" and result.value.status_code is None
    assert body.read <= RESPONSE_LIMIT + 65536
    assert "test-only-key" not in str(result.value)


async def test_declared_oversize_content_length_is_refused_before_reading():
    body = Upstream(CHAT_OK, 0, b"")
    with pytest.raises(LLMError) as result:
        await chat_client(lambda r: httpx.Response(200, stream=body, headers={"Content-Length": str(3 * MIB)})).chat("s", "u")
    assert result.value.category == "oversize" and body.read == 0


async def test_embeddings_have_the_same_budget():
    body = Upstream(b'{"data":[{"index":0,"embedding":[0.1,0.2]}],"padding":"', 2 * MIB, b'"}')
    with pytest.raises(LLMError) as result:
        await chat_client(lambda r: httpx.Response(200, stream=body)).embeddings(["q"])
    assert result.value.category == "oversize" and body.read <= RESPONSE_LIMIT + 65536


async def test_error_status_bodies_are_not_read_either():
    """非 200 分类 http：状态照旧保留，正文既不读完也不回显。"""
    body = Upstream(b"upstream echoed test-only-key ", 2 * MIB, b"")
    with pytest.raises(LLMError) as result:
        await chat_client(lambda r: httpx.Response(429, stream=body)).chat("s", "u")
    assert result.value.category == "http" and result.value.status_code == 429
    assert body.read <= 65536 and "test-only-key" not in str(result.value)


@pytest.mark.parametrize("depth", [64, 1200])
async def test_deep_json_is_an_llm_error_not_a_recursion_error(depth):
    """1200 层嵌套之前会以 RecursionError 逃出 `except LLMError`；现在同样归为 response 类 LLMError。"""
    body = b"[" * depth + b"0" + b"]" * depth
    with pytest.raises(LLMError) as result:
        await chat_client(lambda r: httpx.Response(200, content=body)).chat("s", "u")
    assert result.value.category == "response"
    with pytest.raises(LLMError):
        await chat_client(lambda r: httpx.Response(200, content=body)).embeddings(["q"])


async def test_nested_but_legitimate_payload_still_parses():
    payload = {"choices": [{"message": {"content": "ok", "meta": {"a": {"b": {"c": [1, [2, [3]]]}}}}}]}
    assert await chat_client(lambda r: httpx.Response(200, json=payload)).chat("s", "u") == "ok"


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"not json", b"", b"{", b'"\\ud800"'])
async def test_malformed_bodies_are_llm_errors(raw):
    with pytest.raises(LLMError) as result:
        await chat_client(lambda r: httpx.Response(200, content=raw)).chat("s", "u")
    assert result.value.category == "response"


@pytest.mark.parametrize("index", [False, True, 0.0, 1.0, "0"])
async def test_embedding_index_must_be_a_real_int(index):
    """bool/float/字符串 index 之前靠等值比较混过：现在要求 `type is int`。"""
    def handler(r):
        return httpx.Response(200, json={"data": [{"index": index, "embedding": [0.1, 0.2]}]})

    with pytest.raises(LLMError, match="index"):
        await chat_client(handler).embeddings(["q"])


async def test_integer_indices_still_sort_correctly():
    def handler(r):
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]})

    assert await chat_client(handler).embeddings(["a", "b"]) == [[1.0], [2.0]]


async def test_whole_call_has_a_wall_clock_deadline():
    """上游慢慢滴字节、每块都在 httpx 读超时之内：整次调用仍受 `timeout` 总时限约束，分类 network。"""
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(1000):
                await asyncio.sleep(0.05)
                yield b" "

    client = chat_client(lambda r: httpx.Response(200, stream=Trickle()), timeout=0.3)
    with pytest.raises(LLMError) as result:
        await asyncio.wait_for(client.chat("s", "u"), 5)
    assert result.value.category == "network" and result.value.status_code is None
    assert isinstance(result.value.__cause__, TimeoutError)


async def test_normal_replies_still_work_with_content_length():
    payload = json.dumps({"choices": [{"message": {"content": "classDiagram\n  A --> B"}}]}).encode()
    assert (await chat_client(lambda r: httpx.Response(200, content=payload)).chat("s", "u")).startswith("classDiagram")
