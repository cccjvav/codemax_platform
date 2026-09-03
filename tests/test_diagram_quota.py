"""TD-64：流程图配额 + 软删除。

两条约束各自对应一个真实后果：
- **没有配额** → 任何人都能无限建图把库刷满（`/diagrams` 是需要登录的，但注册不要钱）。
- **硬删除** → 用户手滑删掉一张画了半天的图，就再也找不回来。

这里刻意不测「配额数字算得准不准」这种同义反复，而是测**行为后果**：
删掉一张要真的腾出名额、恢复不能变成绕过配额的口子、删完必须处处看不见。
"""
import pytest

from app.config import settings

SAMPLE_XML = "<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>"


async def auth(client, username="alice", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def new(client, h, name="图") -> int:
    r = await client.post("/diagrams", json={"name": name, "content": SAMPLE_XML}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- 软删除


async def test_delete_is_recoverable(client):
    """删掉的图能原样恢复 —— 这是「删除不可恢复」的正解。"""
    h = await auth(client)
    did = await new(client, h)

    assert (await client.delete(f"/diagrams/{did}", headers=h)).status_code == 204
    assert (await client.get(f"/diagrams/{did}", headers=h)).status_code == 404

    r = await client.post(f"/diagrams/{did}/restore", headers=h)
    assert r.status_code == 200
    assert r.json()["content"] == SAMPLE_XML, "恢复回来的内容必须原样"
    assert (await client.get(f"/diagrams/{did}", headers=h)).status_code == 200


async def test_deleted_diagram_is_invisible_everywhere(client):
    """软删除最容易出的错就是「某条查询忘了过滤」。逐个入口都验一遍。"""
    h = await auth(client)
    did = await new(client, h)
    await client.delete(f"/diagrams/{did}", headers=h)

    assert (await client.get("/diagrams", headers=h)).json() == [], "列表里不该出现"
    assert (await client.get(f"/diagrams/{did}", headers=h)).status_code == 404
    assert (
        await client.put(f"/diagrams/{did}", json={"name": "改了", "content": "<x/>"}, headers=h)
    ).status_code == 404, "已删除的图不该还能改"
    assert (await client.delete(f"/diagrams/{did}", headers=h)).status_code == 404, "重复删除应是 404"


async def test_trash_listing_is_opt_in(client):
    """回收站要能列出来（不然恢复无从下手），但默认列表不能混进来。"""
    h = await auth(client)
    await new(client, h, name="还在的")
    gone = await new(client, h, name="删掉的")
    await client.delete(f"/diagrams/{gone}", headers=h)

    assert [d["name"] for d in (await client.get("/diagrams", headers=h)).json()] == ["还在的"]
    trash = (await client.get("/diagrams?deleted=true", headers=h)).json()
    assert [d["name"] for d in trash] == ["删掉的"], "回收站只该有删掉的那张"


async def test_restore_of_live_diagram_is_409(client):
    h = await auth(client)
    did = await new(client, h)
    r = await client.post(f"/diagrams/{did}/restore", headers=h)
    assert r.status_code == 409
    assert "没有被删除" in r.json()["detail"]


async def test_others_deleted_diagram_cannot_be_restored(client):
    """恢复也必须限本人，否则就成了「翻别人回收站」的接口。"""
    alice = await auth(client)
    bob = await auth(client, username="bob", password="secret456")
    did = await new(client, alice)
    await client.delete(f"/diagrams/{did}", headers=alice)

    assert (await client.post(f"/diagrams/{did}/restore", headers=bob)).status_code == 404
    assert (await client.get("/diagrams?deleted=true", headers=bob)).json() == []


# ---------------------------------------------------------------- 配额


@pytest.mark.parametrize("quota", [3])
async def test_quota_blocks_the_next_create(client, monkeypatch, quota):
    monkeypatch.setattr(settings, "DIAGRAM_QUOTA", quota)
    h = await auth(client)
    for i in range(quota):
        await new(client, h, name=f"图{i}")

    r = await client.post("/diagrams", json={"name": "超了", "content": SAMPLE_XML}, headers=h)
    assert r.status_code == 409
    assert str(quota) in r.json()["detail"], "错误信息要告诉用户上限是多少"
    assert len((await client.get("/diagrams", headers=h)).json()) == quota


async def test_delete_frees_a_slot(client, monkeypatch):
    """配额只数存活行：删掉一张就该能再建一张。"""
    monkeypatch.setattr(settings, "DIAGRAM_QUOTA", 2)
    h = await auth(client)
    a = await new(client, h, name="a")
    await new(client, h, name="b")
    assert (await client.post("/diagrams", json={"name": "c", "content": SAMPLE_XML},
                             headers=h)).status_code == 409

    await client.delete(f"/diagrams/{a}", headers=h)
    assert (await new(client, h, name="c")) > 0


async def test_restore_cannot_bypass_quota(client, monkeypatch):
    """恢复也要占名额 —— 否则「建满 → 删 → 恢复」就是绕过上限的后门。"""
    monkeypatch.setattr(settings, "DIAGRAM_QUOTA", 2)
    h = await auth(client)
    a = await new(client, h, name="a")
    await new(client, h, name="b")
    await client.delete(f"/diagrams/{a}", headers=h)
    await new(client, h, name="c")  # 存活又满了：b、c

    r = await client.post(f"/diagrams/{a}/restore", headers=h)
    assert r.status_code == 409
    assert (await client.get("/diagrams?deleted=true", headers=h)).json(), "恢复失败不能顺手把它从回收站弄丢"


async def test_quota_is_per_user(client, monkeypatch):
    monkeypatch.setattr(settings, "DIAGRAM_QUOTA", 1)
    alice = await auth(client)
    bob = await auth(client, username="bob", password="secret456")
    await new(client, alice)
    assert (await client.post("/diagrams", json={"name": "a2", "content": SAMPLE_XML},
                             headers=alice)).status_code == 409
    assert (await new(client, bob)) > 0, "别人的配额不该影响我"


async def test_update_is_not_limited_by_quota(client, monkeypatch):
    """配额管的是「存了多少张」，不是「改了多少次」—— 编辑必须始终放行。"""
    monkeypatch.setattr(settings, "DIAGRAM_QUOTA", 1)
    h = await auth(client)
    did = await new(client, h)
    r = await client.put(f"/diagrams/{did}", json={"name": "改个名", "content": "<mxfile/>"}, headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "改个名"
