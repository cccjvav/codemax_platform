"""在 Node 中运行真实 Drawio 脚本，覆盖认证返回与页面加载的两种顺序。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
@pytest.mark.parametrize("source", ["app/frontend/drawio-page.js", "app/static/js/drawio-page.js"])
@pytest.mark.parametrize("auth_ready_first", [True, False])
def test_drawio_receives_initial_and_future_auth_state(source, auth_ready_first):
    script = r"""
const fs = require('fs');
const vm = require('vm');
const nodes = new Map();
const get = id => {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', textContent: '', innerHTML: '', appendChild() {},
    contentWindow: {postMessage() {}}
  });
  return nodes.get(id);
};
const ready = JSON.parse(process.argv[2]);
const calls = [];
let listener;
const auth = {user: ready ? {username:'alice'} : null,
  onChange(fn) {listener = fn;}, open() {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document: {getElementById:get, createElement:() => ({})},
  window: {addEventListener() {}}, CodeMaxAuth:auth,
  fetch: async url => {calls.push(url);return {ok:true, json:async () => []};},
});
(async () => {
  await new Promise(setImmediate);
  const initial = get('auth-status').textContent;
  if (!ready) {auth.user = {username:'alice'};await listener(auth.user);}
  await new Promise(setImmediate);
  const loggedIn = get('auth-status').textContent;
  auth.user = null;
  await listener(null);
  console.log(JSON.stringify({initial, loggedIn, loggedOut:get('auth-status').textContent, calls}));
})().catch(e => {console.error(e);process.exit(1);});
"""
    proc = subprocess.run(
        ["node", "-e", script, str(ROOT / source), json.dumps(auth_ready_first)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    result = json.loads(proc.stdout)
    assert result["initial"] == ("已登录，可保存到云端" if auth_ready_first else "未登录，图只能在本地画")
    assert result["loggedIn"] == "已登录，可保存到云端"
    assert result["loggedOut"] == "未登录，图只能在本地画"
    assert result["calls"] == ["/diagrams"], "认证复用共享模块，不额外请求 /auth/me"


# ---------------------------------------------------------------- TD-272：首屏只建一次编辑器 iframe
#
# 真实浏览器与 Node 桩都实测过：模板里 iframe 的 src 已经开始加载 embed.diagrams.net，
# 随后 syncAuth 的首次调用又走重建分支换掉一个全新的 iframe，登录态异步返回时再换一次 ——
# 访客下载 2 次、已登录 3 次外部编辑器。首次同步只登记身份，账号切换仍必须重建。

_FIRST_LOAD_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
let built = 0;
function newFrame() {
  const frame = { value: '', textContent: '', innerHTML: '', hidden: false,
    contentWindow: { postMessage() {} }, parentNode: { replaceChild(next) { built += 1; } },
    cloneNode() { return newFrame(); } };
  return frame;
}
const nodes = new Map();
function get(id) {
  if (!nodes.has(id)) nodes.set(id, id === 'drawio-frame' ? newFrame() : { value: '', textContent: '',
    innerHTML: '', hidden: false, appendChild() {}, replaceChildren() {}, children: [] });
  return nodes.get(id);
}
let listener; const calls = [];
const auth = { user: JSON.parse(process.argv[2]),
  onChange(fn) { listener = fn; }, open() {}, errorText(d, s) { return String((d && d.detail) || s); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document: { getElementById: get, createElement: () => ({}) },
  window: { addEventListener() {} }, CodeMaxAuth: auth,
  fetch: async (url) => { calls.push(url); return { ok: true, status: 200, json: async () => [] }; },
  setTimeout: () => 1, clearTimeout: () => {},
});
(async () => {
  await new Promise(setImmediate); await new Promise(setImmediate);
  const afterBoot = built;
  auth.user = { username: 'bob' };                        // 换账号：必须重建（账号隔离）
  await listener({ username: 'bob' });
  await new Promise(setImmediate);
  const afterSwitch = built;
  console.log(JSON.stringify({ afterBoot, afterSwitch, calls }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
@pytest.mark.parametrize("source", ["app/frontend/drawio-page.js", "app/static/js/drawio-page.js"])
@pytest.mark.parametrize("user", [None, {"username": "alice"}])
def test_first_sync_does_not_rebuild_the_editor_iframe(source, user):
    """首屏（访客与已登录都一样）**零次**替换 iframe；换账号时才重建。"""
    proc = subprocess.run(["node", "-e", _FIRST_LOAD_HARNESS, str(ROOT / source), json.dumps(user)],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"{source} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["afterBoot"] == 0, (
        f"首屏重建了 {result['afterBoot']} 次 iframe —— 外部编辑器会被重复下载（TD-272）"
    )
    assert result["afterSwitch"] >= 1, "换账号必须重建编辑器，否则上一账号的图会留在新账号名下"
    # 只允许 /diagrams 列表请求：首屏不该为了确认身份额外打 /auth/me（那是 auth.js 的事）
    assert result["calls"] and set(result["calls"]) == {"/diagrams"}, result["calls"]
