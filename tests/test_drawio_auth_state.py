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
  const promptInitial = get('drawio-login-prompt').hidden;
  if (!ready) {auth.user = {username:'alice'};await listener(auth.user);}
  await new Promise(setImmediate);
  const loggedIn = get('auth-status').textContent;
  const promptLoggedIn = get('drawio-login-prompt').hidden;
  auth.user = null;
  await listener(null);
  console.log(JSON.stringify({initial, loggedIn, loggedOut:get('auth-status').textContent, calls,
    promptInitial, promptLoggedIn, promptLoggedOut:get('drawio-login-prompt').hidden}));
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
    # TD-278：已登录时「云端保存需登录：[登录 / 注册]」整段隐藏 —— 此前它与「已登录，可保存到云端」
    # 同时显示、互相矛盾；退出后必须重新出现，否则访客找不到登录入口。
    assert result["promptInitial"] is auth_ready_first
    assert result["promptLoggedIn"] is True
    assert result["promptLoggedOut"] is False


# ---------------------------------------------------------------- TD-272：首屏只建一次编辑器 iframe
#
# 真实浏览器与 Node 桩都实测过：模板里 iframe 的 src 已经开始加载 embed.diagrams.net，
# 随后 syncAuth 的首次调用又走重建分支换掉一个全新的 iframe，登录态异步返回时再换一次 ——
# 访客下载 2 次、已登录 3 次外部编辑器。首次同步只登记身份，账号切换仍必须重建。

_FIRST_LOAD_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
const cfg = JSON.parse(process.argv[2]);      // {user: null|{...}, settled: bool, lateUser: null|{...}}
let built = 0;
function newFrame() {
  const frame = { value: '', textContent: '', innerHTML: '', hidden: false,
    contentWindow: { postMessage() {} }, parentNode: { replaceChild() { built += 1; } },
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
const auth = { user: cfg.user, settled: cfg.settled,
  onChange(fn) { listener = fn; }, open() {}, errorText(d, s) { return String((d && d.detail) || s); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document: { getElementById: get, createElement: () => ({}) },
  window: { addEventListener() {} }, CodeMaxAuth: auth,
  fetch: async (url) => { calls.push(url); return { ok: true, status: 200, json: async () => [] }; },
  setTimeout: () => 1, clearTimeout: () => {}, Promise, JSON, String, Array, Object, Error, Date, Math,
});
(async () => {
  await new Promise(setImmediate); await new Promise(setImmediate);
  const afterBoot = built, callsAfterBoot = calls.length;
  if (cfg.lateUser) {                        // 首次 /auth/me 迟到：身份随后到达
    auth.user = cfg.lateUser; auth.settled = true;
    await listener(cfg.lateUser); await new Promise(setImmediate);
  }
  const afterLateIdentity = built;
  auth.user = { username: 'someone-else', role: 0 };   // 之后的换账号仍必须重建
  await listener(auth.user); await new Promise(setImmediate);
  console.log(JSON.stringify({ afterBoot, callsAfterBoot, afterLateIdentity, afterSwitch: built, calls }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
@pytest.mark.parametrize("source", ["app/frontend/drawio-page.js", "app/static/js/drawio-page.js"])
@pytest.mark.parametrize("case", [
    {"user": None, "settled": True, "lateUser": None},                     # 确定是访客
    {"user": {"username": "alice"}, "settled": True, "lateUser": None},    # 启动时已登录
    {"user": None, "settled": False, "lateUser": {"username": "alice"}},   # /auth/me 迟到
])
def test_first_sync_does_not_rebuild_the_editor_iframe(source, case):
    """首屏**零次**替换 iframe（访客、已登录、登录态迟到三种都一样）；之后的换账号必须重建。

    真实浏览器实测：改前每次加载都会重建 1～2 次，外部编辑器被下载 2～3 次；
    登录态迟到那一种尤其常见（页面先渲染成访客，/auth/me 回来后才变成已登录）。
    """
    proc = subprocess.run(["node", "-e", _FIRST_LOAD_HARNESS, str(ROOT / source), json.dumps(case)],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"{source} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["afterBoot"] == 0, f"首屏重建了 {result['afterBoot']} 次 iframe（TD-272）"
    assert result["afterLateIdentity"] == 0, "登录态迟到时重建了编辑器 —— 用户会看到空白图"
    assert result["afterSwitch"] >= 1, "换账号必须重建编辑器，否则上一账号的图会留在新账号名下"
    # 只允许 /diagrams 列表请求：首屏不该为了确认身份额外打 /auth/me（那是 auth.js 的事）
    assert set(result["calls"]) == {"/diagrams"}, result["calls"]


# ---------------------------------------------------------------- TD-273：会话到期不许丢掉正在编辑的内容
#
# 2026-09-23 复核的 N-01（P2 回归）：TD-270 给所有 401 加了统一的登出通知，页面把「到期」
# 当成了「主动退出」—— Drawio 重建编辑器加载空白图、客服清空未发送的留言。提示变好了，
# 用户的工作却没了。修复方式是把到期作为第二个参数传给订阅者（`reason === "expired"`），
# 页面据此只提示、不清理；换账号仍走完整重置，账号隔离不退化。

_SESSION_EXPIRY_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
let built = 0, opened = 0;
const sent = [];
function newFrame() {
  const frame = { value: '', textContent: '', innerHTML: '', hidden: false, children: [],
    contentWindow: { postMessage(text) { sent.push(JSON.parse(text)); } },
    parentNode: { replaceChild() { built += 1; } }, cloneNode() { return newFrame(); },
    appendChild() {}, replaceChildren() {} };
  return frame;
}
const nodes = new Map();
function get(id) {
  if (!nodes.has(id)) nodes.set(id, id === 'drawio-frame' ? newFrame() : { id, value: '', textContent: '',
    innerHTML: '', hidden: false, children: [], appendChild() {}, replaceChildren() {},
    classList: { add() {}, remove() {} }, focus() {} });
  return nodes.get(id);
}
let listener = null, messageHandler = null;
const auth = { user: { username: 'alice', role: 0 },
  onChange(fn) { listener = fn; },
  open() { opened += 1; },
  errorText(d, s) { return String((d && d.detail) || s); },
  sessionExpired(status) {
    if (status !== 401 || !this.user) return false;
    this.user = null; listener(null, 'expired'); this.open(); return true;
  } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document: { getElementById: get, createElement: () => ({}) },
  window: { addEventListener(name, fn) { if (name === 'message') messageHandler = fn; } },
  CodeMaxAuth: auth,
  fetch: async (url, options = {}) => (options.method === 'POST' || options.method === 'PUT')
    ? { ok: false, status: 401, json: async () => ({ detail: '未登录' }), headers: { get: () => null } }
    : { ok: true, status: 200, json: async () => [] },
  setTimeout: () => 1, clearTimeout: () => {}, JSON, Promise, Error, String, Array, Object, Math, Date,
  DOMParser: class { parseFromString() { return { documentElement: { nodeName: 'mxfile' }, querySelector() { return null } } } },
});
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const fromEditor = (message, source) => messageHandler({ origin: 'https://embed.diagrams.net',
  source: source || get('drawio-frame').contentWindow, data: JSON.stringify(message) });
(async () => {
  await tick();
  get('diagram-name').value = '我的图';
  fromEditor({ event: 'init' });            // 编辑器就绪握手
  fromEditor({ event: 'load' });            // ready = true
  fromEditor({ event: 'autosave', xml: '<mxfile>ALICE_WORK_30_MINUTES</mxfile>' });
  const saving = get('btn-save').onclick(); // 保存：先向编辑器要 export
  await tick();
  const request = sent.filter((m) => m.action === 'export').at(-1);
  fromEditor({ event: 'export', xml: '<mxfile>ALICE_WORK_30_MINUTES</mxfile>', message: { requestId: request.requestId } });
  await saving;
  await tick();
  const statusAfter401 = get('drawio-status').textContent;
  const builtAfterExpiry = built;
  auth.user = { username: 'alice', role: 0 };
  await listener(auth.user, 'sync');        // 同一账号重新登录
  await tick();
  const builtAfterSameUser = built;
  auth.user = { username: 'bob', role: 0 };
  await listener(auth.user, 'sync');        // 换账号
  await tick();
  console.log(JSON.stringify({ statusAfter401, builtAfterExpiry, builtAfterSameUser, builtAfterSwitch: built, opened }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="未安装 node")
@pytest.mark.parametrize("source", ["app/frontend/drawio-page.js", "app/static/js/drawio-page.js"])
def test_session_expiry_keeps_the_diagram_and_still_isolates_accounts(source):
    """到期（401）不重建编辑器、不清身份；重新登录同一账号不重建；换账号仍必须重建。"""
    proc = subprocess.run(["node", "-e", _SESSION_EXPIRY_HARNESS, str(ROOT / source)],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"{source} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["builtAfterExpiry"] == 0, "会话到期时重建了编辑器 —— 用户没保存的图会被丢掉（N-01）"
    assert result["opened"] >= 1, "到期必须弹出登录浮层，不能静默"
    assert "登录已过期" in result["statusAfter401"], result["statusAfter401"]
    assert result["builtAfterSameUser"] == 0, "同一账号重新登录不该重建（重建就是重新加载空白图）"
    assert result["builtAfterSwitch"] >= 1, "换账号必须重建，账号隔离不能退化"
