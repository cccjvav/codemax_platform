"""R-08：流程图页在丢弃未保存改动之前先确认（Node 真跑 drawio-page.js 源码与构建产物）。

「未保存」指编辑器发过 autosave（外部编辑器只在内容变化时发）之后，既没有保存到云端、也没有下载到本地。
会丢掉这些改动的操作有：从列表打开另一张图、新建、导入、把正在编辑的图移到回收站、关闭或刷新页面。
前四个先 `window.confirm`，取消就什么都不做；关闭页面走浏览器的 beforeunload 提示。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const sent = [], requests = [], events = {}, confirms = [];
let answer = false, fileClicks = 0, failOpen = false;
const make = (tag) => ({ tag, value: '', textContent: '', innerHTML: '', hidden: false, children: [],
  appendChild(child) { this.children.push(child); }, click() {}, focus() {}, classList: { add() {}, remove() {} } });
const nodes = new Map();
function newFrame() {
  const frame = make('iframe');
  frame.contentWindow = { postMessage(text) { sent.push(JSON.parse(text)); } };
  frame.parentNode = { replaceChild(next) { nodes.set('drawio-frame', next); } };
  frame.cloneNode = newFrame;
  return frame;
}
function get(id) {
  if (!nodes.has(id)) {
    const node = id === 'drawio-frame' ? newFrame() : make(id);
    if (id === 'file-input') node.click = () => { fileClicks += 1; };
    nodes.set(id, node);
  }
  return nodes.get(id);
}
const auth = { user: { username: 'alice', role: 0 }, onChange(fn) { this.listener = fn; }, open() {},
  errorText(d, s) { return String((d && d.detail) || s); } };
const reply = (status, data, etag) => ({ ok: status < 400, status, json: async () => data,
  headers: { get: () => etag || null } });
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document: { getElementById: get, createElement: make },
  window: { addEventListener(name, fn) { events[name] = fn; },
            confirm(text) { confirms.push(text); return answer; } },
  CodeMaxAuth: auth,
  fetch: async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push(method + ' ' + url);
    if (url === '/diagrams/1' && method === 'GET') return failOpen
      ? reply(500, { detail: '服务暂时不可用' }) : reply(200, { id: 1, name: '一号图', content: '<mxfile>ONE</mxfile>' }, '"1"');
    if (url.startsWith('/diagrams?deleted')) return reply(200, []);
    if (url === '/diagrams' && method === 'GET') return reply(200, [{ id: 1, name: '一号图', version: 1 }]);
    if (url === '/diagrams' && method === 'POST') return reply(200, { id: 7 }, '"1"');
    if (url === '/diagrams/1' && method === 'DELETE') return reply(204, null);
    return reply(404, { detail: 'unexpected ' + url });
  },
  setTimeout: () => 1, clearTimeout() {}, URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} }, Blob,
  DOMParser: class { parseFromString() { return { documentElement: { nodeName: 'mxfile' }, querySelector() { return null; } }; } },
});
const tick = async () => { for (let i = 0; i < 6; i++) await new Promise(setImmediate); };
const fromEditor = (message) => events.message({ origin: 'https://embed.diagrams.net',
  source: get('drawio-frame').contentWindow, data: JSON.stringify(message) });
const editorReady = () => { fromEditor({ event: 'init' }); fromEditor({ event: 'load' }); };
const edit = (text) => fromEditor({ event: 'autosave', xml: '<mxfile>' + text + '</mxfile>' });
const leaving = () => { let blocked = false; events.beforeunload?.({ preventDefault() { blocked = true; } }); return blocked; };
let answered = 0;  // 只回应新的导出请求：编辑器没就绪时页面不会发请求，保存会如实失败
const answerExport = (text) => { const request = sent.filter((m) => m.action === 'export').at(-1);
  if (!request || request.requestId === answered) return; answered = request.requestId;
  fromEditor({ event: 'export', xml: '<mxfile>' + text + '</mxfile>', message: { requestId: request.requestId } }); };
const status = () => get('drawio-status').textContent;
const out = {};
(async () => {
  await tick(); editorReady();
  get('btn-new').onclick();
  out.cleanNew = { confirms: confirms.length, status: status(), leaving: leaving() };

  editorReady(); edit('WORK'); answer = false;  // 新建会重建编辑器，握手之后的改动才算数
  out.dirtyLeaving = leaving();
  get('diagram-name').value = '草稿'; get('btn-new').onclick();
  out.newCancelled = { confirms: confirms.length, name: get('diagram-name').value, status: status() };
  get('diagram-list').value = '1'; await get('diagram-list').onchange(); await tick();
  out.openCancelled = { list: get('diagram-list').value, fetched: requests.includes('GET /diagrams/1') };
  get('btn-import').onclick();
  out.importCancelled = fileClicks;
  out.confirmText = confirms[0];

  const saving = get('btn-save').onclick(); await tick(); answerExport('WORK'); await saving; await tick();
  out.afterSave = { leaving: leaving(), status: status() };
  const before = confirms.length; get('btn-new').onclick();
  out.newAfterSave = { asked: confirms.length - before, status: status() };

  editorReady(); edit('EDITED_DURING_SAVE_TEST');
  const saving2 = get('btn-save').onclick(); await tick(); answerExport('SNAPSHOT');
  edit('NEWER_THAN_SNAPSHOT'); await saving2; await tick();
  out.editedDuringSave = { leaving: leaving(), status: status() };

  answer = true; failOpen = true; get('diagram-list').value = '1'; await get('diagram-list').onchange(); await tick();
  fromEditor({ event: 'init' });  // 重建后的编辑器握手，页面把当前 xml 重新载入
  out.openFailed = { leaving: leaving(), reloaded: sent.filter((m) => m.action === 'load').at(-1).xml, status: status() };
  failOpen = false; get('diagram-list').value = '1'; await get('diagram-list').onchange(); await tick();
  out.opened = { leaving: leaving(), status: status() };

  editorReady(); edit('CHANGES_TO_ONE'); answer = false;
  await get('btn-manage').onclick(); await tick();
  const row = get('diagram-manage').children[0];
  await row.children[0].onclick(); await tick();
  out.trashCancelled = { deleted: requests.includes('DELETE /diagrams/1'), leaving: leaving() };
  answer = true; await row.children[0].onclick(); await tick();
  out.trashed = { deleted: requests.includes('DELETE /diagrams/1'), leaving: leaving(), name: get('diagram-name').value };

  editorReady(); edit('BEFORE_DOWNLOAD'); out.beforeDownload = leaving();
  const downloading = get('btn-download').onclick(); await tick(); answerExport('BEFORE_DOWNLOAD'); await downloading;
  out.afterDownload = leaving();

  editorReady(); edit('ALICE_UNSAVED');
  const asked = confirms.length; out.beforeSwitch = leaving();
  await auth.listener(null); await auth.listener({ username: 'bob', role: 0 }); await tick();
  out.accountSwitch = { asked: confirms.length - asked, leaving: leaving() };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
@pytest.mark.parametrize("source", ["app/frontend/drawio-page.js", "app/static/js/drawio-page.js"])
def test_unsaved_changes_are_confirmed_before_they_are_discarded(source):
    proc = subprocess.run(["node", "-e", _HARNESS, str(ROOT / source)], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, f"{source} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    # 没改过就不打扰：新建不询问，离开页面也不拦。
    assert out["cleanNew"] == {"confirms": 0, "status": "已新建空白流程图", "leaving": False}
    # 改过之后，四个会丢改动的操作都先确认；取消就原样保留。
    assert out["dirtyLeaving"] is True
    assert out["newCancelled"] == {"confirms": 1, "name": "草稿", "status": "已新建空白流程图"}, "取消后不该清空名称"
    assert out["openCancelled"] == {"list": "", "fetched": False}, "取消打开：不发请求，列表回到当前的图"
    assert out["importCancelled"] == 0, "取消导入：不弹出文件选择"
    assert out["confirmText"] == "当前流程图有未保存的改动，继续会丢弃这些改动。确定吗？"
    # 保存成功就不再算未保存。
    assert out["afterSave"] == {"leaving": False, "status": "已保存到云端（#7）"}
    assert out["newAfterSave"] == {"asked": 0, "status": "已新建空白流程图"}
    # 保存期间又改过：仍算未保存（与状态栏「编辑器还有新改动」一致）。
    assert out["editedDuringSave"]["leaving"] is True
    assert "编辑器还有新改动" in out["editedDuringSave"]["status"]
    # 打开失败会把编辑器重载成原来的内容，改动还在，所以仍算未保存。
    assert out["openFailed"]["leaving"] is True
    assert out["openFailed"]["reloaded"] == "<mxfile>NEWER_THAN_SNAPSHOT</mxfile>", "打开失败后编辑器应重载成含改动的内容"
    assert "服务暂时不可用" in out["openFailed"]["status"]
    assert out["opened"] == {"leaving": False, "status": "已打开 #1"}
    # 把正在编辑的图移到回收站同样会清空编辑器。
    assert out["trashCancelled"] == {"deleted": False, "leaving": True}
    assert out["trashed"] == {"deleted": True, "leaving": False, "name": ""}
    # 下载到本地副本后不再拦。
    assert out["beforeDownload"] is True and out["afterDownload"] is False
    # 换账号无法取消，不询问；上一个账号的改动按账号隔离清掉，也就不再拦离开。
    assert out["beforeSwitch"] is True
    assert out["accountSwitch"] == {"asked": 0, "leaving": False}
