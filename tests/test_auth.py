async def register(client, username="alice", password="secret123"):
    return await client.post("/auth/register", json={"username": username, "password": password})


async def login(client, username="alice", password="secret123"):
    return await client.post("/auth/login", data={"username": username, "password": password})


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_register_success(client):
    r = await register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "alice"
    assert "password" not in body  # 密码哈希不得泄露


async def test_register_duplicate(client):
    await register(client)
    r = await register(client)
    assert r.status_code == 400


async def test_login_success_returns_token(client):
    await register(client)
    r = await login(client)
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"
    assert r.json()["access_token"]


async def test_login_wrong_password(client):
    await register(client)
    r = await login(client, password="wrong-pass")
    assert r.status_code == 401


async def test_me_with_token(client):
    await register(client)
    token = (await login(client)).json()["access_token"]
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


async def test_me_invalid_token(client):
    r = await client.get("/auth/me", headers={"Authorization": "Bearer bad.token.here"})
    assert r.status_code == 401


async def test_protected_endpoints_require_login(client):
    assert (await client.get("/tools/ping")).status_code == 401
    assert (await client.get("/shop/ping")).status_code == 401


async def test_sso_token_works_across_platforms(client):
    """SSO：登录一次，工具平台与商业平台共享同一 token。"""
    await register(client)
    token = (await login(client)).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/tools/ping", headers=headers)).status_code == 200
    assert (await client.get("/shop/ping", headers=headers)).status_code == 200
