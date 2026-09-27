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
    assert r.json()["error"] == "invalid_grant"


async def test_token_rejects_wrong_secret_and_uri(client):
    headers = await register_and_login(client)
    code = sso_code(await authorize(client, headers))

    r = await exchange_code(client, code, secret="wrong-secret")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_client"

    # 重新授权拿新 code（上一个已被错误请求消费失败不影响，但换 URI 校验）
    code = sso_code(await authorize(client, headers))
    r = await exchange_code(client, code, redirect_uri="https://evil.com/cb")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


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
    assert r.json()["error"] == "invalid_grant"
    assert "expired" in r.json()["error_description"]


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
    assert r.json()["error"] == "unsupported_grant_type"

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
    assert r.json()["error"] == "invalid_grant"


@pytest.mark.skipif(
    TEST_DATABASE_URL.startswith("sqlite"),
    reason="本项指定PostgreSQL验证并发授权码消费；SQLite独立连接不能替代PG隔离验收",
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
    assert failed.json()["error"] == "invalid_grant"


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


# ---------------------------------------------------------------- TD-294：令牌端点错误格式（RFC 6749 §5.2）

_RFC6749_DESCRIPTION_CHARS = {chr(c) for c in (0x20, 0x21, *range(0x23, 0x5C), *range(0x5D, 0x7F))}
_VALID_TOKEN_FORM = {"grant_type": "authorization_code", "code": "x", "redirect_uri": TOOLS_CB,
                     "client_id": "tools", "client_secret": "codemax-tools-secret"}


def _assert_rfc6749_error(r, error: str) -> None:
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == error and "detail" not in body
    assert set(body) <= {"error", "error_description"}
    assert set(body.get("error_description", "")) <= _RFC6749_DESCRIPTION_CHARS, body
    assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"


@pytest.mark.parametrize(("change", "error"), [
    ({"grant_type": "password"}, "unsupported_grant_type"),
    ({"client_secret": "wrong"}, "invalid_client"),
    ({"client_id": "ghost"}, "invalid_client"),
    ({"code": "not-a-real-code"}, "invalid_grant"),
    ({"code": "bad\x00code"}, "invalid_grant"),
    ({"client_secret": None}, "invalid_request"),     # 缺参数：400 invalid_request，不是 FastAPI 的 422
    ({"grant_type": ""}, "invalid_request"),
    ({"code": ["a", "b"]}, "invalid_request"),        # §3.2：参数不得重复
])
async def test_token_errors_use_the_rfc6749_top_level_shape(client, change, error):
    data = {**_VALID_TOKEN_FORM, **change}
    data = {k: v for k, v in data.items() if v is not None}
    _assert_rfc6749_error(await client.post("/oauth/token", data=data), error)


async def test_token_json_body_is_an_invalid_request_not_a_422(client):
    _assert_rfc6749_error(await client.post("/oauth/token", json=_VALID_TOKEN_FORM), "invalid_request")


async def test_token_success_is_not_cacheable_and_authorize_errors_keep_their_shape(client):
    headers = await register_and_login(client, "rfc_user")
    r = await exchange_code(client, sso_code(await authorize(client, headers)))
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"
    assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"
    # 授权页错误按用户决定保持原样（{"detail": {...}}），只有令牌端点换格式
    bad = await consent_page(client, headers, client_id="ghost")
    assert bad.status_code == 400 and bad.json()["detail"]["error"] == "invalid_client"


# ---------------------------------------------------------------- R-02 / TD-301：变异检查补的缺口
# 下面每条都对应一处「删掉或放宽这条判断后原有用例全部通过」的防线。


async def _set_client(client_id="tools", **values):
    async with TestSession() as s:
        await s.execute(update(OAuthClient).where(OAuthClient.client_id == client_id).values(**values))
        await s.commit()


def _code_of(response) -> str:
    assert response.status_code == 302, response.text
    return response.headers["location"].split("code=")[1].split("&")[0]


async def test_disabled_client_can_neither_render_consent_nor_redeem_a_code(client):
    """停用的客户端：同意页 400 invalid_client，停用前已签发的授权码也换不出令牌。"""
    headers = await register_and_login(client)
    code = _code_of(await authorize(client, headers))
    await _set_client(status=0)
    page = await consent_page(client, headers)
    assert page.status_code == 400 and page.json()["detail"]["error"] == "invalid_client"
    r = await exchange_code(client, code)
    assert r.status_code == 400 and r.json()["error"] == "invalid_client"


async def test_code_issued_to_one_client_cannot_be_redeemed_by_another(client):
    """授权码绑定签发时的客户端：另一个客户端即便凭证正确、回调地址也照抄，也换不出令牌。"""
    headers = await register_and_login(client)
    code = _code_of(await authorize(client, headers))
    r = await exchange_code(client, code, client_id="shop", secret="codemax-shop-secret")
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert (await exchange_code(client, code)).status_code == 200, "被拒的尝试不能消费掉授权码"


async def test_code_of_a_user_disabled_after_issue_is_not_redeemable(client):
    """后台禁用账号只改 status、不递增凭据版本，所以令牌端点的状态检查是唯一一道。"""
    headers = await register_and_login(client, username="soon_disabled")
    code = _code_of(await authorize(client, headers))
    async with TestSession() as s:
        await s.execute(update(User).where(User.username == "soon_disabled").values(status=0))
        await s.commit()
    r = await exchange_code(client, code)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("change", ["credential_version", "status"])
async def test_consent_submit_rechecks_the_locked_user_row(client, monkeypatch, change):
    """鉴权依赖读到用户之后、锁住用户行之前，凭据版本或状态被并发修改（改密、禁用）：不签发授权码。"""
    import app.routers.oauth as oauth

    real_lock = oauth.lock_user

    async def lock_after_concurrent_change(db, user_id):
        user = await real_lock(db, user_id)
        if change == "credential_version":
            user.credential_version += 1
        else:
            user.status = 0
        return user

    headers = await register_and_login(client)
    page = await consent_page(client, headers)
    sig = page.text.split('name="sig" value="')[1].split('"')[0]
    monkeypatch.setattr(oauth, "lock_user", lock_after_concurrent_change)
    r = await client.post("/oauth/authorize", headers=headers, follow_redirects=False, data={
        "client_id": "tools", "redirect_uri": TOOLS_CB, "state": "xyz", "sig": sig, "approve": "1"})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_grant"
    async with TestSession() as s:
        assert (await s.scalars(select(OAuthCode))).first() is None


async def test_consent_signature_is_bound_to_the_credential_revision(client):
    """改密前渲染的同意页，改密重新登录后不能再提交：签名里带凭据版本。"""
    headers = await register_and_login(client, username="rotator")
    page = await consent_page(client, headers)
    old_sig = page.text.split('name="sig" value="')[1].split('"')[0]
    rp = await client.post("/auth/password", headers=headers,
                           json={"old_password": "secret123", "new_password": "newsecret456"})
    new_headers = {"Authorization": f"Bearer {rp.json()['access_token']}"}
    r = await client.post("/oauth/authorize", headers=new_headers, follow_redirects=False, data={
        "client_id": "tools", "redirect_uri": TOOLS_CB, "state": "xyz", "sig": old_sig, "approve": "1"})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_request"


@pytest.mark.parametrize("approve", ["", "yes", "true", "01"])
async def test_only_the_exact_approve_value_issues_a_code(client, approve):
    """只有 approve=1 算同意；其它任何值都按拒绝处理，而不是「不是 0 就同意」。"""
    headers = await register_and_login(client)
    r = await sso_authorize(client, headers, approve=approve)
    assert r.status_code == 302 and "error=access_denied" in r.headers["location"]
    assert "code=" not in r.headers["location"]


async def test_only_the_code_response_type_is_supported(client):
    headers = await register_and_login(client)
    r = await client.get("/oauth/authorize", headers=headers, params={
        "response_type": "token", "client_id": "tools", "redirect_uri": TOOLS_CB})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "unsupported_response_type"


@pytest.mark.parametrize("registered", [
    "https://tools.codemax.top/callback#frag",
    "https://user:pw@tools.codemax.top/callback",
    "javascript://tools.codemax.top/callback",
    "https://tools.codemax.top:99999/callback",
    "https:///callback",
])
async def test_registered_redirect_uri_must_itself_be_safe(client, registered):
    """客户端只能经数据库管理登记，登记时不校验回调地址；所以即便请求与登记值完全相等，
    不安全的回调地址（片段、内嵌凭据、非 http(s)、非法端口、无主机）也必须拒绝。"""
    await _set_client(redirect_uri=registered)
    headers = await register_and_login(client)
    r = await consent_page(client, headers, redirect_uri=registered)
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_redirect_uri"


async def test_production_refuses_a_plain_http_redirect_even_if_registered(client, monkeypatch):
    from app.config import settings

    await _set_client(redirect_uri="http://tools.codemax.top/callback")
    headers = await register_and_login(client)
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "OAUTH_TRUSTED_CLIENT_IDS", ("tools",))
    r = await consent_page(client, headers, redirect_uri="http://tools.codemax.top/callback")
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_redirect_uri"


@pytest.mark.parametrize("client_id", ["to\x00ols", "t" * 65])
async def test_malformed_client_id_is_an_invalid_client_on_both_endpoints(client, client_id):
    """含 NUL 或超长的 client_id 直接判 invalid_client，不进数据库查询
    （PostgreSQL 的文本参数不能含 NUL，否则驱动报错成 500）。"""
    headers = await register_and_login(client)
    page = await consent_page(client, headers, client_id=client_id)
    assert page.status_code == 400 and page.json()["detail"]["error"] == "invalid_client"
    r = await exchange_code(client, "whatever", client_id=client_id)
    assert r.status_code == 400 and r.json()["error"] == "invalid_client"
