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
    """iOS Safari 在 <16px 的输入框聚焦时会放大整页 —— 窄屏必须有 16px 覆盖。"""
    html = BASE.read_text(encoding="utf-8")
    mobile = html.split("@media (max-width: 700px)")[1].split("}")[0]
    assert "input, select, textarea { font-size: 16px; }" in html
    assert "max-width: 700px" in html and mobile


def test_mobile_navigation_and_footer_links_get_touchable_padding():
    """窄屏导航链接此前只有 21–24px 高，低于 WCAG 2.2 AA 的 24×24 下限。"""
    html = BASE.read_text(encoding="utf-8")
    assert "header nav a, .actions a, footer .fnav a { display: inline-block; padding: 8px 4px; }" in html


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


async def test_pages_render_one_h1_and_a_page_heading(client):
    """真实渲染：整站每页只有一个 h1（站点名）；工具页额外有可见的页面标题 h2。"""
    for path, heading in (("/tools/er", "SQL DDL 转 ER 图"), ("/tools/mermaid", "自然语言生成 UML 类图"),
                          ("/tools/drawio", "Drawio 在线流程图"), ("/admin/payments", "订单与收款管理")):
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
