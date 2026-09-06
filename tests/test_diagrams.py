"""S2-01-3 测试：Drawio 流程图存取 —— 需鉴权，且只能操作自己的记录。"""
from main import app
from tests.conftest import iter_app_routes

SAMPLE_XML = "<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>"


async def auth_headers(client, username="alice", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def create(client, headers, name="登录流程", content=SAMPLE_XML):
    return await client.post("/diagrams", json={"name": name, "content": content}, headers=headers)


async def test_all_diagram_endpoints_require_login(client):
    payload = {"name": "a", "content": SAMPLE_XML}
    assert (await client.get("/diagrams")).status_code == 401
    assert (await client.post("/diagrams", json=payload)).status_code == 401
    assert (await client.get("/diagrams/1")).status_code == 401
    assert (await client.put("/diagrams/1", json=payload)).status_code == 401
    assert (await client.delete("/diagrams/1")).status_code == 401
    assert (await client.post("/diagrams/1/restore")).status_code == 401


async def test_create_then_get(client):
    h = await auth_headers(client)
    r = await create(client, h)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "登录流程"
    assert body["content"] == SAMPLE_XML
    assert body["id"] > 0
    assert body["update_time"]

    got = await client.get(f"/diagrams/{body['id']}", headers=h)
    assert got.status_code == 200
    assert got.json()["content"] == SAMPLE_XML


async def test_list_returns_only_own_and_skips_content(client):
    alice = await auth_headers(client)
    bob = await auth_headers(client, username="bob", password="secret456")
    await create(client, alice, name="alice 的图")
    await create(client, bob, name="bob 的图")

    mine = (await client.get("/diagrams", headers=bob)).json()
    assert [d["name"] for d in mine] == ["bob 的图"]
    assert "content" not in mine[0], "列表不该带上大字段 content"


async def test_update_replaces_name_and_content(client):
    h = await auth_headers(client)
    did = (await create(client, h)).json()["id"]
    r = await client.put(f"/diagrams/{did}", json={"name": "改名了", "content": "<mxfile/>"},
                         headers={**h, "If-Match": '"1"'})
    assert r.status_code == 200
    assert r.json()["id"] == did
    assert r.json()["name"] == "改名了"
    assert r.json()["content"] == "<mxfile/>"


async def test_delete_then_gone(client):
    h = await auth_headers(client)
    did = (await create(client, h)).json()["id"]
    assert (await client.delete(f"/diagrams/{did}", headers=h)).status_code == 204
    assert (await client.get(f"/diagrams/{did}", headers=h)).status_code == 404
    assert (await client.get("/diagrams", headers=h)).json() == []


async def test_others_diagram_is_invisible(client):
    """不是自己的图一律 404（不泄露是否存在），且不会被改动/删除。"""
    alice = await auth_headers(client)
    bob = await auth_headers(client, username="bob", password="secret456")
    did = (await create(client, alice)).json()["id"]

    assert (await client.get(f"/diagrams/{did}", headers=bob)).status_code == 404
    assert (
        await client.put(f"/diagrams/{did}", json={"name": "抢过来的", "content": "<x/>"},
                         headers={**bob, "If-Match": '"1"'})
    ).status_code == 404
    assert (await client.delete(f"/diagrams/{did}", headers=bob)).status_code == 404

    still = await client.get(f"/diagrams/{did}", headers=alice)
    assert still.status_code == 200
    assert still.json()["name"] == "登录流程"


async def test_payload_validation(client):
    h = await auth_headers(client)
    assert (await create(client, h, name="")).status_code == 422
    assert (await create(client, h, content="")).status_code == 422


async def test_drawio_page_is_wired_to_the_api(client):
    r = await client.get("/tools/drawio")
    assert r.status_code == 200
    assert "embed.diagrams.net" in r.text, "页面必须 iframe 嵌入 Drawio"
    assert 'id="drawio-frame"' in r.text

    registered = {getattr(route, "path", None) for route in iter_app_routes(app.routes)}

    # /diagrams 仍由 drawio 页自己的脚本调用
    assert "/diagrams" in r.text, "页面没有调用 /diagrams"
    assert "/diagrams" in registered, "/diagrams 不是已注册路由"

    # 登录调用在 S2-02-2 之后搬进了全站共享模块 /static/auth.js ——
    # base.html 用它渲染统一的登录/注册浮层，drawio 页不再自带登录表单
    # （原先是页面上常驻的用户名/密码输入框）。所以要连那个外部脚本一起看，
    # 才算真的"接上了"；只查 HTML 会漏掉这条接线。
    assert "/static/auth.js" in r.text, "页面没引用全站登录模块"
    auth_js = (await client.get("/static/auth.js")).text
    assert "/auth/login" in auth_js, "共享登录模块没有调用 /auth/login"
    assert "/auth/login" in registered, "/auth/login 不是已注册路由"
