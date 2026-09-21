"""TD-264 / ROADMAP A-02 后半：每进程 LLM 在途并发闸门。

用户确认的取值：同时最多 4 个模型请求在途（chat 与 embeddings、所有调用方共用），
第 5 个**立即**拒绝而不是排队；路由层映射成 503 + `Retry-After: 5`；客服链路照旧退到转人工；
前端 mermaid 页对 503 自动重试一次。不做站内日额度（供应商控制台的消费上限是钱包保险）。

全部用 MockTransport/事件屏障/Node 桩，不访问真实模型；单进程语义（TD-141），多实例各算各的。
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from app.tools import llm as llm_module
from app.tools.llm import BUSY_RETRY_AFTER, MAX_IN_FLIGHT, InFlightGate, LLMClient, LLMError, get_llm
from main import app

ROOT = Path(__file__).resolve().parents[1]
CHAT_OK = b'{"choices":[{"message":{"content":"classDiagram\\nclass A"}}]}'


@pytest.fixture(autouse=True)
def fresh_gate(monkeypatch):
    """每条用例一把新闸门：默认上限，计数从零开始，不受其他用例在途状态影响。"""
    gate = InFlightGate()
    monkeypatch.setattr(llm_module, "gate", gate)
    return gate


class Barrier:
    """上游替身：前 `hold` 个请求停在 `release` 之前，用来制造真实的在途状态。"""

    def __init__(self, hold: int):
        self.hold = hold
        self.arrived = 0
        self.all_arrived = asyncio.Event()
        self.release = asyncio.Event()
        self.served = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.arrived += 1
        if self.arrived <= self.hold:
            if self.arrived == self.hold:
                self.all_arrived.set()
            await self.release.wait()
        self.served += 1
        return httpx.Response(200, content=CHAT_OK)


def client_for(handler) -> LLMClient:
    return LLMClient(api_key="test-only-key", embed_model="test-embedding", transport=httpx.MockTransport(handler))


def test_agreed_values():
    assert MAX_IN_FLIGHT == 4 and BUSY_RETRY_AFTER == 5


def test_gate_rejects_immediately_when_full_and_counts_rejections():
    gate = InFlightGate(limit=2)
    gate.acquire("大模型")
    gate.acquire("大模型")
    with pytest.raises(LLMError) as result:
        gate.acquire("大模型")
    assert result.value.category == "busy" and "2" in str(result.value) and str(BUSY_RETRY_AFTER) in str(result.value)
    assert gate.in_flight == 2 and gate.rejected == 1, "被拒的请求不占槽"
    gate.release()
    gate.acquire("向量化接口")  # 释放一个就能再进一个
    assert gate.in_flight == 2


async def test_fifth_concurrent_call_is_refused_without_reaching_the_provider(fresh_gate):
    upstream = Barrier(hold=MAX_IN_FLIGHT)
    client = client_for(upstream)
    holders = [asyncio.create_task(client.chat("s", f"u{i}")) for i in range(MAX_IN_FLIGHT)]
    await asyncio.wait_for(upstream.all_arrived.wait(), 5)
    assert fresh_gate.in_flight == MAX_IN_FLIGHT

    with pytest.raises(LLMError) as result:
        await client.chat("s", "one too many")
    assert result.value.category == "busy"
    assert upstream.arrived == MAX_IN_FLIGHT, "第 5 个请求不得发往提供方"
    assert "test-only-key" not in str(result.value)

    upstream.release.set()
    replies = await asyncio.gather(*holders)
    assert len(replies) == MAX_IN_FLIGHT and fresh_gate.in_flight == 0, "全部完成后槽位归零"
    assert (await client.chat("s", "after")).startswith("classDiagram"), "释放后新请求照常"


async def test_slots_are_released_on_every_failure_path(fresh_gate):
    """HTTP 错误、网络异常、超时、超大响应都必须归还槽位，否则闸门会被失败请求慢慢占满。"""
    failing = [
        lambda r: httpx.Response(500),
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")),
        lambda r: httpx.Response(200, content=b"x" * (llm_module.RESPONSE_LIMIT + 1)),
    ]
    for handler in failing:
        with pytest.raises(LLMError):
            await client_for(handler).chat("s", "u")
        assert fresh_gate.in_flight == 0
    slow = LLMClient(api_key="k", timeout=0.05, transport=httpx.MockTransport(Barrier(hold=1)))
    with pytest.raises(LLMError) as result:
        await slow.chat("s", "u")
    assert result.value.category == "network" and fresh_gate.in_flight == 0
    assert fresh_gate.rejected == 0, "这些都是失败，不是被闸门拒绝"


async def test_embeddings_share_the_same_gate(fresh_gate):
    upstream = Barrier(hold=MAX_IN_FLIGHT)
    client = client_for(upstream)
    holders = [asyncio.create_task(client.chat("s", f"u{i}")) for i in range(MAX_IN_FLIGHT)]
    await asyncio.wait_for(upstream.all_arrived.wait(), 5)
    with pytest.raises(LLMError) as result:
        await client.embeddings(["x"])
    assert result.value.category == "busy" and upstream.arrived == MAX_IN_FLIGHT
    upstream.release.set()
    await asyncio.gather(*holders)


# ---------------------------------------------------------------- 路由映射


async def test_mermaid_endpoint_returns_503_with_retry_after_when_busy(client, fresh_gate):
    for _ in range(MAX_IN_FLIGHT):
        fresh_gate.acquire("大模型")  # 模拟四个在途请求；真实上游不参与
    app.dependency_overrides[get_llm] = lambda: client_for(lambda r: httpx.Response(200, content=CHAT_OK))
    try:
        r = await client.post("/tools/mermaid", json={"text": "用户下单"})
    finally:
        app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 503 and r.headers["retry-after"] == str(BUSY_RETRY_AFTER)
    assert "繁忙" in r.json()["detail"] and "test-only-key" not in r.text
    for _ in range(MAX_IN_FLIGHT):
        fresh_gate.release()


async def test_mermaid_endpoint_keeps_502_for_upstream_failures(client, fresh_gate):
    app.dependency_overrides[get_llm] = lambda: client_for(lambda r: httpx.Response(500))
    try:
        r = await client.post("/tools/mermaid", json={"text": "用户下单"})
    finally:
        app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 502 and "retry-after" not in r.headers


async def test_support_answer_escalates_to_human_when_busy(db, fresh_gate):
    """客服链路不暴露 503：闸门满时闲聊/RAG 与其他 LLM 故障一样退到转人工，原因可追溯。"""
    from app.tools.support import answer

    for _ in range(MAX_IN_FLIGHT):
        fresh_gate.acquire("大模型")
    reply = await answer("你好", db, llm=client_for(lambda r: httpx.Response(200, content=CHAT_OK)))
    for _ in range(MAX_IN_FLIGHT):
        fresh_gate.release()
    assert reply.escalated is True and reply.source == "human"
    assert "繁忙" in reply.reason


# ---------------------------------------------------------------- 前端：503 自动重试一次

_HARNESS = r"""
const path = process.argv[2];
const responses = JSON.parse(process.argv[3]);
const fetches = [], sleeps = [], messages = [];
const els = {};
const mkEl = (id) => ({ id, value: "", textContent: "", hidden: false, disabled: false, onclick: null, onsubmit: null,
  removeAttribute() {}, setAttribute() {} });
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)) };
global.window = global;
global.CodeMaxAuth = { errorText: (d, s) => (d && d.detail) || `请求失败（${s}）` };
global.setTimeout = (fn, ms) => { sleeps.push(ms); fn(); return 1; };
global.fetch = async (url, opts) => {
  fetches.push({ url, body: JSON.parse(opts.body) });
  const next = responses.shift() || { status: 200, body: { mermaid: "classDiagram" } };
  return { ok: next.status < 400, status: next.status, json: async () => next.body,
           headers: { get: (k) => (k.toLowerCase() === "retry-after" ? next.retryAfter ?? null : null) } };
};
// 源码是 ES module（import mermaid）：把 import 换成桩后再执行；产物则是自包含的 bundle。
const fs = require("fs");
let code = fs.readFileSync(path, "utf8").replace(/^import\s+mermaid\s+from\s+"mermaid";?/m, "const mermaid = { initialize() {}, run: async () => {} };");
(async () => {
  const vm = require("vm");
  vm.runInThisContext(code, { filename: path });
  const form = els["mermaid-form"], error = els["mermaid-error"], source = els["mermaid-source"];
  els["text-input"].value = "用户下单";
  const errors = [];
  const origSet = Object.getOwnPropertyDescriptor(error, "textContent");
  Object.defineProperty(error, "textContent", { set(v) { errors.push(v); }, get() { return errors.at(-1) || ""; } });
  await form.onsubmit({ preventDefault() {} });
  console.log(JSON.stringify({ fetches: fetches.length, sleeps, errors, source: source.textContent, submitEnabled: !els["mermaid-submit"].disabled }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def _run_page(tmp_path: Path, path: str, responses: list[dict]) -> dict:
    harness = tmp_path / "harness.cjs"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / path), json.dumps(responses)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"{path} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
def test_mermaid_page_retries_busy_once_then_renders(tmp_path):
    """只跑源码：产物把整个 Mermaid（几十个 ES module 分块）打了进来，Node 里不可执行；
    产物与源码的一致性由 CI 的 `npm run build` 漂移检查保证，下面另有文本级核对。"""
    result = _run_page(tmp_path, "app/frontend/mermaid-page.js", [
        {"status": 503, "body": {"detail": "大模型繁忙"}, "retryAfter": "5"},
        {"status": 200, "body": {"mermaid": "classDiagram\nclass Order"}},
    ])
    assert result["fetches"] == 2, "503 后应自动重试一次"
    assert result["sleeps"] == [5000], "等待时长取自 Retry-After"
    assert any("自动重试" in e for e in result["errors"])
    assert result["source"] == "classDiagram\nclass Order" and result["submitEnabled"]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
def test_mermaid_page_gives_up_after_one_retry_and_shows_server_message(tmp_path):
    result = _run_page(tmp_path, "app/frontend/mermaid-page.js", [
        {"status": 503, "body": {"detail": "大模型繁忙：请 5 秒后再试"}, "retryAfter": "bogus"},
        {"status": 503, "body": {"detail": "大模型繁忙：请 5 秒后再试"}, "retryAfter": "5"},
        {"status": 200, "body": {"mermaid": "classDiagram"}},
    ])
    assert result["fetches"] == 2, "只重试一次，不无限打转"
    assert result["sleeps"] == [5000], "Retry-After 不可解析时退回 5 秒"
    assert result["errors"][-1] == "大模型繁忙：请 5 秒后再试" and result["source"] == ""


def test_upstream_errors_do_not_trigger_retry(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("需要 node 执行真实前端代码")
    result = _run_page(tmp_path, "app/frontend/mermaid-page.js", [{"status": 502, "body": {"detail": "上游故障"}}])
    assert result["fetches"] == 1 and result["sleeps"] == [] and result["errors"][-1] == "上游故障"


def test_source_and_bundle_agree_on_retry_policy():
    """产物是压缩过的（变量名会变），只核对不会被改写的字面量：503 判断与重试提示文案。"""
    src = (ROOT / "app/frontend/mermaid-page.js").read_text(encoding="utf-8")
    bundle = (ROOT / "app/static/js/mermaid-page.js").read_text(encoding="utf-8")
    assert re.search(r"MAX_BUSY_RETRIES\s*=\s*1\b", src) and 'res.status === 503' in src
    assert "Retry-After" in bundle and "秒后自动重试" in bundle and re.search(r"===\s*503", bundle), "产物未包含 503 重试逻辑——忘了 npm run build？"
