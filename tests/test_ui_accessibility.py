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
