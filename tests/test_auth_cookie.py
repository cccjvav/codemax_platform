"""TD-44：登录态改走 HttpOnly cookie，前端不再碰 localStorage。

这里的重点不是「cookie 属性写对了」，而是三条会真正被违反的约束：

1. **前端脚本读不到 token**。用一个会抛错的 localStorage 桩真实执行 drawio 页面的
   内联脚本（node），谁再往浏览器存储里塞 token 就当场炸 —— 静态 grep 只能证明
   当下没有，跑一遍才能证明这段代码的运行路径上没有。
2. **cookie 与 Bearer 双通道都得活**。浏览器吃 cookie，Swagger / API 客户端吃
   Bearer 头；只测一条会在另一条上出事。
3. **SameSite=Lax 的前提是「有副作用的接口不能是 GET」**。Lax 只挡跨站的写方法，
   跨站顶层导航的 GET 照样带 cookie。所以把「需要登录的 GET 路由」钉成一个带
   理由的清单：新增一个就会被这条测试拦下来，逼作者说明它为什么没有副作用。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import settings
from app.deps import get_current_user
from app.security import AUTH_COOKIE
from main import app
from tests.conftest import iter_app_routes

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"

_FORM = {"username": "alice", "password": "secret123"}

# node 侧执行器：argv[2] = 从页面里抠出来的内联脚本（argv[1] 是执行器自己）。
# localStorage / sessionStorage 的每个方法都抛错 —— 前端只要碰一下就暴露。
_NODE_HARNESS = r"""
const calls = [];
const els = {};
const mkEl = () => ({
  value: "", textContent: "", innerHTML: "", hidden: false,
  appendChild() {}, click() {}, addEventListener() {}, focus() {},
  classList: { add() {}, remove() {} },   // base.html 的浮层用 classList 开关
});
global.document = { getElementById: (id) => (els[id] ||= mkEl()), createElement: mkEl };
// ⚠️ 浏览器里 `window` **就是**全局对象本身，不是一个独立对象。
// 以前桩成 `global.window = { addEventListener(){} }` 时，base.html 里的
// `window.CodeMaxAuth = ...` 只是往那个独立对象上挂了个属性，
// 页面脚本用裸标识符 `CodeMaxAuth` 引用它就会 `is not defined`。
// 让 window 指向 global 才与浏览器一致，共享模块也才能被页面脚本真正用上。
global.addEventListener = () => {};
global.window = global;
const boom = (what) => () => { throw new Error("前端不得" + what + "浏览器存储"); };
const storage = {
  getItem: boom("读取"), setItem: boom("写入"), removeItem: boom("删除"), clear: boom("清空"),
};
global.localStorage = storage;
global.sessionStorage = storage;
global.fetch = (url, opts = {}) => {
  calls.push({ url, method: opts.method || "GET", headers: opts.headers || {},
               credentials: opts.credentials });
  // 列表接口返回数组，其余返回单个对象 —— 与后端真实形状一致，
  // 否则 refreshList() 的 for...of 会先炸在桩上而不是炸在前端代码上。
  const body = url === "/diagrams"
    ? [{ id: 1, name: "图一" }]
    : { id: 1, name: "图一", username: "alice", access_token: "T" };
  return Promise.resolve({ ok: true, status: 200, json: async () => body });
};
process.on("unhandledRejection", (e) => { console.error("UNHANDLED " + e.message); process.exit(3); });

(async () => {
  new Function(require("fs").readFileSync(process.argv[2], "utf8"))();
  await new Promise((r) => setTimeout(r, 80));   // 等 checkAuth() 跑完
  // 走页面自己的 getElementById 入口填表单：脚本是点击时才懒取这些元素的，
  // 直接从外面往 els 里塞会得到 undefined。
  const byId = (id) => global.document.getElementById(id);
  byId("btn-auth").onclick();                      // 打开全站登录浮层（base.html）
  byId("auth-user").value = "alice";
  byId("auth-pass").value = "secret123";
  // 真实提交一次登录表单。S2-02-2 之后登录表单在 base.html 的浮层里，
  // id 也从 login-user/login-pass 改成了 auth-user/auth-pass。
  await byId("auth-form").onsubmit({ preventDefault() {} });
  await new Promise((r) => setTimeout(r, 80));
  console.log(JSON.stringify(calls));
})();
"""


def _attrs(response) -> list[str]:
    return [a.strip().lower() for a in response.headers["set-cookie"].split(";")]


def _script_with_login(html: str, auth_js: str) -> str:
    """拼出浏览器真正会执行的那一串脚本：**外部共享模块 + 页面内联脚本**。

    ⚠️ 这里踩过两个坑，都记下来：

    1. 早先的写法是「挑出含 `/auth/login` 的那一个 `<script>`」。S2-02-2 把登录逻辑
       收敛进全站浮层后，drawio 页自己的 script 里已不含 `/auth/login`，
       那个挑法会**只取到共享模块、完全没跑 drawio 的代码**，而测试仍然是绿的。
    2. 共享模块后来从 base.html 的内联脚本改成了外部文件 `/static/js/auth.js`
       （因为 base.html 也是 OAuth 同意页的父模板，那页必须零内联脚本，
       见 tests/test_oauth_consent.py）。所以它**不在 HTML 里**，必须单独取来拼在前面。

    顺序也有讲究：base.html 里那个 `<script src>` 在 `<main>` 之前，
    页面脚本在 content block 里 —— 文档顺序就是「共享模块先、页面后」。
    """
    hits = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert hits, "页面应该有自己的内联脚本"
    assert "CodeMaxAuth" in auth_js, "/static/js/auth.js 应该定义全站登录态模块"
    assert "/auth/login" in auth_js, "登录逻辑应该在共享模块里"
    return "\n;\n".join([auth_js, *hits])


async def _register_and_login(client) -> str:
    await client.post("/auth/register", json=_FORM)
    r = await client.post("/auth/login", data=_FORM)
    assert r.status_code == 200
    return r


# ---------------------------------------------------------------- cookie 属性


async def test_login_sets_httponly_lax_cookie(client):
    r = await _register_and_login(client)
    attrs = _attrs(r)
    assert "httponly" in attrs, "少了 HttpOnly —— 这条正是 TD-44 的全部意义"
    assert "samesite=lax" in attrs
    assert "path=/" in attrs, "cookie 必须全站可见，否则只有 /auth 下才带得上"
    assert "secure" not in attrs, "development 下不能加 Secure，否则本地 http 存不进去"
    assert any(a.startswith("max-age=") for a in attrs), "cookie 应与 token 同寿命"
    assert attrs[0].startswith(f"{AUTH_COOKIE}=")


async def test_secure_only_in_production(client, monkeypatch):
    await client.post("/auth/register", json=_FORM)
    monkeypatch.setattr(settings, "ENV", "production")
    r = await client.post("/auth/login", data=_FORM)
    assert "secure" in _attrs(r), "生产环境必须加 Secure，否则 token 会走明文 http"


# ---------------------------------------------------------------- 两条通道


async def test_cookie_alone_authenticates(client):
    """登录之后不带任何 Authorization 头也能访问 —— 靠的就是 cookie。"""
    await _register_and_login(client)
    r = await client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


async def test_bearer_still_works_without_cookie(client):
    """Swagger 的 Authorize 按钮和 API 客户端走 Bearer 头，不能因为改 cookie 就废掉。"""
    await client.post("/auth/register", json=_FORM)
    token = (await client.post("/auth/login", data=_FORM)).json()["access_token"]
    fresh = client.__class__(transport=client._transport, base_url=str(client.base_url))
    try:
        r = await fresh.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["username"] == "alice"
    finally:
        await fresh.aclose()


async def test_header_wins_over_cookie(client):
    """两者都带时以请求头为准：那是调用方显式表达的意图。"""
    await client.post("/auth/register", json=_FORM)
    await client.post("/auth/register", json={"username": "bob", "password": "secret456"})
    await client.post("/auth/login", data=_FORM)  # cookie 是 alice 的
    bob = (await client.post("/auth/login", data={"username": "bob", "password": "secret456"}))
    bob.cookies.clear()  # 丢掉 cookie，只留下面要用的头
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {bob.json()['access_token']}"})
    assert r.json()["username"] == "bob"


async def test_no_credentials_is_401_with_www_authenticate(client):
    r = await client.get("/auth/me")
    assert r.status_code == 401
    # auto_error=False 之后这个头要自己补，少了 Swagger 就不弹登录框
    assert r.headers.get("www-authenticate") == "Bearer"


async def test_garbage_cookie_is_401(client):
    client.cookies.set(AUTH_COOKIE, "not-a-jwt")
    assert (await client.get("/auth/me")).status_code == 401


# ---------------------------------------------------------------- 退出


async def test_logout_clears_cookie_and_ends_session(client):
    await _register_and_login(client)
    assert (await client.get("/auth/me")).status_code == 200
    out = await client.post("/auth/logout")
    assert out.status_code == 204
    attrs = _attrs(out)
    assert any(a in ("max-age=0", 'expires=thu, 01 jan 1970 00:00:00 gmt') for a in attrs), \
        f"退出没有让 cookie 立刻失效：{out.headers['set-cookie']}"
    assert (await client.get("/auth/me")).status_code == 401


# ---------------------------------------------------------------- 与 TD-70 的交界


async def test_password_change_revokes_old_cookie(client):
    """改密码后，**旧 cookie** 必须立刻失效（TD-70 的吊销要透过 cookie 通道同样生效）。"""
    await client.post("/auth/register", json=_FORM)
    old_token = (await client.post("/auth/login", data=_FORM)).json()["access_token"]

    stale = client.__class__(transport=client._transport, base_url=str(client.base_url))
    stale.cookies.set(AUTH_COOKIE, old_token)
    try:
        assert (await stale.get("/auth/me")).status_code == 200  # 改密码之前是好的
        r = await client.post("/auth/password",
                             json={"old_password": "secret123", "new_password": "secret999"})
        assert r.status_code == 200
        assert (await stale.get("/auth/me")).status_code == 401, "旧 cookie 没被吊销"
        # 当前会话换了新 cookie，不该被自己踢下线
        assert (await client.get("/auth/me")).status_code == 200
    finally:
        await stale.aclose()


# ---------------------------------------------------------------- 模板不得碰浏览器存储


def test_no_template_touches_browser_storage():
    offenders = []
    for f in sorted(TEMPLATES.glob("*.html")):
        body = re.sub(r"<!--.*?-->", "", f.read_text(encoding="utf-8"), flags=re.S)
        body = re.sub(r"\{#.*?#\}", "", body, flags=re.S)  # Jinja 注释不算
        for m in re.finditer(r"\b(?:local|session)Storage\s*[.\[]", body):
            offenders.append(f"{f.name}: {m.group(0)}")
    assert not offenders, f"前端又开始用浏览器存储存 token 了：{offenders}"


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
async def test_drawio_frontend_runs_without_browser_storage(client, tmp_path):
    """用 node 真实执行页面里的内联脚本：localStorage 桩会抛错，读了就当场失败。"""
    html = (await client.get("/tools/drawio")).text
    # 共享登录模块是外部文件（原因见 _script_with_login 的说明），要单独取来
    auth_js = (await client.get("/static/js/auth.js")).text
    # 用 tmp_path 而不是写死 /tmp 下的文件名：并发跑测试时不会互相踩，跑完自动清理。
    harness = tmp_path / "harness.js"
    script = tmp_path / "drawio.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    script.write_text(_script_with_login(html, auth_js), encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"前端脚本执行失败：\n{proc.stdout}\n{proc.stderr}"
    calls = json.loads(proc.stdout.strip().splitlines()[-1])

    assert any(c["url"] == "/auth/me" for c in calls), \
        "进页面应该问后端要登录态，而不是靠读存储判断"
    assert not [c for c in calls if "authorization" in {k.lower() for k in c["headers"]}], \
        "前端不应再自己拼 Authorization 头，cookie 由浏览器带"
    diagrams = [c for c in calls if c["url"] == "/diagrams"]
    assert diagrams, "登录后应刷新流程图列表"
    assert all(c["credentials"] == "same-origin" for c in diagrams), \
        "少了 credentials，浏览器不会带 cookie"


# ---------------------------------------------------------------- Lax 的前提


def _uses_auth(dependant) -> bool:
    if dependant.call is get_current_user:
        return True
    return any(_uses_auth(sub) for sub in dependant.dependencies)


# 需要登录的 GET 路由清单。**SameSite=Lax 挡不住跨站顶层导航的 GET**，
# 所以这张表里的每一项都必须是无副作用的读操作；新增一项就会让测试失败，
# 逼作者在这里写清楚它为什么没有副作用。
READ_ONLY_AUTHED_GET = {
    "/auth/me": "读当前用户",
    "/diagrams": "读流程图列表",
    "/diagrams/{diagram_id}": "读单张流程图",
    # OAuth 2.0 的授权端点按规范必须是用户代理重定向（GET），改不成 POST。
    # 这是本次改动**新引入**的残留 CSRF 面（改 Bearer 头时跨站根本伪造不出来），
    # 记为 TD-175，缓解手段见该条。
    "/oauth/authorize": "OAuth 规范要求 GET；残留 CSRF 面见 TD-175",
    "/shop/ping": "SSO 连通性探测，返回常量",
    "/shop/orders/{order_no}": (
        "查订单状态，供下单页每 3 秒轮询（S2-02-2）。"
        "**必须保持只读**：轮询里连「顺手关掉过期单」都不能做 —— 过期关单只在 "
        "create_order 里做，那是用户主动重新下单时的一次性动作。"
        "尤其不能改成调 mark_downloaded，那会把订单一次性烧掉。"
    ),
    "/tools/ping": "SSO 连通性探测，返回常量",
}


def test_authed_get_routes_are_read_only():
    from fastapi.routing import APIRoute

    got = {r.path for r in iter_app_routes(app.routes)
           if isinstance(r, APIRoute) and "GET" in r.methods and _uses_auth(r.dependant)}
    assert got == set(READ_ONLY_AUTHED_GET), (
        "需要登录的 GET 路由变了。Lax 挡不住跨站顶层导航的 GET，"
        "所以新增的必须是无副作用的读操作；有副作用请改成 POST。\n"
        f"多出来的：{got - set(READ_ONLY_AUTHED_GET)}\n"
        f"少掉的：{set(READ_ONLY_AUTHED_GET) - got}"
    )


def test_download_is_post_not_get():
    """一次性下载会烧掉额度（paid→downloaded），有副作用的接口不能是 GET。"""
    from fastapi.routing import APIRoute

    route = next(
        r for r in iter_app_routes(app.routes)
        if isinstance(r, APIRoute) and r.path == "/shop/download/{order_no}"
    )
    assert route.methods == {"POST"}


async def test_download_rejects_get(client):
    assert (await client.get("/shop/download/CM1")).status_code == 405
