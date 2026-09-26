"""代码导读（docs/code_reading_notes.json）的维护工具（TD-291）。

`scripts/code_reading.py` 只负责**校验**导读与源码一致（sha256、块边界、来源类型）；
改了源码后如何更新导读，此前只靠维护者各自手写脚本，脚本不在仓库里，换人或换环境就丢。
本工具把那套做法固定下来。它只用标准库，不引入依赖。

子命令（都只处理命令行点名的文件，不会整库重生成）：

- `check PATH...`：用当前源码重新生成每个 guided 块的「AST语句导读」，与已提交的逐行比较，
  报告不一致的块。只读。手写块、旧模板写法的块会被报告为不一致，这是提示而非错误。
- `remap PATH... [--base REV]`：源码改动后，按 difflib 把块的 `end` 与正文里的 `Lnn`
  从 `REV`（默认 HEAD）版本映射到当前行号，最后一块收到文件末尾，并更新 sha256。
  只移动行号，不改写任何说明文字。
- `regen PATH TITLE... [--note TEXT]`：用当前源码重写指定块的 AST 语句导读；`--note` 会追加到
  块的第一段（功能契约）末尾。块原本没有 AST 段时会补上。
- `add PATH TITLE --head TEXT`：为 TITLE 对应的函数新建块（从 def/装饰器行开始），
  拆分它所在的原块。`--head` 是第一段（「功能契约：…」或测试的准备条件），必须由人来写。
- `drop PATH TITLE`：删除一个块，行范围并入前一块（函数被删除或合并时用）。
- `init PATH --heads FILE`：为还没有导读的新文件建 guided 条目，每个定义一块；FILE 是
  `{标题: 第一段}` 的 JSON（模块块的键为 ""），缺哪块就报哪块，不会代写。

生成的是**语法导读**：只陈述可见语句，不推断设计理由；功能契约与 TD 说明仍由人写。
写入后运行 `pytest tests/test_code_reading.py` 与 `scripts/check_docs_contract.py --write`。
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs" / "code_reading_notes.json"
AST_HDR = "AST语句导读（仅陈述可见语法与显式断言，不推断设计理由）："
BOUNDARY = "阅读边界：原注释/docstring、空行和多行表达式的续行在右侧保留；缩进决定分支归属，原注释不能盖过当前执行语句。"
AWAIT_TAIL = "；等待期间可让出事件循环，恢复后才执行下一句"
FN = (ast.FunctionDef, ast.AsyncFunctionDef)

# 只收录现有导读里已使用的状态码写法；其他状态码的断言用通用写法。
STATUS = {
    200: "请求成功", 201: "创建成功", 204: "操作成功且无正文", 400: "业务/协议拒绝",
    401: "未通过身份验证", 403: "有身份但无权执行", 404: "资源不可见或不存在",
    409: "状态/配额/幂等冲突", 412: "旧版本不得覆盖新版本", 413: "超出上限被拒",
    422: "输入形状或提取结果被拒", 428: "先提供版本条件", 429: "限流拒绝",
    502: "上游故障被明确映射", 503: "服务暂不可用而非假成功",
}

# 调用名（ast.unparse(func)）→ 插在调用文本后的说明
CALL_NOTES = {
    "asyncio.sleep": "等待计时器，不忙循环占CPU",
    "asyncio.gather": "让多个协程共同推进；返回结果列表，不保证网络完成顺序",
    "asyncio.create_task": "启动独立任务，调用者还需管理完成/取消/异常",
    "json.loads": "把外部文本解析为对象，格式错误会抛异常",
    "db.commit": "提交当前事务，之后其他事务才可见已提交变化",
    "session.commit": "提交当前事务，之后其他事务才可见已提交变化",
    "db.refresh": "从数据库重读对象，不是提交",
    "db.add": "把ORM对象加入会话，尚未完成持久提交",
    "run_in_threadpool": "把同步工作转入线程池，不占事件循环执行长计算",
    "subprocess.run": "运行外部命令并等待，returncode是否检查要看后面的断言",
    "pytest.skip": "明确跳过，不计为执行通过",
    "pytest.importorskip": "依赖缺失时跳过，不证明真实能力可用",
    "db.scalar": "执行查询并取一个标量/对象",
    "db.execute": "执行SQL表达式，读/写行为由其中的SELECT/UPDATE/DELETE决定",
    "json.dumps": "序列化成JSON字符串，不自动发送HTTP或写文件",
}
JS_RE = re.compile(r"\b(const|let|var|require|import|function)\b")
CLIENT_NOTE = "通过测试ASGI客户端调用真实路由，中间件/依赖会运行，但不建立真实浏览器或公网连接"
CLIENT_RE = re.compile(r"^(client|ac|c|http|anon|api)\.(get|post|put|patch|delete|request|head|options)$")


# ------------------------------------------------------------------ 语句 → 导读行


class Narrator:
    """把语句列表转成导读行。test=True 时断言、monkeypatch、pytest.raises 用测试写法。"""

    def __init__(self, *, test: bool, annotate: bool = True):
        self.test = test
        self.annotate = annotate

    # ---- 表达式

    def call_note(self, call: ast.Call) -> str:
        name = ast.unparse(call.func)
        if name in CALL_NOTES:
            return "：" + CALL_NOTES[name]
        if self.test and CLIENT_RE.match(name):
            return "：" + CLIENT_NOTE
        return ""

    def rhs(self, v: ast.expr) -> str:
        if isinstance(v, ast.Await):
            inner = v.value
            note = self.call_note(inner) if isinstance(inner, ast.Call) else ""
            text = ast.unparse(inner)
            return f"await 调用 {text}{note}{AWAIT_TAIL}" if isinstance(inner, ast.Call) else f"await {text}{AWAIT_TAIL}"
        if isinstance(v, ast.Call):
            return f"调用 {ast.unparse(v)}{self.call_note(v)}"
        return ast.unparse(v)

    @staticmethod
    def target(t: ast.expr) -> str:
        text = ast.unparse(t)
        return f"({text})" if isinstance(t, ast.Tuple) and not text.startswith("(") else text

    # ---- 语句

    def lines(self, stmts: list[ast.stmt]) -> list[str]:
        out: list[str] = []
        self._stmts(stmts, out)
        return out

    def _stmts(self, stmts, out, nested: bool = False):
        for s in stmts:
            self._stmt(s, out, nested)

    def _stmt(self, s: ast.stmt, out: list[str], nested: bool = False) -> None:  # noqa: C901 - 一条语句一个分支，拆开反而难读
        L = f"L{s.lineno} "
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
            return  # docstring / 独立字符串
        if isinstance(s, FN + (ast.ClassDef,)):
            if nested:  # 定义在 if/for/try/with 里：units 不会给它建块，不能静默漏讲
                raise NotImplementedError(f"复合语句内的定义 {s.name} at L{s.lineno}")
            return  # 函数体/类体里直接嵌套的定义各有自己的块（units 负责登记）
        if isinstance(s, ast.Assign):
            v = s.value
            names = ", ".join(self.target(t) for t in s.targets)
            if self.test and isinstance(v, ast.Constant) and isinstance(v.value, str) and v.end_lineno > v.lineno:
                kind = "测试JavaScript" if JS_RE.search(v.value) else "HTML模板/测试输入"
                out.append(L + f"把{kind}多行字面量存入 `{names}`（{len(v.value)}字符）；此时只是数据，只有后面的执行/解析调用才运行。"
                           "完整内容见源码；不以这段字符串存在作为测试通过证据。")
            else:
                out.append(L + f"计算右侧并赋给 `{names}`：{self.rhs(v)}。")
        elif isinstance(s, ast.AnnAssign):
            tgt, ann = ast.unparse(s.target), ast.unparse(s.annotation)
            if s.value is None:
                out.append(L + f"声明字段 `{tgt}` 的类型 {ann}；仅类型标注，不执行数据库写入。")
            elif self.annotate:
                out.append(L + f"计算右侧并赋给 `{tgt}`（标注 {ann}）：{self.rhs(s.value)}。")
            else:
                out.append(L + f"计算右侧并赋给 `{tgt}`：{self.rhs(s.value)}。")
        elif isinstance(s, ast.AugAssign):
            out.append(L + f"累积修改 `{ast.unparse(s.target)}`：执行 `{ast.unparse(s)}`，新值供下一轮/后续分支使用。")
        elif isinstance(s, ast.Return):
            val = "None" if s.value is None else self.rhs(s.value)
            out.append(L + f"结束当前函数，返回 {val}；不会自动commit未提交事务。")
        elif isinstance(s, ast.If):
            tail = "为真进入当前缩进分支，为假进入else/elif。" if s.orelse else "为真进入当前缩进分支；不成立则跳过该体继续后文。"
            out.append(L + f"判断 `{ast.unparse(s.test)}`，{tail}")
            self._stmts(s.body, out, nested=True)
            self._stmts(s.orelse, out, nested=True)
        elif isinstance(s, (ast.For, ast.AsyncFor)):
            tail = "异步迭代每轮可能等待。" if isinstance(s, ast.AsyncFor) else "循环体按缩进执行，break可提前结束。"
            out.append(L + f"逐项遍历 `{ast.unparse(s.iter)}`，绑定 `{self.target(s.target)}`；{tail}")
            self._stmts(s.body, out, nested=True)
            self._stmts(s.orelse, out, nested=True)
        elif isinstance(s, ast.While):
            out.append(L + f"只要 `{ast.unparse(s.test)}` 为真就重复该体，注意体内推进索引/退出条件。")
            self._stmts(s.body, out, nested=True)
            self._stmts(s.orelse, out, nested=True)
        elif isinstance(s, ast.Try) or type(s).__name__ == "TryStar":
            names = "、".join(ast.unparse(h.type) for h in s.handlers if h.type is not None)
            if not s.handlers:
                head = "执行try体；捕获 未配置except，异常向上传播"
            else:
                head = f"执行try体；捕获 {names or '所有异常'}"
            head += "；finally无论成功失败都会清理。" if s.finalbody else "。"
            out.append(L + head)
            self._stmts(s.body, out, nested=True)
            for h in s.handlers:
                self._stmts(h.body, out, nested=True)
            self._stmts(s.orelse, out, nested=True)
            self._stmts(s.finalbody, out, nested=True)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            items = ", ".join(self._with_item(i) for i in s.items)
            tail = "，入口和退出都可等待。" if isinstance(s, ast.AsyncWith) else "。"
            out.append(L + f"进入 {items} 的上下文；退出时执行资源/锁/异常管理{tail}")
            self._stmts(s.body, out, nested=True)
        elif isinstance(s, ast.Raise):
            if s.exc is None:
                out.append(L + "抛出 当前异常；本路径不再顺序执行后文，由匹配的except/上层处理。")
                return
            cause = ""
            if isinstance(s.cause, ast.Constant):
                cause = "，并用 from None 隐藏内部异常链"
            elif s.cause is not None:
                cause = f"，显式保留原因 {ast.unparse(s.cause)}"
            out.append(L + f"抛出 {ast.unparse(s.exc)}{cause}；本路径不再顺序执行后文，由匹配的except/上层处理。")
        elif isinstance(s, ast.Assert):
            out.append(L + self._assert(s))
        elif isinstance(s, (ast.Import, ast.ImportFrom)):
            out.append(L + f"导入 {ast.unparse(s)}；导入模块顶层可能初始化对象，但不因此运行每个函数体。")
        elif isinstance(s, ast.Global):
            out.append(L + f"global 指定后续赋值修改模块状态：{', '.join(s.names)}；多请求共享，需按模块并发规则使用。")
        elif isinstance(s, ast.Nonlocal):
            out.append(L + f"nonlocal 修改外层闭包状态：{', '.join(s.names)}；不是本函数新建同名局部变量。")
        elif isinstance(s, ast.Continue):
            out.append(L + "continue 跳过本轮剩余语句，进入下一轮。")
        elif isinstance(s, ast.Break):
            out.append(L + "break 提前退出最近一层循环，不退出整个调用栈。")
        elif isinstance(s, ast.Pass):
            out.append(L + "pass 占位，不执行任何操作。")
        elif isinstance(s, ast.Delete):
            out.append(L + f"删除引用/键：{ast.unparse(s)}；具体数据是否删除取决于目标，不是自动SQL DELETE。")
        elif isinstance(s, ast.Expr):
            v = s.value
            if isinstance(v, (ast.Yield, ast.YieldFrom)):
                val = "None" if v.value is None else ast.unparse(v.value)
                out.append(L + f"yield 交出 {val}；生成器下次恢复才继续后续清理。")
            elif self.test and isinstance(v, ast.Call) and ast.unparse(v.func) == "monkeypatch.setattr":
                out.append(L + f"调用 {ast.unparse(v)}：仅在本测试替换属性，fixture结束恢复。")
            elif self.test and isinstance(v, ast.Call) and ast.unparse(v.func) == "monkeypatch.setenv":
                out.append(L + f"调用 {ast.unparse(v)}：仅替换本测试进程环境变量。")
            elif isinstance(v, ast.Await) and not isinstance(v.value, ast.Call):
                out.append(L + f"求值表达式 {ast.unparse(v)}；结果未被保存。")
            else:
                out.append(L + self.rhs(v) + "。")
        else:
            raise NotImplementedError(f"{type(s).__name__} at L{s.lineno}")

    def _with_item(self, item: ast.withitem) -> str:
        text = ast.unparse(item.context_expr)
        if item.optional_vars is not None:
            text += f" as {ast.unparse(item.optional_vars)}"
        if ast.unparse(item.context_expr).startswith("pytest.raises"):
            text += "（该体必须抛指定异常才通过）"
        return text

    def _assert(self, s: ast.Assert) -> str:
        t = s.test
        if (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq)
                and ast.unparse(t.left).endswith("status_code")
                and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value in STATUS):
            return f"核对 `{ast.unparse(t)}`：要求{STATUS[t.comparators[0].value]}；不满足即本用例失败。"
        if (isinstance(t, ast.BoolOp) and isinstance(t.op, ast.And) and isinstance(t.values[0], ast.Compare)
                and ast.unparse(t.values[0].left).endswith("status_code") and isinstance(t.values[0].ops[0], ast.Eq)
                and isinstance(t.values[0].comparators[0], ast.Constant) and t.values[0].comparators[0].value in STATUS):
            return f"核对 `{ast.unparse(t)}`：要求{STATUS[t.values[0].comparators[0].value]}；不满足即本用例失败。"
        return f"核对 `{ast.unparse(t)}`：此条件为假即测试失败；不是生产输入校验。"


# ------------------------------------------------------------------ 源码 → 块单元


def _body_wo_doc(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _start(n) -> int:
    return n.decorator_list[0].lineno if getattr(n, "decorator_list", None) else n.lineno


def units(src: str) -> tuple[dict[str, tuple[int, ast.AST, list[ast.stmt]]], list[ast.stmt]]:
    """返回 (标题 → (起始行, 定义节点, 该定义自己的语句), 模块级非定义语句)。

    约定与现有导读一致：定义块导读自身函数体里、第一个嵌套定义之前的语句；嵌套定义块导读
    自己的函数体，再接外层体里它之后、下一个嵌套定义之前的语句。类块导读类体里第一个方法之前的语句。
    模块级语句（导入、常量等）不固定跟随哪个定义，而是按**块的行范围**归属，见 `block_stmts`。
    """
    tree = ast.parse(src)
    out: dict[str, tuple[int, ast.AST, list[ast.stmt]]] = {"": (1, tree, [])}

    def split(prefix: str, node):
        body = _body_wo_doc(node.body)
        idx = [i for i, s in enumerate(body) if isinstance(s, FN + (ast.ClassDef,))]
        out[prefix] = (_start(node), node, body[: idx[0]] if idx else body)
        for k, i in enumerate(idx):
            sub = body[i]
            rest = body[i + 1: idx[k + 1]] if k + 1 < len(idx) else body[i + 1:]
            split(f"{prefix}.{sub.name}", sub)
            st, nd, own = out[f"{prefix}.{sub.name}"]
            if not any(isinstance(x, FN + (ast.ClassDef,)) for x in _body_wo_doc(sub.body)):
                out[f"{prefix}.{sub.name}"] = (st, nd, own + rest)
            else:  # 子定义自己还有嵌套：外层剩余语句接在它最后一个嵌套块后
                last = max((t for t in out if t.startswith(f"{prefix}.{sub.name}.")), key=lambda t: out[t][0])
                a, b, c = out[last]
                out[last] = (a, b, c + rest)

    toplevel = []
    for n in tree.body:
        if isinstance(n, FN + (ast.ClassDef,)):
            split(n.name, n)
        elif not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)):
            toplevel.append(n)
    return out, toplevel


def block_stmts(src: str, title: str, lo: int, hi: int) -> list[ast.stmt] | None:
    """块 [lo, hi] 要导读的语句：该定义自己的语句 + 落在块行范围里的模块级语句，按行号排序。"""
    u, toplevel = units(src)
    if title not in u:
        return None
    own = u[title][2]
    return sorted(own + [t for t in toplevel if lo <= t.lineno <= hi], key=lambda s: s.lineno)


def block_range(f: dict, i: int) -> tuple[int, int]:
    return (1 if i == 0 else f["blocks"][i - 1]["end"] + 1), f["blocks"][i]["end"]


def signature(fn, *, empty: str = "") -> str:
    a = fn.args
    parts = []
    pos = a.posonlyargs + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for arg, d in zip(pos, defaults, strict=True):
        p = arg.arg + (f": {ast.unparse(arg.annotation)}" if arg.annotation else "")
        parts.append(p + (f"={ast.unparse(d)}" if d is not None else ""))
    if a.vararg:
        parts.append("*" + a.vararg.arg + (f": {ast.unparse(a.vararg.annotation)}" if a.vararg.annotation else ""))
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults, strict=True):
        p = arg.arg + (f": {ast.unparse(arg.annotation)}" if arg.annotation else "")
        parts.append(p + (f"={ast.unparse(d)}" if d is not None else ""))
    if a.kwarg:
        parts.append("**" + a.kwarg.arg + (f": {ast.unparse(a.kwarg.annotation)}" if a.kwarg.annotation else ""))
    params = ", ".join(parts) or empty
    run = "异步调用需await才推进。" if isinstance(fn, ast.AsyncFunctionDef) else "定义函数不等于此处立刻执行函数体。"
    ret = f"返回类型标注：{ast.unparse(fn.returns)}。" if fn.returns is not None else "未写返回类型标注，以下方return为准。"
    return f"输入签名：{params}；{run} {ret}"


# ------------------------------------------------------------------ 导读 JSON 读写


def load() -> dict:
    return json.loads(NOTES.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    NOTES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def entry(data: dict, rel: str) -> dict:
    for f in data["files"]:
        if f["path"] == rel:
            return f
    raise SystemExit(f"导读里没有 {rel}")


def ast_section(explanation: str) -> list[str] | None:
    if AST_HDR not in explanation:
        return None
    body = explanation.partition(AST_HDR + "\n")[2]
    return body.split("\n\n")[0].split("\n")


def replace_section(explanation: str, lines: list[str]) -> str:
    """替换（或插入）AST 段；lines 为空时删除 AST 段。"""
    paras = explanation.split("\n\n")
    idx = next((i for i, p in enumerate(paras) if p.startswith(AST_HDR)), None)
    new = AST_HDR + "\n" + "\n".join(lines) if lines else None
    if idx is not None:
        if new:
            paras[idx] = new
        else:
            del paras[idx]
    elif new:
        at = next((i for i, p in enumerate(paras) if p.startswith("阅读边界")), len(paras))
        paras.insert(at, new)
    return "\n\n".join(paras)


def narrator_for(rel: str, f: dict | None = None) -> Narrator:
    test = rel.startswith("tests/")
    annotate = True
    if f is not None:  # 沿用该文件已有的注解写法（少数文件的块不带「（标注 …）」）
        text = "\n".join(b["explanation"] for b in f["blocks"])
        if "（标注 " not in text and re.search(r"^L\d+ 计算右侧并赋给", text, re.M):
            src = (ROOT / rel).read_text(encoding="utf-8")
            if any(isinstance(n, ast.AnnAssign) and n.value is not None for n in ast.walk(ast.parse(src))):
                annotate = False
    return Narrator(test=test, annotate=annotate)


def _title_key(f: dict, i: int) -> str:
    return "" if i == 0 else f["blocks"][i]["title"]


def generated(rel: str, f: dict, i: int) -> list[str] | None:
    stmts = block_stmts((ROOT / rel).read_text(encoding="utf-8"), _title_key(f, i), *block_range(f, i))
    return None if stmts is None else narrator_for(rel, f).lines(stmts)


# ------------------------------------------------------------------ 子命令


def cmd_check(paths: list[str]) -> int:
    data = load()
    bad = 0
    for rel in paths:
        f = entry(data, rel)
        ok = total = 0
        for i, b in enumerate(f["blocks"]):
            have = ast_section(b["explanation"])
            if have is None:
                continue
            try:
                want = generated(rel, f, i)
            except NotImplementedError as e:
                total += 1
                print(f"  {rel} :: {b['title']}: 生成器不支持 {e}")
                continue
            if want is None:
                continue
            total += 1
            if want == have:
                ok += 1
            else:
                bad += 1
                diff = [ln for ln in difflib.unified_diff(have, want, lineterm="", n=0) if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]
                print(f"  {rel} :: {b['title']}: 与当前源码生成结果不同")
                for ln in diff[:6]:
                    print("      " + ln[:160])
        print(f"{rel}: {ok}/{total} 个带 AST 段的块与生成结果一致")
    return 1 if bad else 0


def _mapper(old: list[str], new: list[str]) -> dict[int, int]:
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    m: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for k in range(i1, i2):
            m[k + 1] = (j1 + (k - i1) + 1) if tag == "equal" else max(j2, 1)
    return m


def _shift_lines(explanation: str, m: dict[int, int]) -> str:
    """把说明里行首的 `Lnn ` 按映射改成新行号；映射里没有的行号保持不变。"""
    return re.sub(r"(?m)^L(\d+) ", lambda mm: f"L{m.get(int(mm.group(1)), int(mm.group(1)))} ", explanation)


def cmd_remap(paths: list[str], base: str) -> int:
    data = load()
    for rel in paths:
        f = entry(data, rel)
        old = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        raw = (ROOT / rel).read_bytes()
        new = raw.decode("utf-8").splitlines()
        m = _mapper(old, new)
        n = len(f["blocks"])
        for i, b in enumerate(f["blocks"]):
            b["end"] = len(new) if i == n - 1 else m.get(b["end"], b["end"])
            b["explanation"] = _shift_lines(b["explanation"], m)
        f["sha256"] = hashlib.sha256(raw).hexdigest()
        print(f"{rel}: {len(old)} → {len(new)} 行，{n} 个块已映射")
    save(data)
    return 0


def _block_index(f: dict, title: str) -> int:
    for i, b in enumerate(f["blocks"]):
        if b["title"] == title or (i == 0 and title == ""):
            return i
    raise SystemExit(f"{f['path']} 没有标题为 {title!r} 的块")


def cmd_regen(rel: str, titles: list[str], note: str | None) -> int:
    data = load()
    f = entry(data, rel)
    for title in titles:
        i = _block_index(f, title)
        lines = generated(rel, f, i)
        if lines is None:
            raise SystemExit(f"{rel} 的源码里找不到 {title}")
        b = f["blocks"][i]
        b["explanation"] = replace_section(b["explanation"], lines)
        if note:
            head, sep, rest = b["explanation"].partition("\n\n")
            b["explanation"] = head + " " + note.strip() + sep + rest
    f["sha256"] = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
    save(data)
    return 0


def compose(head: str, node, lines: list[str]) -> str:
    """一块完整说明：人写的第一段 + 登记/装饰 + 输入签名 + AST 语句导读 + 阅读边界。"""
    parts = [head.strip()]
    if isinstance(node, FN):
        if node.decorator_list:
            parts.append(f"登记/装饰：{ast.unparse(node.decorator_list[0])}。pytest参数化会展开用例，fixture负责准备/恢复；路由装饰器则登记HTTP入口。")
        parts.append(signature(node))
    if lines:
        parts.append(AST_HDR + "\n" + "\n".join(lines))
    parts.append(BOUNDARY)
    return "\n\n".join(parts)


def cmd_init(rel: str, heads_file: str) -> int:
    """为尚无导读的新文件建 guided 条目：每个定义一块，第一段取自 heads JSON（标题 → 文字，模块块用 ""）。"""
    data = load()
    if any(f["path"] == rel for f in data["files"]):
        raise SystemExit(f"{rel} 已有导读；改用 regen / add")
    heads = json.loads(Path(heads_file).read_text(encoding="utf-8"))
    src = (ROOT / rel).read_text(encoding="utf-8")
    u, _ = units(src)
    order = sorted(u, key=lambda t: (u[t][0], t))
    missing = [t or "(模块)" for t in order if t not in heads]
    if missing:
        raise SystemExit("heads 缺少这些块的第一段（需人工撰写）：" + "、".join(missing))
    n_lines = len(src.splitlines())
    nar = narrator_for(rel)
    blocks = []
    for k, t in enumerate(order):
        lo = u[t][0]
        hi = u[order[k + 1]][0] - 1 if k + 1 < len(order) else n_lines
        lines = nar.lines(block_stmts(src, t, lo, hi))
        blocks.append({"end": hi, "title": heads.get("__module_title__", "模块装配") if t == "" else t,
                       "explanation": compose(heads[t], u[t][1], lines)})
    data["files"].append({"path": rel, "sha256": hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(),
                          "method": "guided", "blocks": blocks})
    save(data)
    print(f"{rel}: 新建 {len(blocks)} 个块")
    return 0


def cmd_add(rel: str, title: str, head: str) -> int:
    data = load()
    f = entry(data, rel)
    if any(b["title"] == title for b in f["blocks"]):
        raise SystemExit(f"{rel} 已有块 {title}")
    src = (ROOT / rel).read_text(encoding="utf-8")
    u, _ = units(src)
    if title not in u:
        raise SystemExit(f"{rel} 的源码里找不到 {title}")
    start, node, _own = u[title]
    i = next(k for k, b in enumerate(f["blocks"]) if b["end"] >= start)
    prev = f["blocks"][i]
    stmts = block_stmts(src, title, start, prev["end"])
    new = {"end": prev["end"], "title": title, "explanation": compose(head, node, narrator_for(rel, f).lines(stmts))}
    prev["end"] = start - 1
    f["blocks"].insert(i + 1, new)
    # 被拆分的原块只保留 start 之前的语句
    if ast_section(prev["explanation"]) is not None:
        prev_lines = [ln for ln in (ast_section(prev["explanation"]) or []) if int(ln.split(" ", 1)[0][1:]) < start]
        prev["explanation"] = replace_section(prev["explanation"], prev_lines)
    f["sha256"] = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
    save(data)
    return 0


def cmd_drop(rel: str, title: str) -> int:
    data = load()
    f = entry(data, rel)
    i = _block_index(f, title)
    if i == 0:
        raise SystemExit("不能删除第一个块")
    f["blocks"][i - 1]["end"] = f["blocks"][i]["end"]
    del f["blocks"][i]
    save(data)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("paths", nargs="+")
    r = sub.add_parser("remap")
    r.add_argument("paths", nargs="+")
    r.add_argument("--base", default="HEAD")
    g = sub.add_parser("regen")
    g.add_argument("path")
    g.add_argument("titles", nargs="+")
    g.add_argument("--note")
    a = sub.add_parser("add")
    a.add_argument("path")
    a.add_argument("title")
    a.add_argument("--head", required=True)
    d = sub.add_parser("drop")
    d.add_argument("path")
    d.add_argument("title")
    n = sub.add_parser("init")
    n.add_argument("path")
    n.add_argument("--heads", required=True)
    ns = p.parse_args(argv)
    if ns.cmd == "check":
        return cmd_check(ns.paths)
    if ns.cmd == "remap":
        return cmd_remap(ns.paths, ns.base)
    if ns.cmd == "regen":
        return cmd_regen(ns.path, ns.titles, ns.note)
    if ns.cmd == "add":
        return cmd_add(ns.path, ns.title, ns.head)
    if ns.cmd == "init":
        return cmd_init(ns.path, ns.heads)
    return cmd_drop(ns.path, ns.title)


if __name__ == "__main__":
    sys.exit(main())
