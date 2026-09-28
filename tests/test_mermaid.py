"""S2-01-2 测试：LLM → Mermaid 类图。

LLM 客户端全部注入（假客户端 / httpx.MockTransport），**测试不会真打网络**。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from app.tools.llm import LLMClient, LLMError, default_llm, generate_mermaid, get_llm
from main import app
from tests.conftest import iter_app_routes

# mermaid 页的交互脚本源码（C2 从模板内联搬出来的）。
# 在**模块级**读一次而不是在 async 用例里读：ruff 的 ASYNC240 会拦「async 函数里
# 用阻塞的 pathlib」，而且这文件在测试期间不会变，没必要每条用例都重读。
MERMAID_PAGE_JS = (
    Path(__file__).resolve().parents[1] / "app/frontend/mermaid-page.js"
).read_text(encoding="utf-8")


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

    # 调用的接口地址必须是真实注册过的路由，且读的是接口真正返回的字段。
    # ⚠️ C2 之后交互脚本抽成了外部文件，这两项要去**源码**里核对：
    #    构建产物是压缩过的，`data.mermaid` 会变成 `e.mermaid`（变量名被改），
    #    拿字面量去产物里找必然失败 —— 源码才是人写的、可读的那一份。
    assert "/static/js/mermaid-page.js" in r.text, "页面没加载自己的交互脚本"
    js = MERMAID_PAGE_JS
    urls = re.findall(r'["\'](/tools/[\w-]+)["\']', js)
    assert urls, "交互脚本里没有调用任何 /tools 接口"
    registered = {getattr(route, "path", None) for route in iter_app_routes(app.routes)}
    assert set(urls) <= registered, f"{sorted(set(urls) - registered)} 不是已注册路由"

    app.dependency_overrides[get_llm] = lambda: FakeLLM("classDiagram\nclass A")
    try:
        payload = (await client.post("/tools/mermaid", json={"text": "x"})).json()
    finally:
        app.dependency_overrides.pop(get_llm, None)
    for key in payload:
        assert f"data.{key}" in js, f"交互脚本没读接口返回的 {key} 字段"


# ---------------------------------------------------------------- TD-318：出错时按阶段说明，画不出来时给中文解释

_STAGE_HARNESS = r"""
const [path, scenario] = process.argv.slice(1);
const els = {}, errors = [];
let config = null;
const mkEl = (id) => ({ id, value: "", textContent: "", hidden: false, disabled: false, onclick: null, onsubmit: null,
  removeAttribute() {}, setAttribute() {} });
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)) };
global.window = global;
global.CodeMaxAuth = { errorText: (d, s) => (d && d.detail) || `请求失败（${s}）`,
  failureText: (e) => (e.name === "TypeError" && /failed to fetch/i.test(e.message) ? "网络连接失败，请检查网络后重试" : e.message) };
global.setTimeout = (fn) => { fn(); return 1; };
global.fetch = async () => {
  if (scenario === "offline") throw new TypeError("Failed to fetch");
  return { ok: true, status: 200, json: async () => ({ mermaid: "classDiagram\nclass Order {\n  +int id\n" }), headers: { get: () => null } };
};
// Mermaid 11 解析失败时抛出的是多行英文：第一行说哪一行出错，后面是指向出错位置的字符画和期望的记号。
const parseError = new Error("Parse error on line 3:\n...s Order {  +int id\n----------------------^\nExpecting 'STRUCT_STOP', got 'EOF'");
const stub = `Promise.resolve({ default: { initialize(c) { config = c; }, run: async () => { throw parseError; } } })`;
const code = require("fs").readFileSync(path, "utf8").replace(/import\(\s*"mermaid"\s*\)/, stub);
(async () => {
  require("vm").runInThisContext(code, { filename: path });
  const error = els["mermaid-error"];
  Object.defineProperty(error, "textContent", { set(v) { errors.push(v); }, get() { return errors.at(-1) || ""; } });
  els["text-input"].value = "订单";
  await els["mermaid-form"].onsubmit({ preventDefault() {} });
  console.log(JSON.stringify({ error: errors.at(-1), shown: !error.hidden, source: els["mermaid-source"].textContent,
    sourceShown: !els["mermaid-source"].hidden, preview: els["mermaid-preview"].textContent, config,
    submitEnabled: !els["mermaid-submit"].disabled }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def _run_stage(scenario: str) -> dict:
    proc = subprocess.run(["node", "-e", _STAGE_HARNESS, str(Path(__file__).resolve().parents[1] / "app/frontend/mermaid-page.js"),
                           scenario], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
def test_network_failure_is_reported_as_a_request_failure_not_a_render_failure():
    """断网时请求根本没发出去，原来也显示「渲染失败：…」。"""
    out = _run_stage("offline")
    assert out["error"] == "请求失败：网络连接失败，请检查网络后重试"
    assert out["submitEnabled"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
def test_unrenderable_model_output_gets_a_chinese_explanation_and_keeps_the_source():
    """服务端只核对首行图类型，模型给出的源码可能有语法错误。原来显示解析器的多行英文原文，
    预览区里还有 Mermaid 自己画的「Syntax error in text」炸弹图。"""
    out = _run_stage("parse-error")
    assert out["config"]["suppressErrorRendering"] is True, "不让 Mermaid 往预览区画错误图"
    assert out["config"]["securityLevel"] == "strict"
    assert out["error"] == ("无法绘制模型生成的图（Mermaid 提示：Parse error on line 3:）。"
                            "下方是模型给出的源码，可以调整描述后重新生成，或复制源码自行修改。")
    assert "Expecting" not in out["error"] and "^" not in out["error"], "字符画和期望记号不进提示"
    assert out["sourceShown"] and out["source"].startswith("classDiagram"), "源码仍然给用户"
    assert out["preview"] == "", "预览区不再重复一份源码原文"
    assert out["submitEnabled"] is True


def test_source_and_bundle_agree_on_render_failure_handling():
    """产物把 Mermaid 整个打了进来，Node 里跑不了；核对不会被压缩改写的字面量。"""
    bundle = (Path(__file__).resolve().parents[1] / "app/static/js/mermaid-page.js").read_text(encoding="utf-8")
    assert "suppressErrorRendering" in bundle and "无法绘制模型生成的图" in bundle and "请求失败：" in bundle, "忘了 npm run build？"


def test_error_messages_point_at_the_source_box_where_it_actually_is():
    """错误提示让用户去看「源码」，方位词得与模板一致（TD-318）：模板里源码框紧跟在错误提示**之后**，
    桌面双栏与手机单栏都在它下方。原来两条提示都写「上方是…源码」，用户往上找只能看到输入框。"""
    root = Path(__file__).resolve().parents[1]
    template = (root / "app" / "templates" / "mermaid.html").read_text(encoding="utf-8")
    assert template.index('id="mermaid-error"') < template.index('id="mermaid-source"')
    for path in (root / "app" / "frontend" / "mermaid-page.js", root / "app" / "static" / "js" / "mermaid-page.js"):
        text = path.read_text(encoding="utf-8")
        assert "上方是" not in text, f"{path.name} 仍说源码在上方"
        assert text.count("下方是") == 2, f"{path.name}：渲染失败与组件下载失败两条提示都应指向下方的源码框"
