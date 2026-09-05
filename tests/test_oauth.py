import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models import OAuthClient, OAuthCode, User
from tests.conftest import TEST_DATABASE_URL, TestSession, sso_authorize, sso_code

TOOLS_CB = "https://tools.codemax.top/callback"


async def register_and_login(client, username="bob"):
    await client.post("/auth/register", json={"username": username, "password": "secret123"})
    r = await client.post("/auth/login", data={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def consent_page(client, headers, redirect_uri=TOOLS_CB, state="xyz", client_id="tools"):
    """只走第一步：GET 同意页（TD-78 之后 GET 不再签发授权码）。"""
    return await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        },
        headers=headers,
        follow_redirects=False,
    )


async def authorize(client, headers, redirect_uri=TOOLS_CB, state="xyz", client_id="tools"):
    """走完同意流程，返回 POST 的 302 响应（Location 里带 code）。"""
    return await sso_authorize(
        client, headers, client_id=client_id, redirect_uri=redirect_uri, state=state
    )


async def exchange_code(client, code, redirect_uri=TOOLS_CB, client_id="tools", secret="codemax-tools-secret"):
    return await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": secret,
        },
    )


async def test_authorize_requires_login(client):
    """同意页与签发端点**都**要登录 —— 少一边就等于绕过。"""
    assert (await consent_page(client, headers={})).status_code == 401
    r = await client.post("/oauth/authorize", data={
        "client_id": "tools", "redirect_uri": TOOLS_CB, "state": "xyz", "sig": "x", "approve": "1",
    })
    assert r.status_code == 401


async def test_authorize_rejects_bad_client_and_uri(client):
    headers = await register_and_login(client)
    r = await consent_page(client, headers, client_id="hacker")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_client"

    r = await consent_page(client, headers, redirect_uri="https://evil.com/cb")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_redirect_uri"

    r = await authorize(client, headers, redirect_uri=TOOLS_CB)
    assert r.status_code == 302  # 合法客户端 + 合法回调才能通过


async def test_full_auth_code_flow(client):
    """完整授权码流程：登录 → 授权 → 换 token → 访问受保护资源。"""
    headers = await register_and_login(client)

    # 1. 用户授权，认证中心 302 跳回客户端回调，携带 code + state
    r = await authorize(client, headers)
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith(f"{TOOLS_CB}?code=")
    assert "state=xyz" in location
    code = location.split("code=")[1].split("&")[0]
    assert code

    # 2. 客户端用 code + client_secret 换 access_token
    r = await exchange_code(client, code)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    access_token = body["access_token"]

    # 3. access_token 直接访问双平台受保护资源（SSO 生效）
    assert (await client.get("/tools/ping", headers={"Authorization": f"Bearer {access_token}"})).status_code == 200
    assert (await client.get("/shop/ping", headers={"Authorization": f"Bearer {access_token}"})).status_code == 200


async def test_code_is_one_time_use(client):
    headers = await register_and_login(client)
    code = sso_code(await authorize(client, headers))

    assert (await exchange_code(client, code)).status_code == 200
    r = await exchange_code(client, code)  # 复用同一授权码
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"


async def test_token_rejects_wrong_secret_and_uri(client):
    headers = await register_and_login(client)
    code = sso_code(await authorize(client, headers))

    r = await exchange_code(client, code, secret="wrong-secret")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_client"

    # 重新授权拿新 code（上一个已被错误请求消费失败不影响，但换 URI 校验）
    code = sso_code(await authorize(client, headers))
    r = await exchange_code(client, code, redirect_uri="https://evil.com/cb")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"


async def test_expired_code_rejected(client):
    await register_and_login(client)
    async with TestSession() as s:
        user_id = (await s.execute(select(User.id).where(User.username == "bob"))).scalar_one()
        client_id = (await s.execute(select(OAuthClient.id).where(OAuthClient.client_id == "tools"))).scalar_one()
        s.add(OAuthCode(
            code="expired-code",
            user_id=user_id,
            client_id=client_id,
            redirect_uri=TOOLS_CB,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        await s.commit()

    r = await exchange_code(client, "expired-code")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"
    assert "过期" in r.json()["detail"]["error_description"]


async def test_unsupported_grant_type(client):
    r = await client.post(
        "/oauth/token",
        data={
            "grant_type": "password",
            "code": "x",
            "redirect_uri": TOOLS_CB,
            "client_id": "tools",
            "client_secret": "codemax-tools-secret",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "unsupported_grant_type"

async def test_code_consumption_is_atomic(client):
    """授权码消费必须是原子的 compare-and-set，不能"先查后改"（否则并发重放能换出两个 token）。

    真正的并发在 SQLite 单连接上复现不出来，这里直接钉住接口所依赖的原子语义：
    同一条"仅当未使用时置为已使用"的 UPDATE，第二次执行必须影响 0 行。
    """
    headers = await register_and_login(client)
    code = sso_code(await authorize(client, headers))

    async with TestSession() as s:
        stmt = update(OAuthCode).where(OAuthCode.code == code, OAuthCode.used.is_(False)).values(used=True)
        first = await s.execute(stmt)
        second = await s.execute(stmt)
        await s.commit()
    assert (first.rowcount, second.rowcount) == (1, 0)

    # 接口层面：该 code 已被消费，再用必须是 invalid_grant
    r = await exchange_code(client, code)
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"


@pytest.mark.skipif(
    TEST_DATABASE_URL.startswith("sqlite"),
    reason="需要真数据库：SQLite 用 StaticPool 共享单连接，两个请求排不成真正的并发",
)
async def test_code_single_use_under_real_concurrency(client):
    """真并发下同一授权码只能被消费一次。

    这条测试过去写不了：SQLite + StaticPool 只有一个共享连接，两个请求实际是串行的，
    测不出竞态，所以当时只断言了 CAS 语义本身（上面那条 rowcount 测试）。
    跑在真 PostgreSQL 上时两个请求各拿一条连接，第二个会在行锁上等第一个提交，
    然后重新判定 WHERE，命中 0 行 → invalid_grant。结果与调度顺序无关，不是碰运气。

    跑法：TEST_DATABASE_URL="postgresql+asyncpg://postgres@/codemax_test?host=/tmp/pgdata" pytest -q
    """
    headers = await register_and_login(client, username="racer")
    code = sso_code(await authorize(client, headers))

    results = await asyncio.gather(exchange_code(client, code), exchange_code(client, code))
    assert sorted(r.status_code for r in results) == [200, 400], "必须恰好一个成功、一个失败"
    failed = next(r for r in results if r.status_code == 400)
    assert failed.json()["detail"]["error"] == "invalid_grant"


async def test_oauth_token_still_works_after_password_change(client):
    """改过密码的用户，**新换出的** OAuth token 必须可用（TD-197）。

    令牌版本化（TD-70）的语义是「早于 `password_changed_at` 签发的 token 一律失效」，
    所以 `/oauth/token` 签发时必须带上**库里当前的** `password_changed_at`。
    漏传的话新 token 的 `pwd` 声明是 `None`，`get_current_user` 会立刻拒 ——
    结果是「改过密码的人 SSO 彻底用不了」，而且旧 token 照样失效，安全性没换来任何东西。

    这条测试覆盖完整链路，并同时钉住三件事：
      ① 改密码**前**签发的 token 失效（TD-70 的本意，不能被这次修复破坏）
      ② 改密码接口返回的新 token 可用
      ③ OAuth 新换出的 token 也可用
    """
    headers = await register_and_login(client, username="pwduser")

    # ① 改密码前的 token
    r = await client.get("/tools/ping", headers=headers)
    assert r.status_code == 200, r.text

    rp = await client.post(
        "/auth/password",
        json={"old_password": "secret123", "new_password": "newsecret456"},
        headers=headers,
    )
    assert rp.status_code == 200, rp.text

    assert (await client.get("/tools/ping", headers=headers)).status_code == 401, (
        "改密码后旧 token 必须失效（TD-70 的核心语义）"
    )

    new_headers = {"Authorization": f"Bearer {rp.json()['access_token']}"}
    assert (await client.get("/tools/ping", headers=new_headers)).status_code == 200, (
        "改密码接口返回的新 token 必须可用"
    )

    # ③ 走完整授权码流程换新 token
    oauth_token = (await exchange_code(client, sso_code(await authorize(client, new_headers)))
                   ).json()["access_token"]
    r = await client.get("/tools/ping", headers={"Authorization": f"Bearer {oauth_token}"})
    assert r.status_code == 200, (
        f"❌ 改过密码后新换出的 OAuth token 被判失效：{r.status_code} {r.text[:200]}"
    )
