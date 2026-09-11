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
