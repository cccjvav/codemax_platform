# tests/test_proxy_headers.py
#
# 反向代理之后，应用看到的 scheme 是 http（TLS 在 nginx / 云 LB 那层终止）。
# 全站要靠 `X-Forwarded-Proto` 才知道外面其实是 https —— 而这件事**只在
# `TRUST_PROXY_HEADERS=true` 时可信**，否则伪造一个头就能把站点锁死一年（TD-142）。
#
# 这条纪律 HSTS 早就守住了（`app/middleware.py`），但**预签名下载链接没有**：
# 它用的是 `request.base_url`，而 Starlette 的 base_url 直接读 `scope["scheme"]`，
# 不看转发头。实测带 `X-Forwarded-Proto: https` 请求 `/shop/download/{no}`，
# 拿回来的仍然是 `http://test/shop/dl?...`。
#
# 后果很具体：浏览器在 https 页面上拿到一个 http 的下载链接，按**混合内容**
# 直接拦掉 —— 用户付了钱点下载没反应，而且控制台那行报错没人会去关联到代理配置。
"""A-9：预签名链接必须与 HSTS 用同一套转发头信任规则。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

from tests.test_download import auth_headers, make_order  # noqa: E402

_FWD = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "shop.example.com"}


async def _download_url(client, headers: dict, username: str) -> str:
    # ⚠️ make_order 默认给 "buyer" 建单，必须与登录用户对齐 ——
    #    否则 /shop/download 会因为「不是自己的单」返回 404（刻意模糊存在性）。
    no = await make_order("paid", username)
    r = await client.post(f"/shop/download/{no}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["download_url"]


async def test_presigned_url_stays_http_when_proxy_headers_are_not_trusted(client, product):
    """默认（`TRUST_PROXY_HEADERS=false`）必须**无视**转发头。

    这是安全侧：谁都能发一个 `X-Forwarded-Proto: https`。若无条件相信，
    攻击者就能让服务端签发指向任意 host 的下载链接（`X-Forwarded-Host`
    一并被采信），把用户引到自己的域名上。
    """
    h = await auth_headers(client, "proxy_off")
    url = await _download_url(client, {**h, **_FWD}, "proxy_off")
    assert url.startswith("http://test/"), (
        f"没开 TRUST_PROXY_HEADERS 却采信了转发头：{url[:100]}"
    )
    assert "shop.example.com" not in url, "X-Forwarded-Host 不该被采信"


async def test_presigned_url_becomes_https_behind_a_trusted_proxy(
    client, product, monkeypatch
):
    """开了 `TRUST_PROXY_HEADERS` 就必须采信，链接得是 https。

    这是功能侧：真上了反向代理之后，不采信就意味着**每一个**下载链接都是
    http 的，浏览器按混合内容全部拦掉。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)

    h = await auth_headers(client, "proxy_on")
    url = await _download_url(client, {**h, **_FWD}, "proxy_on")

    assert url.startswith("https://"), f"链接仍是 http，浏览器会按混合内容拦掉：{url[:100]}"
    assert "shop.example.com" in url, f"X-Forwarded-Host 没被采信：{url[:100]}"
    # 签名必须仍然有效：换了 scheme/host 不能把签名算错
    assert "signature=" in url and "expires=" in url


async def test_mock_pay_link_uses_the_same_rule(client, mock_mode, monkeypatch):
    """模拟收银台的链接走的是同一处 base_url，必须一起修。

    只修 `_storage()` 而漏掉 `create_order` 里那一处，是最容易发生的半修 ——
    两者都在 `shop.py`，都用 `str(request.base_url)`。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)

    h = await auth_headers(client, "proxy_mock")
    r = await client.post("/shop/orders", headers={**h, **_FWD})
    assert r.status_code == 200, r.text
    code_url = r.json()["code_url"]
    assert code_url.startswith("https://"), f"模拟收银台链接仍是 http：{code_url[:100]}"


async def test_hsts_and_presigned_url_agree(client, product, monkeypatch):
    """**两处必须给出同一个答案**，否则会出现自相矛盾的响应。

    这条是这一组里最重要的一条：HSTS 告诉浏览器「本站只有 https」，
    同时下载链接给的是 http —— 浏览器会直接拒绝，且现象极难排查
    （HSTS 生效是异步的，本地复现不出来）。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "HSTS_MAX_AGE", 31536000)

    h = await auth_headers(client, "proxy_agree")
    url = await _download_url(client, {**h, **_FWD}, "proxy_agree")

    probe = await client.get("/healthz", headers=_FWD)
    hsts = probe.headers.get("strict-transport-security")

    if hsts:  # 下发了 HSTS ⇒ 认定是 https ⇒ 链接也必须是 https
        assert url.startswith("https://"), (
            f"响应里带了 HSTS（{hsts}）却签发 http 链接：{url[:100]}"
        )
