"""TD-70：改密码后旧 token 必须失效（令牌版本化，不用 jti 黑名单）。

顺带把 TD-70 原文里一处**不准确**的说法钉正：「封号后旧 token 仍然有效」并不成立 ——
`get_current_user` 每次都查库并检查 `user.status != 1`，禁用账号的 token 会立刻失效。
真正的缺口只有「改密码」这一条，本文件覆盖它。
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import User
from app.security import create_access_token
from tests.conftest import TestSession

OLD_PWD = "old-secret-1"
NEW_PWD = "new-secret-2"


async def signup(client, username="alice", password=OLD_PWD) -> dict:
    r = await client.post("/auth/register", json={"username": username, "password": password})
    assert r.status_code == 201, r.text
    return await login(client, username, password)


async def login(client, username, password) -> dict:
    r = await client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def change_password(client, headers, old=OLD_PWD, new=NEW_PWD):
    return await client.post(
        "/auth/password", headers=headers, json={"old_password": old, "new_password": new}
    )


# ============================================================ 核心：旧 token 失效


@pytest.mark.asyncio
async def test_old_token_is_rejected_after_password_change(client):
    """改密码的主要用途之一就是「怀疑泄露」，此时旧 token 还能用等于没改。"""
    h = await signup(client)
    assert (await client.get("/auth/me", headers=h)).status_code == 200

    r = await change_password(client, h)
    assert r.status_code == 200, r.text

    r2 = await client.get("/auth/me", headers=h)
    assert r2.status_code == 401, "改密码后旧 token 必须失效"
    assert "重新登录" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_change_password_returns_a_usable_new_token(client):
    """当前会话不该被自己踢下线 —— 要踢的是**其他**会话。"""
    h = await signup(client)
    new_token = (await change_password(client, h)).json()["access_token"]
    h2 = {"Authorization": f"Bearer {new_token}"}

    r = await client.get("/auth/me", headers=h2)
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


@pytest.mark.asyncio
async def test_all_other_sessions_die_but_the_new_one_lives(client):
    """多端登录：A、B 两个会话，从 A 改密码后两个旧 token 全废，只有新 token 活着。

    这正是选「令牌版本化」而不是 jti 黑名单的原因：一次就能全吊销，
    黑名单得先把所有 jti 枚举出来。
    """
    h_a = await signup(client, "bob")
    h_b = await login(client, "bob", OLD_PWD)  # 第二个会话
    assert (await client.get("/auth/me", headers=h_b)).status_code == 200

    new_token = (await change_password(client, h_a)).json()["access_token"]

    assert (await client.get("/auth/me", headers=h_a)).status_code == 401
    assert (await client.get("/auth/me", headers=h_b)).status_code == 401
    assert (
        await client.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    ).status_code == 200


@pytest.mark.asyncio
async def test_login_after_change_needs_the_new_password(client):
    h = await signup(client)
    await change_password(client, h)

    assert (await client.post("/auth/login", data={"username": "alice", "password": OLD_PWD})).status_code == 401
    assert (await client.post("/auth/login", data={"username": "alice", "password": NEW_PWD})).status_code == 200


# ============================================================ 改不动的情况


@pytest.mark.asyncio
async def test_wrong_old_password_changes_nothing(client):
    """原密码不对就什么都不该发生 —— 旧 token 继续有效。"""
    h = await signup(client)
    r = await change_password(client, h, old="totally-wrong")
    assert r.status_code == 400

    assert (await client.get("/auth/me", headers=h)).status_code == 200, "失败的改密码不该吊销 token"
    # 也确认库里没被改
    async with TestSession() as s:
        u = (await s.execute(select(User).where(User.username == "alice"))).scalar_one()
        assert u.password_changed_at is None


@pytest.mark.asyncio
async def test_same_password_rejected(client):
    """新旧密码相同就拒绝：否则用户以为改了、实际什么都没变，是个安全错觉。"""
    h = await signup(client)
    r = await change_password(client, h, new=OLD_PWD)
    assert r.status_code == 400
    assert "相同" in r.json()["detail"]


@pytest.mark.asyncio
async def test_short_new_password_rejected_by_validation(client):
    """新密码规则与注册一致（min 6），不能出现两套标准。"""
    h = await signup(client)
    r = await change_password(client, h, new="abc")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_change_password_requires_login(client):
    r = await client.post(
        "/auth/password", json={"old_password": OLD_PWD, "new_password": NEW_PWD}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_change_password_is_rate_limited(client, monkeypatch):
    """改密码接口能拿它试原密码，必须限流。"""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW", 60)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH", 2)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    h = await signup(client)
    codes = [
        (await change_password(client, h, old="wrong")).status_code for _ in range(3)
    ]
    assert codes == [400, 400, 429], codes


# ============================================================ 向后兼容


@pytest.mark.asyncio
async def test_tokens_without_pwd_claim_still_work_when_never_changed(client):
    """**上线不能把全站踢下线**：从未改过密码（列为 NULL）时跳过校验，
    所以本次改动之前签发的、不带 pwd 声明的 token 依然有效。
    """
    await signup(client)  # 注册后 password_changed_at 应为 NULL
    async with TestSession() as s:
        u = (await s.execute(select(User).where(User.username == "alice"))).scalar_one()
        assert u.password_changed_at is None

    legacy = create_access_token("alice")  # 不传 password_changed_at -> pwd 声明为 None
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200, "从未改过密码的用户不该被这次上线踢下线"


@pytest.mark.asyncio
async def test_token_issued_before_change_is_rejected_even_if_pwd_claim_present(client):
    """带 pwd 声明但比库里当前值旧 —— 一样要拒。"""
    h = await signup(client)
    stale = create_access_token(
        "alice", password_changed_at=datetime(2020, 1, 1, tzinfo=timezone.utc)
    )
    await change_password(client, h)
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {stale}"})
    assert r.status_code == 401


# ============================================================ 禁用账号（订正 TD-70 的说法）


@pytest.mark.asyncio
async def test_disabled_user_token_is_rejected_immediately(client):
    """订正 TD-70 原文：封号后旧 token **并不是**仍然有效。

    `get_current_user` 每次都查库并检查 `status != 1`，所以禁用立刻生效，
    不依赖 token 过期。
    """
    h = await signup(client)
    assert (await client.get("/auth/me", headers=h)).status_code == 200

    async with TestSession() as s:
        u = (await s.execute(select(User).where(User.username == "alice"))).scalar_one()
        u.status = 0
        await s.commit()

    r = await client.get("/auth/me", headers=h)
    assert r.status_code == 401


# ============================================================ token 声明本身


def test_pwd_claim_is_unix_seconds_not_iso_string():
    """声明里放数字：比 ISO 串小得多，也不受时区/格式歧义影响。"""
    from datetime import datetime, timezone

    from app.security import decode_token

    moment = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    token = create_access_token("carol", password_changed_at=moment)
    claims = decode_token(token)
    assert claims.sub == "carol"
    assert claims.pwd == int(moment.timestamp())
    assert isinstance(claims.pwd, int)


def test_decode_returns_none_for_garbage():
    from app.security import decode_token

    assert decode_token("not-a-jwt") is None
    assert decode_token("") is None


def test_token_without_subject_is_rejected():
    """没有 sub 的 token 不能被当成有效声明（decode 会返回 None 而不是空 claims）。"""
    from jose import jwt

    from app.config import settings
    from app.security import decode_token

    token = jwt.encode({"exp": 9999999999}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    assert decode_token(token) is None
