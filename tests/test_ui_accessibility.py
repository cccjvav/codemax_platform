"""TD-263 / ROADMAP A-09：颜色对比度、登录浮层可访问性与 `:has()` 回退。

三类检查，都是静态或 Node 执行，不是真实浏览器渲染或读屏实测：
1. **对比度**：把模板/样式里出现的前景色按 WCAG 相对亮度公式与白底算比值，钉住 ≥ 4.5:1。
   计算与 WebAIM 的对照器一致（#2563eb 5.17、#15803d 5.02、#64748b 4.76）。
2. **浮层语义与键盘行为**：`role="dialog"`/`aria-modal`/`aria-labelledby` 出现在 base.html；
   用 Node 真跑源码 `auth.js` 与构建产物 `static/js/auth.js`：Esc 关闭、关闭后焦点回到打开它的按钮。
3. **`:has()` 回退**：`support.css` 不再含 `:has(`，两栏由 `support-page.js` 切 `with-inbox` class。
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "app" / "templates" / "base.html"
SHOP = ROOT / "app" / "templates" / "shop.html"
SUPPORT_CSS = ROOT / "app" / "static" / "support.css"


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str = "#ffffff") -> float:
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_formula_matches_reference_values():
    """先证明公式本身没写错：白底黑字 21:1，旧蓝 #3b82f6 3.68:1（复核报告的数字）。"""
    assert round(contrast("#000000"), 2) == 21.0
    assert round(contrast("#3b82f6"), 2) == 3.68
    assert round(contrast("#2563eb"), 2) == 5.17


@pytest.mark.parametrize(("selector", "color", "file"), [
    ("a", "#2563eb", BASE),                 # 链接
    ("button", "#2563eb", BASE),            # 按钮底色（白字反过来算同一个比值）
    ("button.ghost", "#2563eb", BASE),      # 幽灵按钮文字
    ("button.cta", "#15803d", BASE),        # CTA 底色
    (".modal .hint", "#64748b", BASE),      # 浮层提示
    (".price", "#15803d", SHOP),            # 商品价格
])
def test_text_colors_meet_wcag_aa(selector, color, file):
    css = file.read_text(encoding="utf-8")
    rule = re.search(re.escape(selector) + r"\s*\{[^}]*\}", css)
    assert rule and color in rule.group(0), f"{file.name} 里 `{selector}` 的颜色应为 {color}（实际规则：{rule.group(0) if rule else None}）"
    assert contrast(color) >= 4.5, f"{selector} {color} 对白底仅 {contrast(color):.2f}:1"


def test_old_low_contrast_colors_are_gone_from_templates_and_css():
    """#3b82f6 / #16a34a / #94a3b8 分别只有 3.68 / 3.30 / 2.56:1，不得再用于文字或按钮。"""
    offenders = []
    for f in [*sorted((ROOT / "app" / "templates").glob("*.html")), SUPPORT_CSS]:
        for m in re.finditer(r"#(?:3b82f6|16a34a|94a3b8)\b", f.read_text(encoding="utf-8"), flags=re.I):
            offenders.append(f"{f.name}:{m.group(0)}")
    assert offenders == [], offenders


def test_er_table_header_text_background_meets_aa():
    """ER 图表头是白色 14px 文字盖在填充色上：填充必须 ≥ 4.5:1（TD-270 把 #3b82f6 换成 #2563eb）。
    连线/边框描边是图形而非文字，不在此检查。"""
    js = (ROOT / "app" / "frontend" / "er-page.js").read_text(encoding="utf-8")
    header = re.search(r'\.attr\("height", L\.headH\)[\s\S]*?\.attr\("fill", "(#[0-9a-fA-F]{6})"\)', js)
    assert header, "找不到表头矩形的 fill"
    assert contrast("#ffffff", header.group(1)) >= 4.5, f"表头 {header.group(1)} 对白字仅 {contrast('#ffffff', header.group(1)):.2f}:1"


def test_disabled_and_focus_visible_states_are_styled():
    css = BASE.read_text(encoding="utf-8")
    disabled = re.search(r"button:disabled\s*\{([^}]*)\}", css)
    assert disabled and "cursor: not-allowed" in disabled.group(1) and "background" in disabled.group(1), "禁用按钮需要可见的灰化与 not-allowed 光标"
    assert re.search(r"button:focus-visible[^{]*\{[^}]*outline:\s*2px", css), "键盘焦点必须有可见轮廓（:focus-visible）"


def test_login_dialog_has_aria_semantics():
    html = BASE.read_text(encoding="utf-8")
    modal = re.search(r'<div class="modal"([^>]*)>', html)
    assert modal, "找不到浮层容器"
    attrs = modal.group(1)
    assert 'role="dialog"' in attrs and 'aria-modal="true"' in attrs and 'aria-labelledby="auth-title"' in attrs, attrs
    assert 'id="auth-title"' in html, "aria-labelledby 指向的标题必须存在"


def test_support_layout_does_not_depend_on_css_has():
    css = re.sub(r"/\*.*?\*/", "", SUPPORT_CSS.read_text(encoding="utf-8"), flags=re.S)  # 注释里可以提到它
    assert ":has(" not in css, "support.css 不得依赖 :has()（旧内核不支持，管理员会只看到单栏）"
    assert ".support-layout.with-inbox" in css
    js = (ROOT / "app" / "frontend" / "support-page.js").read_text(encoding="utf-8")
    assert 'add("with-inbox")' in js and 'remove("with-inbox")' in js, "两栏切换应由脚本按角色切 class"


# ---------------------------------------------------------------- Node 真跑 auth.js：Esc 关闭 + 焦点归还

_HARNESS = r"""
const path = process.argv[2];
const els = {};
const log = [];
let active = null;
const mkEl = (id) => {
  const el = {
    id, value: "", textContent: "", hidden: false, disabled: false, onclick: null, onkeydown: null, onsubmit: null,
    classes: new Set(), children: new Set(),
    classList: { add(c) { el.classes.add(c); }, remove(c) { el.classes.delete(c); }, contains(c) { return el.classes.has(c); } },
    focus() { active = el; log.push("focus:" + id); },
    contains(other) { return el.children.has(other); },
    addEventListener() {}, appendChild() {}, click() {},
  };
  return el;
};
const body = mkEl("body");
global.document = {
  getElementById: (id) => (els[id] ||= mkEl(id)),
  createElement: (t) => mkEl(t),
  get activeElement() { return active; },
  body,
};
global.window = global;
global.addEventListener = () => {};
global.fetch = async () => ({ ok: false, status: 401, json: async () => ({}) });
require(path);
(async () => {
  await new Promise((r) => setImmediate(r));
  // 一律经 getElementById 取：auth.js 只在 open() 时才懒取输入框，直接读 els 会拿到 undefined
  const byId = (id) => global.document.getElementById(id);
  const mask = byId("auth-mask"), user = byId("auth-user"), btn = byId("btn-auth");
  mask.children.add(user);           // 用户名输入框在浮层里
  active = btn;                      // 用户用键盘把焦点放在「登录 / 注册」按钮上
  btn.onclick();                     // 打开浮层
  const openedWithFocusInside = mask.classes.has("open") && active === user;
  mask.onkeydown({ key: "a", stopPropagation() {} });
  const otherKeyIgnored = mask.classes.has("open");
  mask.onkeydown({ key: "Escape", stopPropagation() {} });
  const closedByEsc = !mask.classes.has("open");
  const focusReturned = active === btn;
  // 第二次：用户在浮层打开期间点到了页面别处（焦点已不在浮层内），关闭时不要抢焦点
  const elsewhere = mkEl("elsewhere");
  active = btn; btn.onclick(); active = elsewhere; byId("auth-cancel").onclick();
  const focusLeftAlone = active === elsewhere;
  console.log(JSON.stringify({ openedWithFocusInside, otherKeyIgnored, closedByEsc, focusReturned, focusLeftAlone, log }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
@pytest.mark.parametrize("path", ["app/frontend/auth.js", "app/static/js/auth.js"])
def test_login_dialog_closes_on_escape_and_returns_focus(tmp_path, path):
    """源码与构建产物都跑：产物漂移或 vite 改写都会在这里露出来。"""
    harness = tmp_path / "harness.cjs"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"{path} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    import json

    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["openedWithFocusInside"], result
    assert result["otherKeyIgnored"], "非 Esc 按键不能关闭浮层"
    assert result["closedByEsc"], "Esc 必须关闭浮层"
    assert result["focusReturned"], f"关闭后焦点应回到打开它的按钮：{result['log']}"
    assert result["focusLeftAlone"], "焦点已离开浮层时关闭不得抢焦点"


# ---------------------------------------------------------------- 会话到期（TD-270）：401 → 清用户、弹浮层、说明原因

_EXPIRY_HARNESS = r"""
const [authPath, supportPath] = [process.argv[2], process.argv[3]];
const els = {}; const fetches = []; let meStatus = 200;
const mkEl = (id) => { const classes = new Set(); return { id, value: "", textContent: "", hidden: false, disabled: false, classes,
  classList: { add(c) { classes.add(c); }, remove(c) { classes.delete(c); }, contains(c) { return classes.has(c); } },
  focus() {}, contains() { return false; }, append() {}, appendChild() {}, prepend() {}, replaceChildren() {}, addEventListener() {}, click() {} }; };
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)), createElement: (t) => mkEl(t), body: mkEl("body"), activeElement: null,
  addEventListener() {} };
global.window = global; global.addEventListener = () => {}; global.setTimeout = (fn) => 1; global.clearTimeout = () => {};
global.AbortController = class { constructor() { this.signal = {}; } abort() {} };
global.fetch = async (url, opts = {}) => {
  fetches.push(url);
  if (url === "/auth/me") return { ok: meStatus === 200, status: meStatus, json: async () => ({ username: "alice", role: 0 }) };
  return { ok: false, status: 401, json: async () => ({ detail: "未登录" }) };  // session expired for every data call
};
require(authPath);
(async () => {
  await new Promise((r) => setImmediate(r));
  const auth = global.CodeMaxAuth;
  const before = { user: auth.user && auth.user.username, btnAuthHidden: els["btn-auth"].hidden, whoHidden: els["auth-who"].hidden };
  require(supportPath);           // page boots with a logged-in user, then its first poll gets 401
  // 用户已经写了一半的草稿：到期时**必须留住**（TD-273 / 复核 N-01）。清空它等于
  // 在提示「请重新登录后继续」的同一秒里把用户写的东西删掉。
  els["support-body"].value = "写了一半的留言";
  await new Promise((r) => setImmediate(r)); await new Promise((r) => setImmediate(r));
  const after = { user: auth.user, btnAuthHidden: els["btn-auth"].hidden, whoHidden: els["auth-who"].hidden,
                  modalOpen: els["auth-mask"].classes.has("open"), err: els["auth-error"].textContent,
                  draft: els["support-body"].value,
                  workspaceHidden: els["support-workspace"].hidden, loginHidden: els["support-login"].hidden };
  console.log(JSON.stringify({ before, after, fetches }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
def test_expired_session_reopens_login_and_clears_the_user_everywhere(tmp_path):
    """会话在页面打开期间到期：数据接口回 401 时，共享模块清掉用户快照、顶栏切回「登录 / 注册」、
    弹出浮层并说明"登录已过期"；订阅页（客服）随之隐藏工作区、显示登录提示。改前顶栏仍显示已登录，
    页面只报"读取失败"。"""
    harness = tmp_path / "expiry.cjs"
    harness.write_text(_EXPIRY_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / "app/frontend/auth.js"), str(ROOT / "app/frontend/support-page.js")],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"执行失败：\n{proc.stdout}\n{proc.stderr}"
    import json

    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["before"] == {"user": "alice", "btnAuthHidden": True, "whoHidden": False}, result
    after = result["after"]
    assert after["user"] is None and after["btnAuthHidden"] is False and after["whoHidden"] is True, after
    assert after["modalOpen"] is True and "登录已过期" in after["err"], after
    assert after["workspaceHidden"] is True and after["loginHidden"] is False, after
    assert after["draft"] == "写了一半的留言", (
        "会话到期把未发送的留言清空了（N-01）—— 到期是凭证过期，不是用户放弃草稿"
    )
    assert "/support/messages" in result["fetches"]


# ---------------------------------------------------------------- TD-315：网络层失败显示中文

_NETWORK_HARNESS = r"""
const [authPath, supportPath] = [process.argv[2], process.argv[3]];
const els = {}; let down = false;
const mkEl = (id) => ({ id, value: "", textContent: "", hidden: false, disabled: false, children: [],
  classList: { add() {}, remove() {}, contains() { return false; } }, setAttribute() {}, removeAttribute() {},
  focus() {}, contains() { return false; }, append() {}, appendChild() {}, prepend() {}, replaceChildren() {},
  addEventListener() {}, click() {}, scrollIntoView() {} });
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)), createElement: (t) => mkEl(t), body: mkEl("body"),
  activeElement: null, addEventListener() {} };
global.window = global; global.addEventListener = () => {}; global.setTimeout = () => 1; global.clearTimeout = () => {};
global.AbortController = class { constructor() { this.signal = {}; } abort() {} };
global.fetch = async (url) => {
  if (url === "/auth/me") return { ok: true, status: 200, json: async () => ({ username: "alice", role: 0 }) };
  if (url === "/support/ask") return { ok: false, status: 422, json: async () => ({ detail: [{ msg: "文本至少 1 个字符" }, {}] }) };
  if (down) throw new TypeError("Failed to fetch");
  return { ok: true, status: 200, json: async () => [] };
};
const tick = () => new Promise((r) => setImmediate(r));
require(authPath);
(async () => {
  await tick();
  const auth = global.CodeMaxAuth, out = {};
  out.direct = [new TypeError("Failed to fetch"), new TypeError("NetworkError when attempting to fetch resource."),
    new TypeError("Load failed"), new TypeError("rows is not iterable"), new Error("云端已有新版本")].map(auth.failureText);
  down = true;
  await document.getElementById("auth-form").onsubmit({ preventDefault() {} });
  out.login = document.getElementById("auth-error").textContent;
  require(supportPath); await tick(); await tick();
  out.poll = document.getElementById("support-error").textContent;
  const byId = (id) => document.getElementById(id);
  byId("support-ask-text").value = "q";
  await byId("support-ask-form").onsubmit({ preventDefault() {} });
  out.ask = byId("support-ask-error").textContent;
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
@pytest.mark.parametrize("folder", ["app/frontend", "app/static/js"])
def test_network_failures_are_shown_in_chinese(tmp_path, folder):
    """TD-315：fetch 在网络层失败时抛英文 TypeError（三种浏览器文字不同），以前页面原样显示成
    「网络错误：TypeError: Failed to fetch」「读取失败，将重试：Failed to fetch」。只认这几种网络失败，
    脚本自身的 TypeError 与页面抛的中文 Error 原样显示。客服页的 422 列表文字走共享 errorText。"""
    harness = tmp_path / "harness.cjs"
    harness.write_text(_NETWORK_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / folder / "auth.js"), str(ROOT / folder / "support-page.js")],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    import json

    out = json.loads(proc.stdout.strip().splitlines()[-1])
    network = "网络连接失败，请检查网络后重试"
    assert out["direct"] == [network, network, network, "rows is not iterable", "云端已有新版本"]
    assert out["login"] == network, "登录表单不能再显示 TypeError: Failed to fetch"
    assert out["poll"] == f"读取失败，将重试：{network}"
    assert out["ask"] == "暂时无法回答：文本至少 1 个字符；输入无效"


def test_frontend_pages_never_show_raw_exception_text():
    """TD-315 护栏：页面显示错误一律经 failureText（本页的 failure 包装），不直接拼 e.message / ${ex}。
    唯一的例外是包装里的兜底（测试替身没有 failureText 时）与 auth.js 里 failureText 自身。"""
    offenders = []
    for path in sorted((ROOT / "app" / "frontend").glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\b(?:e|ex|err|error)\.message\b|\$\{(?:e|ex|err|error)\}", source):
            before = source[max(0, match.start() - 3):match.start()]
            line = source[source.rfind("\n", 0, match.start()) + 1:source.find("\n", match.end())]
            if before == "?? " or "NETWORK_FAILURE.test(" in line or line.lstrip().startswith("//"):
                continue
            offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], offenders


# ---------------------------------------------------------------- V-06 / TD-280：修改密码浮层

_PASSWORD_HARNESS = r"""
const path = process.argv[2];
const els = {}; const posts = []; let active = null; let reply = null;
const mkEl = (id) => {
  const el = { id, value: "", textContent: "", hidden: false, disabled: false, ariaLabel: "", classes: new Set(), children: new Set(),
    classList: { add(c) { el.classes.add(c); }, remove(c) { el.classes.delete(c); }, contains(c) { return el.classes.has(c); } },
    focus() { active = el; }, contains(o) { return el.children.has(o); }, addEventListener() {}, appendChild() {}, click() {} };
  return el;
};
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)), createElement: (t) => mkEl(t),
  get activeElement() { return active; }, body: mkEl("body") };
global.window = global; global.addEventListener = () => {};
global.fetch = async (url, opts = {}) => {
  if (url === "/auth/me") return { ok: true, status: 200, json: async () => ({ username: "alice", role: 0 }) };
  if (url === "/auth/logout") return { ok: true, status: 204, json: async () => null };
  posts.push({ url, method: opts.method, credentials: opts.credentials, body: JSON.parse(opts.body) });
  return { ok: reply.status === 200, status: reply.status, json: async () => reply.body };
};
require(path);
const byId = (id) => global.document.getElementById(id);
const tick = () => new Promise((r) => setImmediate(r));
const isOpen = () => byId("pw-mask").classes.has("open");
const fill = (o, n, a) => { byId("pw-old").value = o; byId("pw-new").value = n; byId("pw-again").value = a; };
const submit = async () => { await byId("pw-form").onsubmit({ preventDefault() {} }); await tick(); };
const snap = () => ({ open: isOpen(), err: byId("pw-error").textContent, done: byId("pw-done").textContent, posts: posts.length,
  fields: ["pw-old", "pw-new", "pw-again"].map((id) => byId(id).value) });
(async () => {
  await tick();
  const who = byId("auth-who"), out = {};
  byId("pw-mask").children.add(byId("pw-old"));
  out.label = who.ariaLabel;
  active = who; who.onclick();
  out.opened = { open: isOpen(), focusOld: active === byId("pw-old"), user: byId("pw-user").value, account: byId("pw-account").textContent };
  fill("secret123", "newpass1", "newpass2"); await submit(); out.mismatch = snap();
  fill("secret123", "secret123", "secret123"); await submit(); out.same = snap();
  reply = { status: 400, body: { detail: "原密码不正确" } };
  fill("wrong", "newpass1", "newpass1"); await submit(); out.wrongOld = snap();
  reply = { status: 200, body: { access_token: "T" } };
  fill("secret123", "newpass1", "newpass1"); await submit();
  out.ok = { ...snap(), sent: posts[posts.length - 1], user: global.CodeMaxAuth.user && global.CodeMaxAuth.user.username,
             cancel: byId("pw-cancel").textContent, focusCancel: active === byId("pw-cancel") };
  byId("pw-mask").children.add(byId("pw-cancel"));
  byId("pw-mask").onkeydown({ key: "Escape", stopPropagation() {} });
  out.esc = { open: isOpen(), focusWho: active === who };
  // 重新打开：上一次的成功提示与输入不能残留
  who.onclick(); out.reopen = snap();
  reply = { status: 401, body: { detail: "未登录" } };
  fill("secret123", "newpass2", "newpass2"); await submit();
  out.expired = { open: isOpen(), loginOpen: byId("auth-mask").classes.has("open"), err: byId("auth-error").textContent,
                  user: global.CodeMaxAuth.user, fields: snap().fields };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
@pytest.mark.parametrize("path", ["app/frontend/auth.js", "app/static/js/auth.js"])
def test_password_dialog_changes_password_and_handles_every_outcome(tmp_path, path):
    """后端 `POST /auth/password`（TD-70）早就有，但整站没有任何入口：用户怀疑密码泄露时只能找管理员。

    顶栏用户名即入口。前端先挡「两次不一致」「与原密码相同」（不消耗改密限流额度）；原密码错显示服务端
    文案；成功后清空三个密码框并说明其他设备会掉线；401 交给统一的会话到期处理。源码与产物都跑。
    """
    harness = tmp_path / "password.cjs"
    harness.write_text(_PASSWORD_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"{path} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    import json

    r = json.loads(proc.stdout.strip().splitlines()[-1])
    assert r["label"] == "alice（修改密码）", "用户名按钮的可访问名称要说明用途，且包含可见文字"
    assert r["opened"] == {"open": True, "focusOld": True, "user": "alice", "account": "当前账号：alice"}, r["opened"]
    assert r["mismatch"]["err"] == "两次输入的新密码不一致" and r["mismatch"]["posts"] == 0, r["mismatch"]
    assert r["same"]["err"] == "新密码不能与原密码相同" and r["same"]["posts"] == 0, r["same"]
    assert r["wrongOld"]["err"] == "原密码不正确" and r["wrongOld"]["open"] and r["wrongOld"]["posts"] == 1, r["wrongOld"]
    ok = r["ok"]
    assert ok["sent"] == {"url": "/auth/password", "method": "POST", "credentials": "same-origin",
                          "body": {"old_password": "secret123", "new_password": "newpass1"}}, ok["sent"]
    assert ok["err"] == "" and "其他设备" in ok["done"] and ok["fields"] == ["", "", ""], ok
    assert ok["user"] == "alice" and ok["open"], "改密成功后当前会话保持登录（服务端已换新 cookie）"
    assert ok["cancel"] == "关闭" and ok["focusCancel"], ok
    assert r["esc"] == {"open": False, "focusWho": True}, r["esc"]
    assert r["reopen"]["done"] == "" and r["reopen"]["err"] == "" and r["reopen"]["fields"] == ["", "", ""], r["reopen"]
    exp = r["expired"]
    assert exp["open"] is False and exp["loginOpen"] is True and "登录已过期" in exp["err"], exp
    assert exp["user"] is None and exp["fields"] == ["", "", ""], exp


def test_password_dialog_markup_matches_the_backend_contract():
    """浮层字段约束与 PasswordChangeIn 一致；autocomplete 让密码管理器把新密码存到正确的账号下。"""
    html = BASE.read_text(encoding="utf-8")
    header = html.split("<header>")[1].split("</header>")[0]
    assert re.search(r'<button type="button" class="ghost who" id="auth-who" aria-haspopup="dialog"[^>]*hidden>', header)
    dialog = html.split('id="pw-mask"')[1].split("<script")[0]
    assert 'role="dialog" aria-modal="true" aria-labelledby="pw-title"' in dialog
    assert 'id="pw-user" autocomplete="username"' in dialog
    assert 'id="pw-old" autocomplete="current-password" required maxlength="64"' in dialog
    for field in ("pw-new", "pw-again"):
        assert f'id="{field}" autocomplete="new-password" required minlength="6" maxlength="64"' in dialog
    assert 'id="pw-error" role="alert"' in dialog and 'id="pw-done" role="status"' in dialog
    assert html.index('id="pw-mask"') < html.index('<script src="/static/js/auth.js">'), "浮层必须在 auth.js 之前"


# ---------------------------------------------------------------- TD-272：真实浏览器复核暴露的排版与导航问题
#
# 这一节的对应用例都来自 2026-09-23 的第二次接手复核（真实 Chromium + axe-core 实测），
# 断言的是「模板里有没有那条规则/那个元素」，不是「浏览器渲染出来一定好看」。
# 视觉签收仍在 Windows 指南第 10～12 步，这里只防回退。

def test_header_has_skip_link_and_main_target():
    """WCAG 2.4.1：键盘用户要能跳过顶栏与页脚导航直接到正文。"""
    html = BASE.read_text(encoding="utf-8")
    assert 'class="skip" href="#main"' in html
    assert '<main id="main">' in html


def test_buttons_and_inputs_inherit_the_page_font():
    """表单控件默认是浏览器给的 13.33px Arial：比正文小一号且字体不一致。"""
    html = BASE.read_text(encoding="utf-8")
    assert re.search(r"button,\s*input,\s*select,\s*textarea\s*\{\s*font:\s*inherit", html)


def test_mobile_inputs_avoid_ios_zoom():
    """iOS Safari 在 <16px 的输入框聚焦时会放大整页 —— 窄屏必须有 16px 覆盖，而且这条覆盖必须**真的生效**。

    TD-272 写过这条规则，但它放在样式表中段：后面的 `textarea { font: 13px … }` 与特异性更高的
    `.modal input { font: inherit }` 把它盖掉了，真实 Chromium 390px 实测登录框/留言框/DDL 框全是 13px
    （TD-278）。所以这里不只查「规则存在」，还钉住两件决定层叠结果的事：
    ① 它是 base.html 样式表里的**最后一条**规则，且点名 `.modal input`；
    ② 各页模板自己的 <style>（文档顺序在 base 之后）不给输入控件写 font / font-size。
    """
    html = BASE.read_text(encoding="utf-8")
    style = html.split("<style>")[1].split("</style>")[0]
    tail = style[style.rindex("@media (max-width: 700px)"):]
    assert "input, select, textarea, .modal input { font-size: 16px; }" in tail
    after = tail.split("font-size: 16px; }", 1)[1]
    assert after.strip() == "}", f"16px 规则后面还有规则，会被覆盖：{after!r}"
    control = re.compile(r"([^{}]*\b(?:input|select|textarea)\b[^{}]*)\{([^}]*)\}")
    for tpl in (ROOT / "app" / "templates").glob("*.html"):
        if tpl.name == "base.html":
            continue
        for block in re.findall(r"<style>(.*?)</style>", tpl.read_text(encoding="utf-8"), re.S):
            for selector, body in control.findall(re.sub(r"/\*.*?\*/", "", block, flags=re.S)):
                assert not re.search(r"\bfont(-size)?\s*:", body), f"{tpl.name} 的 {selector.strip()} 会盖掉手机 16px"


def test_mobile_navigation_and_footer_links_get_touchable_padding():
    """窄屏导航链接此前只有 21–24px 高，低于 WCAG 2.2 AA 的 24×24 下限。"""
    html = BASE.read_text(encoding="utf-8")
    assert "header nav a, footer .fnav a { display: inline-block; padding: 8px 4px; }" in html


def test_mobile_header_is_two_rows_brand_and_login_then_scrolling_nav():
    """窄屏顶栏曾是三～四行（真实 CJK 字体 390px 实测 189px，管理员更高）。TD-278 改成网格两行：
    第一行站点名 + 登录态，第二行整组导航（工具 / 客服 / 管理员入口 / 商品）横向滚动。

    客服、管理员入口、商品链接必须在 nav 里，窄屏才能进入同一滚动行；登录控件留在 .actions。
    """
    html = BASE.read_text(encoding="utf-8")
    header = html.split("<header>")[1].split("</header>")[0]
    nav = header.split("<nav")[1].split("</nav>")[0]
    for link in ('href="/support/center"', 'id="admin-entry"', 'class="cta-link"'):
        assert link in nav, f"{link} 应在站点导航里"
    actions = header.split('<div class="actions">')[1]
    assert 'id="btn-auth"' in actions and 'id="btn-logout"' in actions and "<a " not in actions
    mobile = html.split("@media (max-width: 900px) {")[1].split("\n      }\n")[0]
    assert "header { display: grid; grid-template-columns: minmax(0, 1fr) auto;" in mobile
    assert "header nav { grid-column: 1 / -1; grid-row: 2; flex-wrap: nowrap; overflow-x: auto;" in mobile


def test_favicon_exists_and_is_a_dependency_free_svg():
    """此前没有图标：浏览器对每个页面都会多打一次 /favicon.ico 并拿到 404。"""
    html = BASE.read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />' in html
    svg = (ROOT / "app" / "static" / "favicon.svg").read_text(encoding="utf-8")
    # 零外部引用：除 xml 命名空间外不该出现第二个 URL，也没有脚本、外链或字体引用。
    assert "<script" not in svg.lower() and "href=" not in svg and "url(" not in svg
    assert svg.count("http") == 1 and "http://www.w3.org/2000/svg" in svg


def test_admin_link_is_labelled_and_hidden_for_non_admins():
    """顶栏「订单管理（管理员）」对普通用户是死链接；脚本按角色隐藏，但**不是**权限。

    角色仍由后端 `require_admin` 查库判断（get_current_user → User.role），前端只是导航整洁。
    """
    html = BASE.read_text(encoding="utf-8")
    assert '<a id="admin-entry" href="/admin/payments" hidden>' in html, "顶栏入口默认隐藏，由脚本按角色显示"
    assert '<a href="/admin/payments">订单管理（管理员）</a>' in html.split("<footer>")[1], "页脚要有常驻入口"
    source = (ROOT / "app" / "frontend" / "auth.js").read_text(encoding="utf-8")
    assert 'getElementById("admin-entry")' in source and "on && user.role === 1" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
@pytest.mark.parametrize("path", ["app/frontend/auth.js", "app/static/js/auth.js"])
def test_admin_entry_visibility_follows_the_reported_role(tmp_path, path):
    """Node 真跑 auth.js：访客与普通用户都隐藏，只有 role=1 显示（页脚另有常驻入口）。"""
    harness = tmp_path / "admin_entry.cjs"
    harness.write_text(_ADMIN_ENTRY_HARNESS, encoding="utf-8")
    proc = subprocess.run([ "node", str(harness), str(ROOT / path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"{path} 执行失败：\n{proc.stdout}\n{proc.stderr}"
    import json

    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result == {"anon": True, "member": True, "admin": False}, result


_ADMIN_ENTRY_HARNESS = r"""
const els = {};
const mkEl = (id) => ({ id, value: "", textContent: "", hidden: false, disabled: false,
  classList: { add() {}, remove() {}, contains() { return false; } },
  focus() {}, contains() { return false; }, addEventListener() {}, click() {} });
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)), createElement: (t) => mkEl(t),
  body: mkEl("body"), activeElement: null, addEventListener() {} };
global.window = global; global.addEventListener = () => {};
let current = null;
global.fetch = async () => ({ ok: !!current, status: current ? 200 : 401,
  json: async () => current || {} });
require(process.argv[2]);
(async () => {
  const auth = global.CodeMaxAuth;
  await new Promise((r) => setImmediate(r));
  const anon = !!els["admin-entry"].hidden;
  current = { username: "alice", role: 0 };
  await auth.refresh();
  const member = !!els["admin-entry"].hidden;
  current = { username: "boss", role: 1 };
  await auth.refresh();
  const admin = !!els["admin-entry"].hidden;
  console.log(JSON.stringify({ anon, member, admin }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def test_support_message_timestamp_meets_contrast_on_its_own_bubble():
    """客服消息时间：11.7px 的 #64748b 落在 #f1f5f9 气泡上只有 4.34:1（axe 实测）。"""
    css = SUPPORT_CSS.read_text(encoding="utf-8")
    rule = re.search(r"#support-messages small \{[^}]*\}", css)
    assert rule, "support.css 里应有消息时间样式"
    assert "color: #475569" in rule.group(0), rule.group(0)
    assert contrast("#475569", "#f1f5f9") >= 4.5
    assert contrast("#475569", "#eff6ff") >= 4.5  # 管理员气泡底色


def test_tool_pages_have_a_visible_page_heading():
    """工具页此前只有顶栏的站点名 h1：读屏按标题跳转时看不到「这页是干什么的」。"""
    for name in ("er.html", "mermaid.html", "drawio.html"):
        text = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
        assert '{% if page_heading %}<h2 class="page-heading">{{ page_heading }}</h2>{% endif %}' in text, name
    site = (ROOT / "app" / "routers" / "site.py").read_text(encoding="utf-8")
    assert 'page_heading="" if tool.key == "home" else tool.title' in site, "标题只该有 Tool.title 一处来源"


async def test_pages_render_one_h1_and_a_page_heading(client, mock_mode):
    """真实渲染：整站每页只有一个 h1（站点名）；工具页额外有可见的页面标题 h2。"""
    for path, heading in (("/tools/er", "SQL DDL 转 ER 图"), ("/tools/mermaid", "自然语言生成 UML 类图"),
                          ("/tools/drawio", "Drawio 在线流程图"), ("/admin/payments", "订单与收款管理"),
                          ("/support/center", "站内客服"), ("/shop", "我的订单"),
                          ("/shop/mock-pay?order_no=CM1", "模拟收银台")):
        html = (await client.get(path)).text
        assert html.count("<h1>") == 1, f"{path} 应当只有一个 h1"
        assert heading in html, f"{path} 缺少页面级标题"
    home = (await client.get("/")).text
    assert 'class="page-heading"' not in home, "首页的卡片标题已经是 h2，不该再重复一个页面标题"


def test_admin_console_separates_read_only_evidence_from_dangerous_actions():
    """订单管理页 13 个表单与 12 个 pre 叠在一列：只读凭证单独成组、危险操作加红框。"""
    html = (ROOT / "app" / "templates" / "payments-admin.html").read_text(encoding="utf-8")
    assert '<details class="finance-readonly" open>' in html, "只读凭证应当可折叠（默认展开）"
    assert 'class="finance-readonly" open>\n<summary>只读凭证与核验记录' in html
    for form in ("finance-refund-send", "finance-refund-stop"):
        assert re.search(r'<form id="' + form + r'" class="danger" hidden>', html), form
    assert "#finance-title, #finance-list button { overflow-wrap:anywhere; }" in html, "长订单号不许撑破布局"
    assert "summary { padding: 6px 0; }" in BASE.read_text(encoding="utf-8"), "折叠标题也要够 24px（base.html 统一给）"


def test_hidden_attribute_wins_over_layout_display_rules():
    """`hidden` 必须真的不显示：作者写的 `display: inline-block` 会盖掉 UA 的 display:none。

    实测踩过：给导航链接加内边距与 `display: inline-block` 之后，带 `hidden` 的
    管理员入口重新出现在顶栏（它是 hidden，却占了 38px 高）。全局兜底并钉住。
    """
    html = BASE.read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in html


# ---------------------------------------------------------------- TD-278：真实浏览器复核发现的页面结构问题


def test_shop_order_history_comes_after_every_status_section():
    """「我的订单」曾夹在落地区与状态区之间：付完款回到 /shop，390px 实测「我的订单」在上、
    「支付成功 / 下载」在下（top 218 vs 268），而且 h3 先于状态区的 h2（axe `heading-order`）。"""
    html = (ROOT / "app" / "templates" / "shop.html").read_text(encoding="utf-8")
    history = html.index('id="btn-history"')
    for status in ("st-pending", "st-paid", "st-refunded", "st-closed"):
        assert html.index(f'id="{status}"') < history, f"{status} 应排在「我的订单」之前"


def test_closed_order_page_shows_its_order_number():
    """TD-281：render() 把关闭单的订单号写进 #x-no，但 #x-no 原本在从不显示的 st-downloaded 里，
    关闭页因此没有订单号 —— 而页面恰恰让用户「通过站内客服核对」。downloaded 走 paid 区重领，
    st-downloaded 是死区，已删除。"""
    html = (ROOT / "app" / "templates" / "shop.html").read_text(encoding="utf-8")
    closed = html.split('<section id="st-closed"')[1].split("</section>")[0]
    assert 'id="x-no"' in closed
    assert html.count('id="x-no"') == 1
    assert 'id="st-downloaded"' not in html
    js = (ROOT / "app" / "frontend" / "shop-page.js").read_text(encoding="utf-8")
    assert 'getElementById("st-downloaded")' not in js
    assert 'o.status === "paid" || o.status === "downloaded"' in js, "downloaded 仍须渲染成可重新领取的 paid 区"


def test_drawio_login_prompt_is_one_toggleable_element():
    """已登录时「云端保存需登录：[登录 / 注册]」要整段隐藏，所以文字和按钮必须包在同一个可切换元素里。"""
    html = (ROOT / "app" / "templates" / "drawio.html").read_text(encoding="utf-8")
    prompt = html.split('<span id="drawio-login-prompt">')[1].split("</button></span>")[0]
    assert "云端保存需登录" in prompt and 'id="btn-login"' in prompt
    assert 'id="auth-status"' not in prompt, "登录状态文字不能跟着提示一起隐藏"


def test_type_scale_is_one_set_of_variables_with_16px_body_on_phones():
    """TD-287：此前正文 14px、次级 12–13px，真实 Chromium 统计 85–95% 的可见文字是 13–14px，手机上也一样。
    字号收拢到 :root 变量：正文 15px、手机 16px，次级不低于 13px；页面标题统一 22px（原来 20/21/24px 各不相同）。"""
    html = BASE.read_text(encoding="utf-8")
    style = html.split("<style>")[1].split("</style>")[0]
    assert "--fs-body: 15px; --fs-small: 14px; --fs-xs: 13px; --fs-title: 22px;" in style
    assert "font: var(--fs-body)/1.65 system-ui" in style
    assert "@media (max-width: 700px) { :root { --fs-body: 16px; } }" in style
    sizes = [int(n) for n in re.findall(r"font-size:\s*(\d+)px", style)]
    assert min(sizes) >= 13, f"base.html 仍有小于 13px 的字号：{sorted(sizes)}"
    assert ".page-heading { margin: 0 0 12px; font-size: var(--fs-title);" in style
    shop = SHOP.read_text(encoding="utf-8")
    support = (ROOT / "app" / "templates" / "support-center.html").read_text(encoding="utf-8")
    admin = (ROOT / "app" / "templates" / "payments-admin.html").read_text(encoding="utf-8")
    assert '<h2 class="page-heading">{{ product_name }}</h2>' in shop
    assert '<h2 class="page-heading">站内客服</h2>' in support
    assert ".finance .finance-heading { margin: 0 0 12px; font-size: var(--fs-title); }" in admin
    assert ".finance section h2 { margin: 0 0 8px; font-size: 18px; }" in admin, "分区标题不能比页面标题大"


def test_header_main_and_footer_share_one_content_column():
    """TD-287：顶栏、正文、页脚用同一个 --edge 内边距，宽屏对齐到 1440px 版心，窄屏退回 --gutter。"""
    style = BASE.read_text(encoding="utf-8").split("<style>")[1].split("</style>")[0]
    assert "--edge: max(var(--gutter), calc((100% - var(--page-max)) / 2));" in style
    for rule in ("padding: 12px var(--edge);", "main { padding: 20px var(--edge) 28px;",
                 "padding: 14px var(--edge);", "padding: 8px var(--edge) 0;"):
        assert rule in style, rule
    admin = (ROOT / "app" / "templates" / "payments-admin.html").read_text(encoding="utf-8")
    assert ".finance { padding: 4px 0 0; }" in admin and "max-width: 1180px" not in admin


def test_home_page_is_a_card_grid_with_a_lead_line_but_no_page_heading():
    """TD-287：首页三张卡片原来各占满整行。改为自适应网格 + 导语；导语取自 HOME.description（同一来源），
    且不是标题——首页不该再多一个页面标题（见 test_pages_render_one_h1_and_a_page_heading）。"""
    index = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<p class="home-lead">{{ description }}</p>' in index
    assert '<section class="tool-grid">' in index
    style = BASE.read_text(encoding="utf-8")
    assert ".tool-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));" in style


def test_diagram_canvases_explain_themselves_before_the_first_render():
    """TD-287：生成前 ER 画布与类图预览只是空框。提示用纯 CSS：Mermaid 预览靠 :empty（结果写入后消失），
    ER 靠「#er-tools 仍 hidden」判断（renderEr 成功后才取消 hidden）。提示色在两种底色上都要达 AA。"""
    base = BASE.read_text(encoding="utf-8")
    er = (ROOT / "app" / "templates" / "er.html").read_text(encoding="utf-8")
    assert "#mermaid-preview:empty::before { content:" in base
    assert ".er-view:has(> #er-tools[hidden])::after { content:" in er
    assert "pointer-events: none;" in er.split(".er-view:has(")[1].split("}")[0], "提示不能挡住画布拖动"
    js = (ROOT / "app" / "frontend" / "er-page.js").read_text(encoding="utf-8")
    assert "tools.hidden = false;" in js, "ER 提示依赖渲染成功后取消 #er-tools 的 hidden"
    assert contrast("#64748b", "#f8fafc") >= 4.5  # ER 画布底色
    assert contrast("#64748b") >= 4.5             # 类图预览白底


# ---------------------------------------------------------------- TD-306：截图复核后的界面整理


def _base_style() -> str:
    return BASE.read_text(encoding="utf-8").split("<style>")[1].split("</style>")[0]


def test_textareas_use_the_body_font_and_only_code_inputs_are_monospace():
    """所有 textarea 原来都是等宽字体（给 DDL 准备的），Mermaid 自然语言描述、客服留言也跟着用了 Consolas。
    现在默认正文字体，只有 class="code" 用等宽；DDL 输入框点名 code，其余多行输入不点名。"""
    style = _base_style()
    assert "textarea { font-size: var(--fs-small); line-height: 1.55; }" in style
    assert "textarea.code { font-family: ui-monospace, Consolas, monospace; }" in style
    assert not re.search(r"(?m)^\s*textarea\s*\{[^}]*monospace", style), "textarea 默认规则不应再带等宽字体"
    templates = ROOT / "app" / "templates"
    assert '<textarea id="ddl-input" class="code"' in (templates / "er.html").read_text(encoding="utf-8")
    for name, textarea_id in (("mermaid.html", "text-input"), ("support-center.html", "support-ask-text"),
                              ("support-center.html", "support-body")):
        tag = re.search(rf'<textarea id="{textarea_id}"[^>]*>', (templates / name).read_text(encoding="utf-8")).group(0)
        assert 'class="code"' not in tag, f"{textarea_id} 是自然语言输入，不该用等宽字体"
    assert "font-family" not in SUPPORT_CSS.read_text(encoding="utf-8"), "客服页不需要再单独把字体改回正文"


def test_form_controls_share_one_border_that_meets_non_text_contrast():
    """单行输入框与下拉框此前只有登录浮层、drawio 工具栏各自画了浅色边框，管理页是浏览器默认样式。
    统一规则用 :where()（特异性 0，页面规则仍可覆盖）、不写字体（不干扰手机 16px）；
    边框色是辨认控件的唯一线索，WCAG 1.4.11 要求 ≥3:1，白底与客服助手卡片的浅底都要达标。"""
    style = _base_style()
    color = re.search(r"--control-border: (#[0-9a-f]{6});", style).group(1)
    assert contrast(color) >= 3 and contrast(color, "#f8fafc") >= 3, color
    rule = re.search(r":where\(input:not\([^{]*\), select\) \{([^}]*)\}", style)
    assert rule, "缺少全站单行输入/下拉框规则"
    assert "border: 1px solid var(--control-border)" in rule.group(1) and "background: #fff" in rule.group(1)
    assert not re.search(r"\bfont(-size)?\s*:", rule.group(1)), "统一控件规则不能写字体（会与手机 16px 规则打架）"
    for selector in ("textarea { width: 100%; padding: 8px; border: 1px solid var(--control-border);",
                     ".modal input { width: 100%; padding: 7px 9px; border: 1px solid var(--control-border);"):
        assert selector in style, selector
    assert "#cbd5e1; border-radius: 6px" not in style, "输入框边框不应再用 1.48:1 的 #cbd5e1"
    drawio = (ROOT / "app" / "templates" / "drawio.html").read_text(encoding="utf-8")
    assert ".toolbar select" not in drawio, "drawio 工具栏控件由全站规则统一，不再单独画边框"


def test_tool_form_buttons_sit_in_a_spaced_flex_row():
    """ER / 类图页的按钮原来靠行内空白隔开（约 4px），截图里几乎挨在一起。"""
    assert ".form-actions { display: flex; flex-wrap: wrap; gap: 8px;" in _base_style()
    for name, first in (("er.html", "er-submit"), ("mermaid.html", "mermaid-submit")):
        html = (ROOT / "app" / "templates" / name).read_text(encoding="utf-8")
        assert re.search(rf'<p class="form-actions">\s*<button type="submit" id="{first}">', html), name


def test_shop_order_number_in_headings_can_wrap():
    """登录后有待付款订单时，标题「订单 CM…」里 28 位订单号不能换行，390px 实测整页被撑到 418px 宽。"""
    html = SHOP.read_text(encoding="utf-8")
    assert ".shop h2, .shop .muted { overflow-wrap: anywhere; }" in html
    # TD-317：待支付态的订单号从标题移到 .muted 行（与支付成功、已关闭两态一致），仍在可换行的元素里。
    assert '<p class="muted">订单 <span id="p-no"></span></p>' in html


def test_list_rows_are_not_primary_buttons_and_mark_the_current_item():
    """订单列表、客服会话列表原来是一排实心主按钮，与「查询 / 刷新」长得一样，也看不出选中的是哪条。
    改为 button.item 列表行；点击后由脚本设 aria-current，CSS 按它高亮。三个列表都要用。"""
    style = _base_style()
    assert re.search(r"button\.item \{[^}]*background: #fff;[^}]*white-space: pre-line;", style)
    assert 'button.item[aria-current="true"] {' in style
    frontend = ROOT / "app" / "frontend"
    for name in ("payments-admin.js", "support-page.js", "shop-page.js"):
        js = (frontend / name).read_text(encoding="utf-8")
        assert 'button.className = "item";' in js, name
        assert 'button.setAttribute?.("aria-current", "true");' in js and 'removeAttribute?.("aria-current")' in js, name
    admin = (frontend / "payments-admin.js").read_text(encoding="utf-8")
    assert "button.textContent = `${row.order_no}\\n${row.username}" in admin, "订单号单独一行"


_SUPPORT_LIST_HARNESS = r"""
const path = process.argv[2];
const els = {}; let opened = 0;
const mkEl = (id) => {
  const attrs = {};
  return { id, value: "", textContent: "", hidden: false, disabled: false, className: "", children: [], attrs,
    classList: { add() {}, remove() {} }, setAttribute(k, v) { attrs[k] = String(v); }, removeAttribute(k) { delete attrs[k]; },
    append(...x) { this.children.push(...x); }, prepend(...x) { this.children.unshift(...x); },
    replaceChildren(...x) { this.children = x; }, focus() {}, scrollIntoView() {} };
};
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)), createElement: (t) => mkEl(t) };
const auth = { user: { username: "boss", role: 1 }, onChange(fn) { this.listener = fn; }, open() { opened += 1; } };
global.window = global; global.CodeMaxAuth = auth; global.addEventListener = () => {};
global.setTimeout = () => 1; global.clearTimeout = () => {};
global.fetch = async (url) => {
  let data = [];
  if (url.startsWith("/support/conversations?") || url === "/support/conversations")
    data = [{ id: 9, customer_id: 1, username: "alice", awaiting_admin: true }, { id: 8, customer_id: 2, username: "bob", awaiting_admin: false }];
  else if (url.startsWith("/support/conversations/2/messages"))
    data = [{ id: 5, sender_role: 0, body: "hi", create_time: "2026-09-28T09:49:30" }];
  return { ok: true, status: 200, json: async () => data };
};
const tick = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); };
require(path);
(async () => {
  await tick();
  const inbox = els["support-inbox"];
  const initialTitle = els["support-title"].textContent;
  const classes = inbox.children.map((b) => b.className);
  inbox.children[0].onclick(); await tick();
  inbox.children[1].onclick(); await tick();
  const marks = inbox.children.map((b) => b.attrs["aria-current"] || null);
  const meta = els["support-messages"].children[0].children[0].textContent;
  els["support-inbox-refresh"].onclick(); await tick();
  const afterRefresh = els["support-inbox"].children.map((b) => b.attrs["aria-current"] || null);
  auth.listener(null);
  const guestTitle = els["support-title"].textContent, loginHidden = els["support-login"].hidden;
  els["support-login-btn"].onclick();
  console.log(JSON.stringify({ initialTitle, classes, marks, meta, afterRefresh, guestTitle, loginHidden, opened }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
@pytest.mark.parametrize("path", ["app/frontend/support-page.js", "app/static/js/support-page.js"])
def test_support_inbox_marks_the_open_conversation_and_formats_time_in_chinese(tmp_path, path):
    """TD-306：① 管理员选中会话前标题提示「请先选择一个客户会话」（原来是没有内容可看的「我的留言」）；
    ② 会话列表是 item 行，只有当前会话带 aria-current，刷新列表后仍保留；③ 消息时间固定中文格式
    （原来跟随浏览器语言，英文系统上是「9/28/2026, 9:49:30 AM」）；④ 游客提示旁的按钮打开登录浮层。"""
    harness = tmp_path / "support-list.cjs"
    harness.write_text(_SUPPORT_LIST_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"执行失败：\n{proc.stdout}\n{proc.stderr}"
    import json

    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["initialTitle"] == "请先选择一个客户会话", result
    assert result["classes"] == ["item", "item"], result
    assert result["marks"] == [None, "true"], result
    assert result["afterRefresh"] == [None, "true"], result
    assert result["meta"] == "客户 · 2026/09/28 09:49", result
    assert result["guestTitle"] == "我的留言" and result["loginHidden"] is False, result
    assert result["opened"] == 1, result


# ---------------------------------------------------------------- TD-307：R-06 三项界面小问题

_TABS_HARNESS = r"""
const path = process.argv[2];
const els = {};
const mkEl = (id) => { const attrs = {}; return { id, attrs, value: "", textContent: "", hidden: false, disabled: false,
  classList: { add() {}, remove() {}, contains() { return false; } }, setAttribute(k, v) { attrs[k] = String(v); },
  focus() {}, contains() { return false; }, addEventListener() {}, appendChild() {}, click() {} }; };
global.document = { getElementById: (id) => (els[id] ||= mkEl(id)), createElement: (t) => mkEl(t), activeElement: null, body: mkEl("body") };
global.window = global; global.addEventListener = () => {};
global.fetch = async () => ({ ok: false, status: 401, json: async () => ({}) });
require(path);
(async () => {
  await new Promise((r) => setImmediate(r));
  const get = (id) => global.document.getElementById(id);
  const state = () => [get("tab-login").attrs["aria-pressed"], get("tab-register").attrs["aria-pressed"], get("auth-title").textContent];
  global.CodeMaxAuth.open("register"); const register = state();
  get("tab-login").onclick(); const login = state();
  get("tab-register").onclick(); const back = state();
  console.log(JSON.stringify({ register, login, back }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
@pytest.mark.parametrize("path", ["app/frontend/auth.js", "app/static/js/auth.js"])
def test_login_dialog_marks_the_current_tab(tmp_path, path):
    """登录浮层的「登录 / 注册」两个标签原来外观相同，只能看下面的标题判断当前模式（RR-25 ①）。
    setMode 给当前标签 aria-pressed="true"、另一个 "false"，CSS 按它高亮；打开与切换都要同步。"""
    harness = tmp_path / "tabs.cjs"
    harness.write_text(_TABS_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"执行失败：\n{proc.stdout}\n{proc.stderr}"
    import json

    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["register"] == ["false", "true", "注册新账号"], result
    assert result["login"] == ["true", "false", "登录"], result
    assert result["back"] == ["false", "true", "注册新账号"], result
    assert '.modal .tabs button[aria-pressed="true"] {' in _base_style()


def test_mobile_nav_fades_at_the_right_edge_and_the_last_link_can_clear_it():
    """390px 下「站内客服」「毕设服务」在导航行右侧之外，原来看不出还能滑（RR-25 ③）。
    右缘 28px 渐隐作提示；末尾同宽的 ::after 占位让滑到最右时最后一个链接离开渐隐区。
    两条都只在窄屏段里，桌面导航不渐隐；原有的 `header nav { grid-column … }` 规则保持不变。"""
    style = _base_style()
    mobile = style.split("@media (max-width: 900px) {")[1].split("\n      }\n")[0]
    fade = "linear-gradient(to right, #000 calc(100% - 28px), transparent)"
    assert f"-webkit-mask-image: {fade};" in mobile and f"mask-image: {fade};" in mobile
    assert 'header nav::after { content: ""; flex: 0 0 28px; }' in mobile
    desktop = style.replace(mobile, "")
    assert "mask-image" not in desktop, "渐隐只用于窄屏的横向滚动导航"


# ---------------------------------------------------------------- TD-317：第二轮截图复核（字号阶梯、空框、页脚行距、回调地址）


def test_pending_order_heading_states_the_status_not_the_order_number():
    """待支付态的标题原来是「订单 + 28 位订单号」，24px 粗体在 390px 宽折成三行，比状态和付款按钮还显眼。"""
    pending = SHOP.read_text(encoding="utf-8").split('<section id="st-pending"')[1].split("</section>")[0]
    assert "<h2>等待支付</h2>" in pending
    assert 'id="p-no"' in pending and "<h2>订单" not in pending


def test_unstyled_headings_follow_the_type_scale():
    """截图实测：商城状态区 h2 是浏览器默认 1.5em（22.5 / 24px），「我的订单」和客服页 h3 是默认 1.17em
    （17.55 / 18.72px），模拟收银台标题写死 24px，授权页标题写死 20px —— 同一站点的标题有五六种大小。"""
    style = _base_style()
    assert "--fs-subtitle: 17px;" in style
    assert "h2 { font-size: var(--fs-title); line-height: 1.35; }" in style
    assert "h3 { font-size: var(--fs-subtitle); line-height: 1.4; }" in style
    mock = (ROOT / "app" / "templates" / "mock_pay.html").read_text(encoding="utf-8")
    assert not re.search(r"\.mock-title\{[^}]*font-size", mock), "模拟收银台标题应跟随全站 h2"
    consent = (ROOT / "app" / "templates" / "oauth_consent.html").read_text(encoding="utf-8")
    assert '<h2 class="page-heading">' in consent
    assert not re.search(r"font-size:\s*\d+px", consent), "授权页不再写死像素字号"


def test_empty_status_boxes_are_not_drawn():
    """mock 模式没有收款码，待支付页出现一个带边框的空小方块；模拟收银台点击之前，
    空的输出 pre 带着全站 pre 的底色和边框显示成一个空灰框。"""
    assert ".pay .qr:empty { display: none; }" in SHOP.read_text(encoding="utf-8")
    assert "#out:empty{display:none}" in (ROOT / "app" / "templates" / "mock_pay.html").read_text(encoding="utf-8")


def test_mobile_footer_rows_are_spaced_by_padding_only():
    """窄屏页脚链接已有上下 8px 内边距，行间再加 14px gap，两行之间空出约 30px。行距只留给内边距；
    覆盖规则必须排在 footer .fnav 的 gap 之后，否则被它盖掉。"""
    style = _base_style()
    base_rule = style.index("footer .fnav { display: flex; gap: 14px;")
    override = style.index("@media (max-width: 900px) { footer .fnav { row-gap: 0; } }")
    assert override > base_rule


def test_oauth_redirect_uri_wraps_inside_the_card():
    """回调地址是一整串没有空格的 URL；390px 截图里它越过授权卡片右边框（页面没变宽，所以查不出横向滚动）。"""
    consent = (ROOT / "app" / "templates" / "oauth_consent.html").read_text(encoding="utf-8")
    line = next(x for x in consent.splitlines() if "{{ redirect_uri }}</code>" in x)
    assert "overflow-wrap: anywhere" in line



# 默认以彩色 emoji 显示的字符（Unicode Emoji_Presentation）：BMP 里的这些码位，加上 U+1F000 起的图形平面。
# 它们没有普通文字字形可退，没有彩色 emoji 字体的系统（常见于 Linux 桌面）只能画方框。
# ⚠（U+26A0）这类默认文字显示的符号不在其中：中文字体自带字形，截图核对正常。
_EMOJI_PRESENTATION = re.compile(
    "[\u231a\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd\u25fe\u2614\u2615\u2648-\u2653\u267f\u2693\u26a1\u26aa\u26ab"
    "\u26bd\u26be\u26c4\u26c5\u26ce\u26d4\u26ea\u26f2\u26f3\u26f5\u26fa\u26fd\u2705\u270a\u270b\u2728\u274c\u274e"
    "\u2753-\u2755\u2757\u2795-\u2797\u27b0\u27bf\u2b1b\u2b1c\u2b50\u2b55\U0001F000-\U0001FAFF]"
)


def test_visible_text_has_no_emoji_only_characters():
    """TD-325：支付成功页标题原来是「✅ 支付成功」，真 Chromium（无彩色 emoji 字体）截图里显示成「☒ 支付成功」。
    扫模板与前端脚本里用户看得见的部分（去掉 HTML / Jinja / JS 注释），不许出现只能以 emoji 显示的字符。"""
    found = []
    for path in sorted((ROOT / "app" / "templates").glob("*.html")) + sorted((ROOT / "app" / "frontend").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            text = re.sub(r"<!--.*?-->|\{#.*?#\}", "", text, flags=re.S)
        else:
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            text = re.sub(r"(^|\s)//[^\n]*", r"\1", text)
        found += [f"{path.name}: {ch} U+{ord(ch):04X}" for ch in _EMOJI_PRESENTATION.findall(text)]
    assert not found, found


def test_empty_support_thread_leaves_no_gap_but_stays_a_live_region():
    """TD-325：还没有留言时，空的 #support-messages 带默认外边距，在「我的留言」和留言框之间留一段空白，像加载失败。
    只收外边距、不隐藏：它是 aria-live 区域，display:none 会移出无障碍树，第一条消息出现时读屏可能不播报。"""
    css = (ROOT / "app" / "static" / "support.css").read_text(encoding="utf-8")
    rule = re.search(r"#support-messages:empty\s*\{([^}]*)\}", css)
    assert rule and "margin: 0" in rule.group(1) and "display" not in rule.group(1)
    html = (ROOT / "app" / "templates" / "support-center.html").read_text(encoding="utf-8")
    assert '<ol id="support-messages" aria-live="polite" aria-label="会话消息"></ol>' in html, "模板里不能有空白，否则 :empty 不成立"


# ---------------------------------------------------------------- TD-326：第四轮真 Chromium 截图（使用中的状态）

_INBOX_HARNESS = r"""
const [authPath, supportPath, scenario] = [process.argv[2], process.argv[3], process.argv[4]];
const els = {};
const mkEl = (id) => { const classes = new Set(); return { id, value: "", textContent: "", hidden: false, disabled: false, children: [],
  classList: { add(c) { classes.add(c); }, remove(c) { classes.delete(c); }, contains(c) { return classes.has(c); }, toggle(c, on) { on ? classes.add(c) : classes.delete(c); } },
  focus() {}, contains() { return false; }, setAttribute() {}, removeAttribute() {},
  append(...xs) { this.children.push(...xs); }, appendChild(x) { this.children.push(x); }, prepend() {},
  replaceChildren(...xs) { this.children = xs; }, addEventListener() {}, click() {} }; };
// 模板里空状态提示带 hidden（support-center.html），假 DOM 保持一致
global.document = { getElementById: (id) => (els[id] ||= Object.assign(mkEl(id), { hidden: id === "support-inbox-empty" })),
  createElement: (t) => mkEl(t), body: mkEl("body"), activeElement: null, addEventListener() {} };
global.window = global; global.addEventListener = () => {}; global.setTimeout = () => 1; global.clearTimeout = () => {};
global.AbortController = class { constructor() { this.signal = {}; } abort() {} };
global.fetch = async (url) => {
  const body = url === "/auth/me" ? { username: "admin", role: 1 }
    : url.startsWith("/support/conversations") ? (scenario === "rows" ? [{ id: 1, customer_id: 7, username: "student2", awaiting_admin: true }] : [])
    : [];
  return { ok: true, status: 200, json: async () => body, headers: { get: () => null } };
};
require(authPath);
(async () => {
  const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
  await tick(); require(supportPath); await tick();
  const loaded = { empty: !els["support-inbox-empty"].hidden, rows: els["support-inbox"].children.length, panel: !els["support-inbox-panel"].hidden };
  await els["btn-logout"].onclick(); await tick();  // 顶栏「退出」：auth.js 通知订阅页 onUser(null)
  console.log(JSON.stringify({ loaded, afterLogout: { empty: !els["support-inbox-empty"].hidden } }));
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def _run_inbox(tmp_path, scenario: str) -> dict:
    import json

    harness = tmp_path / "inbox.cjs"
    harness.write_text(_INBOX_HARNESS, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ROOT / "app/frontend/auth.js"), str(ROOT / "app/frontend/support-page.js"), scenario],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"执行失败：\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 执行真实前端代码")
def test_admin_inbox_says_so_when_there_are_no_conversations(tmp_path):
    """真 Chromium 截图：管理员没有客户会话时，「客户会话 / 刷新会话」下面什么也没有，
    看不出是加载完了还是没加载。现在显示空状态；有会话时不显示；退出后收起。"""
    empty = _run_inbox(tmp_path, "empty")
    assert empty["loaded"] == {"empty": True, "rows": 0, "panel": True}
    assert empty["afterLogout"]["empty"] is False
    rows = _run_inbox(tmp_path, "rows")
    assert rows["loaded"] == {"empty": False, "rows": 1, "panel": True}


def test_inbox_empty_state_is_announced_and_hidden_by_default():
    html = (ROOT / "app/templates/support-center.html").read_text(encoding="utf-8")
    tag = re.search(r'<p id="support-inbox-empty"[^>]*>', html)
    assert tag and "hidden" in tag.group(0) and 'role="status"' in tag.group(0)
    assert html.index('id="support-inbox"') < html.index('id="support-inbox-empty"') < html.index('id="support-inbox-more"')


def test_list_rows_break_between_fields_not_inside_chinese_words():
    """真 Chromium 截图（390px）：订单列表的状态被拆成「可重新 / 下载」「已全额 / 退款」，
    管理端订单列表拆成「已关 / 闭（closed）」。button.item 三处用法都是「字段 · 字段」，用 keep-all
    只在空格与标点处断行；overflow-wrap:anywhere 必须同时保留，否则超长单字段会撑出横向滚动。"""
    css = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
    rule = re.search(r"button\.item\s*\{([^}]*)\}", css).group(1)
    assert "word-break: keep-all" in rule and "overflow-wrap: anywhere" in rule


def test_select_never_grows_wider_than_its_container():
    """真 Chromium 截图（390px）：drawio「我的流程图」里有一个长文件名，下拉框按最长选项撑到 615px，整页横向滚动。"""
    css = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*select\s*\{[^}]*max-width:\s*100%", css)


def test_drawio_row_actions_are_outline_buttons():
    """文件管理列表每行的「移至回收站 / 恢复 / 永久删除」原来是实心主按钮，破坏性操作成了列表里最醒目的东西。"""
    for path in (ROOT / "app/frontend/drawio-page.js", ROOT / "app/static/js/drawio-page.js"):
        text = path.read_text(encoding="utf-8")
        assert re.search(r'className\s*=\s*["`]ghost["`]', text), f"{path.name}：忘了 npm run build？"
    html = (ROOT / "app/templates/drawio.html").read_text(encoding="utf-8")
    assert re.search(r"#diagram-manage button\s*\{[^}]*margin", html)
