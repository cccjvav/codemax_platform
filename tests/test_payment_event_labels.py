"""订单管理页事件历史的中文名称（TD-323 / ROADMAP R-09）。

`payments-admin.js` 的 `EVENT_LABELS` 必须与后端实际写入的事件种类**完全一致**：后端新增一种而这里没登记，
管理员看到的是原值；这里留着已经不存在的种类，说明名称表跟代码脱节了。两种情况都让本测试失败。

后端种类不是手抄一份清单比对，而是从源码的语法树里找：每一处 `PaymentEvent(kind=...)`，以及把参数原样
传给 `kind=` 的辅助函数（`observation(...)`）的每一处调用。`kind` 可以是字面量、模块常量（含从别的模块导入的）、
条件表达式、同一函数里赋过值的局部变量，或 `'query_' + result.state.lower()` 这种前缀加渠道状态的拼接 ——
拼接的状态取自 `wechat_pay.py` 里对应查询函数允许的状态元组。认不出的写法直接失败，而不是悄悄漏掉。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
# 前缀拼接的种类：前缀 → wechat_pay 里校验该状态的函数（退款查询的应答由 query_full_refund 交给 parse_full_refund 校验）
DYNAMIC_PREFIXES = {"query_": "query_order", "refund_query_": "parse_full_refund"}
# 只有这三种是成功凭证，别的名称里不能出现「成功」（「不是成功」除外）
SUCCESS_KINDS = {"query_success", "refund_query_success", "refund_manual_success"}


def _allowed_states(function: str) -> set[str]:
    """wechat_pay 里 `if state not in (...): raise` 的状态元组。"""
    tree = ast.parse((APP / "wechat_pay.py").read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == function:
            for node in ast.walk(fn):
                if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "state"
                        and isinstance(node.ops[0], ast.NotIn) and isinstance(node.comparators[0], ast.Tuple)):
                    return {elt.value for elt in node.comparators[0].elts}
    raise AssertionError(f"wechat_pay.{function} 里找不到 `state not in (...)` 的状态校验")


def _module_constants(path: Path, tree: ast.Module) -> dict[str, str]:
    """模块顶层的字符串常量，加上 `from .x import NAME` 导入的字符串常量。"""
    consts: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            consts[node.targets[0].id] = node.value.value
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            base = path.parent
            for _ in range(node.level - 1):
                base = base.parent
            source = base.joinpath(*node.module.split(".")).with_suffix(".py")
            if not source.is_file():
                continue
            other = ast.parse(source.read_text(encoding="utf-8"))
            imported = {
                n.targets[0].id: n.value.value for n in other.body
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
            }
            for alias in node.names:
                if alias.name in imported:
                    consts[alias.asname or alias.name] = imported[alias.name]
    return consts


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _resolve(expr: ast.AST, scope, consts: dict[str, str], where: str) -> set[str]:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}
    if isinstance(expr, ast.IfExp):
        return _resolve(expr.body, scope, consts, where) | _resolve(expr.orelse, scope, consts, where)
    if isinstance(expr, ast.Name):
        if expr.id in consts:
            return {consts[expr.id]}
        values = [
            node.value for node in ast.walk(scope) if scope is not None
            and isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == expr.id for t in node.targets)
        ] if scope is not None else []
        assert values, f"{where}：认不出 kind 变量 {expr.id!r} 的取值"
        return set().union(*(_resolve(v, scope, consts, where) for v in values))
    if (isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add)
            and isinstance(expr.left, ast.Constant) and isinstance(expr.left.value, str)):
        prefix = expr.left.value
        assert prefix in DYNAMIC_PREFIXES, f"{where}：新的拼接前缀 {prefix!r}，请在 DYNAMIC_PREFIXES 里登记它的状态来源"
        return {prefix + state.lower() for state in _allowed_states(DYNAMIC_PREFIXES[prefix])}
    raise AssertionError(f"{where}：认不出的 kind 写法 {ast.unparse(expr)}")


def backend_event_kinds() -> set[str]:
    kinds: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = _module_constants(path, tree)
        parents = _parents(tree)
        helpers: dict[str, tuple[int, str]] = {}  # 把参数原样当 kind 的函数：名字 → (参数位置, 参数名)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "PaymentEvent"):
                continue
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            kind = next((k.value for k in node.keywords if k.arg == "kind"), None)
            assert kind is not None, f"{where}：PaymentEvent 没有用 kind= 关键字参数"
            fn = _enclosing_function(node, parents)
            params = [a.arg for a in fn.args.args] if fn is not None else []
            if isinstance(kind, ast.Name) and kind.id in params and kind.id not in consts:
                helpers[fn.name] = (params.index(kind.id), kind.id)
            else:
                kinds |= _resolve(kind, fn, consts, where)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in helpers:
                index, name = helpers[node.func.id]
                where = f"{path.relative_to(ROOT)}:{node.lineno}"
                arg = next((k.value for k in node.keywords if k.arg == name), None)
                if arg is None:
                    assert len(node.args) > index, f"{where}：调用 {node.func.id} 没有传 {name}"
                    arg = node.args[index]
                kinds |= _resolve(arg, _enclosing_function(node, parents), consts, where)
    return kinds


def frontend_event_labels() -> dict[str, str]:
    source = (APP / "frontend" / "payments-admin.js").read_text(encoding="utf-8")
    block = re.search(r"const EVENT_LABELS = Object\.freeze\(\{(.*?)\}\);", source, re.S)
    assert block, "payments-admin.js 里找不到 EVENT_LABELS"
    return dict(re.findall(r'^\s*([a-z_]+): "([^"]+)",?$', block.group(1), re.M))


def test_backend_scan_finds_the_known_shapes():
    """扫描器自身的健全性：每种写法各有一个已知种类被找到，总数与 R-09 逐一核对的 35 种一致。"""
    kinds = backend_event_kinds()
    for kind in ("prepay_started",            # 字面量
                 "channel_close_unknown",     # 模块常量 + 条件表达式
                 "operator_review",           # 从 payment_review 导入的常量
                 "query_notpay",              # 局部变量 = 前缀 + 渠道状态
                 "refund_query_processing",   # 辅助函数参数里的前缀拼接
                 "refund_verify_started"):    # 模块级辅助函数，kind 是第二个参数
        assert kind in kinds, kind
    assert len(kinds) == 35, sorted(kinds)


def test_every_backend_event_kind_has_exactly_one_chinese_label():
    kinds, labels = backend_event_kinds(), frontend_event_labels()
    assert not kinds - set(labels), f"后端会写入、名称表里没有：{sorted(kinds - set(labels))}"
    assert not set(labels) - kinds, f"名称表里有、后端已不再写入：{sorted(set(labels) - kinds)}"


def test_only_success_evidence_is_called_success():
    """R-09 的措辞规则：只有成功凭证能写「成功」；已验签但不是成功的观察必须写明「不是成功」。"""
    labels = frontend_event_labels()
    for kind, label in labels.items():
        if kind not in SUCCESS_KINDS:
            assert "成功" not in label.replace("不是成功", ""), f"{kind} 不是成功凭证，名称却像成功：{label}"
    assert "已支付" in labels["query_success"] and "退款成功" in labels["refund_manual_success"]
    assert "确认成功" in labels["refund_query_success"]
    for kind in ("refund_query_closed", "refund_query_processing", "refund_query_abnormal", "refund_send_observed"):
        assert "不是成功" in labels[kind], kind
    assert len(set(labels.values())) == len(labels), "两种事件用了同一个名称，管理员分不出来"


def test_event_rows_keep_their_line_breaks():
    """每条事件由脚本用换行分成「时间 · 事件 · 操作人 / 尝试号 / 依据」三行（TD-323）。原来 li 没有 pre-line，
    三行挤成一段，操作人、尝试号和依据 JSON 连在一起（真 Chromium 截图核对）。"""
    html = (APP / "templates" / "payments-admin.html").read_text(encoding="utf-8")
    assert re.search(r"#finance-events li\s*\{[^}]*white-space:\s*pre-line", html)
    source = (APP / "frontend" / "payments-admin.js").read_text(encoding="utf-8")
    assert "${labelled(EVENT_LABELS, event.kind)} · ${event.actor || \"系统\"}\\n尝试 ${event.attempt_id}\\n" in source
