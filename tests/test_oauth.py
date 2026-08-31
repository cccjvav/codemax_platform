from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import OAuthClient, OAuthCode, User
from tests.conftest import TestSession

TOOLS_CB = "https://tools.codemax.top/callback"


async def register_and_login(client, username="bob"):
    await client.post("/auth/register", json={"username": username, "password": "secret123"})
    r = await client.post("/auth/login", data={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def authorize(client, headers, redirect_uri=TOOLS_CB, state="xyz", client_id="tools"):
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
    r = await authorize(client, headers={})
    assert r.status_code == 401


async def test_authorize_rejects_bad_client_and_uri(client):
    headers = await register_and_login(client)
    r = await authorize(client, headers, client_id="hacker")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_client"

    r = await authorize(client, headers, redirect_uri="https://evil.com/cb")
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
    code = (await authorize(client, headers)).headers["location"].split("code=")[1].split("&")[0]

    assert (await exchange_code(client, code)).status_code == 200
    r = await exchange_code(client, code)  # 复用同一授权码
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_grant"


async def test_token_rejects_wrong_secret_and_uri(client):
    headers = await register_and_login(client)
    code = (await authorize(client, headers)).headers["location"].split("code=")[1].split("&")[0]

    r = await exchange_code(client, code, secret="wrong-secret")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "invalid_client"

    # 重新授权拿新 code（上一个已被错误请求消费失败不影响，但换 URI 校验）
    code = (await authorize(client, headers)).headers["location"].split("code=")[1].split("&")[0]
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
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1),
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
