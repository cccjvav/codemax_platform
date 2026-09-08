"""TD-78 + TD-175：授权同意页。

改之前 `GET /oauth/authorize` 直接签发授权码并 302。TD-44 把登录态改成 cookie 之后，
这就成了一个**新引入**的 CSRF 面：`SameSite=Lax` 挡不住跨站顶层导航的 GET，
所以攻击者只要让用户点一个链接，浏览器就会带着 cookie 打过去、**替用户签发一个
授权码**并重定向到合法客户端 —— 用户全程什么都没看见。

现在的契约是：**GET 只渲染一张页面，签不出任何东西；签发只发生在 POST，
且 POST 必须带上由本站签发的表单签名。** 下面每条都对应这条契约的一个面。
"""
import re
from datetime import datetime, timedelta, timezone

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


# ---------------------------------------------------------------- P1-9：过期码要清


async def test_expired_and_used_codes_are_purged(client):
    """P1-9：oauth_code 只写不清 —— 每次授权流都留一条永久记录。

    旧实现只在**读**的时候判过期（token 端点），过期行和已用行都永远留在表里。
    授权码是一次性、几分钟就作废的凭据，留着的唯一后果是表无限增长。

    清理放在**签发时顺带做**（不引入调度器/后台任务）：清理量与流量成正比，
    忙站自己清干净，闲站本来也不产生垃圾。
    """
    h = await login(client)
    await sso_authorize(client, h)
    await sso_authorize(client, h)
    assert await code_count() == 2

    # 把这两条改成「早该被清掉」的样子：一条过期，一条已使用
    async with TestSession() as s:
        rows = (await s.scalars(select(OAuthCode))).all()
        rows[0].expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        rows[1].used = True
        await s.commit()
    assert await code_count() == 2  # 清理还没被触发

    r = await sso_authorize(client, h)  # 再签一个，顺带清理
    assert r.status_code == 302
    assert await code_count() == 1, (
        "签发新授权码时应顺带清掉已过期与已使用的旧码；只写不清会让 oauth_code 无限增长"
    )


async def test_valid_unused_code_survives_purge(client):
    """反面：未过期、未使用的码**绝不能**被清掉 —— 那会打断正在进行的登录。

    清理逻辑最容易写错的方向就是这个：条件放宽一点，用户点了同意、正要拿码换
    token 的那一瞬间码被删了，表现为「登录偶发失败」，极难排查。
    """
    h = await login(client)
    await sso_authorize(client, h)
    await sso_authorize(client, h)  # 第二次签发会触发清理
    assert await code_count() == 2, "两条码都仍然有效，谁都不该被清掉"


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
