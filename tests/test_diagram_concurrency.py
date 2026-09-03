"""TD-65：保存流程图的乐观锁。

原来的 `PUT` 是直接覆盖 `content` 的：开两个标签页（或手机 + 电脑）编辑同一张图，
后保存的会**静默覆盖**先保存的，先改的那份一个字都不留，而且用户完全不知道。
这和 TD-64 修的「删除不可恢复」是同一类数据丢失，只是触发路径不同。

协议用标准的 `ETag` / `If-Match` / `412`：
  GET/POST/PUT 都回 `ETag`（就是 version），保存时用 `If-Match` 带回来。
  缺头 -> 428；版本对不上 -> 412 且一个字都不写。
"""
import asyncio

SAMPLE_XML = "<mxfile><diagram><mxGraphModel><root/></mxGraphModel></diagram></mxfile>"


async def auth(client, username="alice", password="secret123") -> dict:
    await client.post("/auth/register", json={"username": username, "password": password})
    r = await client.post("/auth/login", data={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def create(client, h, name="图", content=SAMPLE_XML):
    r = await client.post("/diagrams", json={"name": name, "content": content}, headers=h)
    assert r.status_code == 201, r.text
    return r


def put(client, h, did, tag, name="改了", content="<mxfile/>"):
    return client.put(f"/diagrams/{did}", json={"name": name, "content": content},
                      headers={**h, "If-Match": tag})


# ---------------------------------------------------------------- 协议本身


async def test_missing_if_match_is_428(client):
    """缺 If-Match 必须**拒绝**，不能默认放行 —— 默认放行等于这个接口仍然会被静默覆盖。"""
    h = await auth(client)
    did = (await create(client, h)).json()["id"]
    r = await client.put(f"/diagrams/{did}", json={"name": "x", "content": "<x/>"}, headers=h)
    assert r.status_code == 428
    assert "If-Match" in r.json()["detail"]


async def test_garbage_if_match_is_400(client):
    h = await auth(client)
    did = (await create(client, h)).json()["id"]
    for bad in ('"abc"', "*", ""):
        r = await put(client, h, did, bad)
        assert r.status_code == 400, f"{bad!r} 应该是 400，实际 {r.status_code}"


async def test_etag_is_returned_and_version_increments(client):
    h = await auth(client)
    made = await create(client, h)
    did = made.json()["id"]
    assert made.headers["etag"] == '"1"', "新建就该把版本给客户端，省一次 GET"
    assert made.json()["version"] == 1

    got = await client.get(f"/diagrams/{did}", headers=h)
    assert got.headers["etag"] == '"1"'

    r = await put(client, h, did, '"1"')
    assert r.status_code == 200
    assert r.headers["etag"] == '"2"', "保存成功后必须把新版本回给客户端"
    assert r.json()["version"] == 2


# ---------------------------------------------------------------- 冲突


async def test_stale_version_is_rejected_and_writes_nothing(client):
    """这是核心：过期版本一律 412，而且**一个字都不能写**。"""
    h = await auth(client)
    did = (await create(client, h)).json()["id"]

    first = await put(client, h, did, '"1"', name="第一次改", content="<a/>")
    assert first.status_code == 200

    # 拿着旧版本 "1" 再存一次 —— 云端已经是 "2" 了
    stale = await put(client, h, did, '"1"', name="想覆盖掉", content="<b/>")
    assert stale.status_code == 412
    detail = stale.json()["detail"]
    assert "2" in detail and "1" in detail, f"错误信息要把两个版本都告诉用户：{detail}"

    after = (await client.get(f"/diagrams/{did}", headers=h)).json()
    assert after["name"] == "第一次改" and after["content"] == "<a/>", "412 之后不能有任何写入"
    assert after["version"] == 2


async def test_concurrent_saves_only_one_wins(client):
    """同一版本同时来 5 个保存请求，**恰好一个**成功。

    这条是「必须用原子 CAS 而不是先读后写」的唯一守卫，实测**在两个后端上都咬得住**：
    把实现换成 `if diagram.version != expected: 412` 再赋值（先读后写），
    SQLite 与真 PostgreSQL 上本用例**都变红**（其余 8 条全绿）—— 即使 SQLite 是
    StaticPool 单连接，5 个请求仍会在「读完版本」与「写回」之间交错。
    （先前这里写的是「SQLite 上杀不掉该变异」，是**没做变异就下的结论**，实测为假。）
    """
    h = await auth(client)
    did = (await create(client, h)).json()["id"]

    results = await asyncio.gather(*[put(client, h, did, '"1"', name=f"第{i}个", content=f"<c{i}/>")
                                     for i in range(5)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1, f"必须只有一个成功，实际 {codes}"
    assert set(codes) - {200} == {412}
    assert (await client.get(f"/diagrams/{did}", headers=h)).json()["version"] == 2


# ---------------------------------------------------------------- 边界


async def test_non_owner_gets_404_not_412(client):
    """版本对不对不该泄露给别人：不是自己的图一律 404。"""
    alice = await auth(client)
    bob = await auth(client, username="bob", password="secret456")
    did = (await create(client, alice)).json()["id"]
    assert (await put(client, bob, did, '"1"')).status_code == 404


async def test_deleted_diagram_is_404_even_with_correct_version(client):
    h = await auth(client)
    did = (await create(client, h)).json()["id"]
    await client.delete(f"/diagrams/{did}", headers=h)
    assert (await put(client, h, did, '"1"')).status_code == 404


async def test_restore_keeps_version_history(client):
    """软删除再恢复不该把版本清零 —— 否则恢复后拿着旧 ETag 的客户端会误判成冲突。"""
    h = await auth(client)
    did = (await create(client, h)).json()["id"]
    await put(client, h, did, '"1"')  # -> v2
    await client.delete(f"/diagrams/{did}", headers=h)
    r = await client.post(f"/diagrams/{did}/restore", headers=h)
    assert r.status_code == 200
    assert r.json()["version"] == 2, "恢复不是一次保存，版本不该变"
    assert (await put(client, h, did, '"2"')).status_code == 200


async def test_list_exposes_version(client):
    """列表也要带 version：前端从列表直接打开某张图时，不该被迫先 GET 一次。"""
    h = await auth(client)
    await create(client, h)
    items = (await client.get("/diagrams", headers=h)).json()
    assert items[0]["version"] == 1
