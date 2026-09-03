"""TD-78 + TD-175：授权同意页。

改之前 `GET /oauth/authorize` 直接签发授权码并 302。TD-44 把登录态改成 cookie 之后，
这就成了一个**新引入**的 CSRF 面：`SameSite=Lax` 挡不住跨站顶层导航的 GET，
所以攻击者只要让用户点一个链接，浏览器就会带着 cookie 打过去、**替用户签发一个
授权码**并重定向到合法客户端 —— 用户全程什么都没看见。

现在的契约是：**GET 只渲染一张页面，签不出任何东西；签发只发生在 POST，
且 POST 必须带上由本站签发的表单签名。** 下面每条都对应这条契约的一个面。
"""
import re

from sqlalchemy import func, select

from app.models import OAuthCode
from tests.conftest import TestSession, sso_authorize

TOOLS_CB = "https://tools.codemax.top/callback"
PARAMS = {"response_type": "code", "client_id": "tools", "redirect_uri": TOOLS_CB, "state": "xyz"}


async def login(client, username="bob") -> dict:
    await client.post("/auth/register", json={"username": username, "password": "secret123"})
    r = await client.post("/auth/login", data={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def code_count() -> int:
    async with TestSession() as s:
        return await s.scalar(select(func.count()).select_from(OAuthCode))


async def post(client, headers, *, sig="garbage", state="xyz", approve="1", redirect_uri=TOOLS_CB):
    return await client.post(
        "/oauth/authorize",
        data={"client_id": "tools", "redirect_uri": redirect_uri, "state": state,
              "sig": sig, "approve": approve},
        headers=headers,
        follow_redirects=False,
    )


# ---------------------------------------------------------------- GET 只渲染页面


async def test_get_renders_consent_page_and_issues_nothing(client):
    """核心：GET 返回同意页，**不签发授权码**、也不 302。"""
    h = await login(client)
    r = await client.get("/oauth/authorize", params=PARAMS, headers=h, follow_redirects=False)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "location" not in r.headers, "GET 不该再重定向 —— 那正是 CSRF 的落点"
    assert "工具平台" in r.text, "必须告诉用户是**哪个应用**在申请"
    assert "bob" in r.text, "必须告诉用户当前是**哪个账号**要被授权"
    assert await code_count() == 0, "GET 阶段绝不能签发授权码"


async def test_cross_site_navigation_cannot_issue_a_code(client):
    """TD-175 的正解：攻击者能做到的最多就是「让用户带着 cookie 打一个 GET」。

    这里刻意**不带 Authorization 头**，只用登录 cookie —— 完全复刻跨站顶层导航
    能构造出的请求。改之前它会拿到 302 + code，现在只能拿到一张页面。
    """
    await login(client)  # 只为了让 cookie 落到 client 上
    r = await client.get("/oauth/authorize", params=PARAMS, follow_redirects=False)

    assert r.status_code == 200
    assert "location" not in r.headers
    assert await code_count() == 0


# ---------------------------------------------------------------- 签名


async def test_post_without_valid_signature_is_rejected(client):
    h = await login(client)
    r = await post(client, h, sig="garbage")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_request"
    assert await code_count() == 0


async def test_tampered_state_breaks_the_signature(client):
    """签名盖住了 client_id / redirect_uri / state 三个参数 ——
    拿合法页面上的签名去改 state，必须被拒。"""
    h = await login(client)
    page = await client.get("/oauth/authorize", params=PARAMS, headers=h)
    sig = re.search(r'name="sig" value="([^"]+)"', page.text).group(1)

    r = await post(client, h, sig=sig, state="attacker-state")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_request"
    assert await code_count() == 0


async def test_valid_signature_issues_a_code(client):
    h = await login(client)
    r = await sso_authorize(client, h)
    assert r.status_code == 302
    assert "code=" in r.headers["location"]
    assert await code_count() == 1


# ---------------------------------------------------------------- 拒绝


async def test_decline_redirects_with_access_denied_and_issues_nothing(client):
    h = await login(client)
    r = await sso_authorize(client, h, approve="0")
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "error=access_denied" in loc
    assert "state=xyz" in loc, "state 必须原样回传，客户端靠它防 CSRF"
    assert "code=" not in loc
    assert await code_count() == 0


async def test_missing_approve_field_defaults_to_decline(client):
    """表单里少了 approve 字段时必须落到**更安全**的一侧（拒绝），而不是默认同意。"""
    h = await login(client)
    page = await client.get("/oauth/authorize", params=PARAMS, headers=h)
    sig = re.search(r'name="sig" value="([^"]+)"', page.text).group(1)

    r = await client.post(
        "/oauth/authorize",
        data={"client_id": "tools", "redirect_uri": TOOLS_CB, "state": "xyz", "sig": sig},
        headers=h,
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "error=access_denied" in r.headers["location"]
    assert await code_count() == 0


# ---------------------------------------------------------------- 页面本身


async def test_consent_page_adds_no_inline_script(client):
    """CSP 仍然允许 'unsafe-inline'（TD-163），新页面就别再添一个理由。"""
    h = await login(client)
    html = (await client.get("/oauth/authorize", params=PARAMS, headers=h)).text
    # 只检查本页自己的内容块，base.html 里没有 <script>
    assert "<script" not in html, "同意页不得含内联脚本"


async def test_consent_form_posts_back_to_the_authorize_endpoint(client):
    """表单的 action / 隐藏字段必须与 POST 端点期望的一致，否则用户点了同意会 422。"""
    h = await login(client)
    html = (await client.get("/oauth/authorize", params=PARAMS, headers=h)).text
    assert 'action="/oauth/authorize"' in html
    assert 'method="post"' in html
    for field in ("client_id", "redirect_uri", "state", "sig"):
        assert f'name="{field}"' in html, f"同意页表单缺少 {field} 字段"
