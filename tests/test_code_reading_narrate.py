"""TD-291：导读维护工具 scripts/code_reading_narrate.py。

这里验证工具的**机械部分**：语句写法、块单元划分、行号映射、增删块后仍通过 code_reading 的校验。
功能契约（每块第一段）仍由人写，工具不代写，也不证明中文说明语义正确。
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import code_reading as cr  # noqa: E402
import code_reading_narrate as crn  # noqa: E402

SAMPLE = '''"""模块说明。"""
import os

LIMIT = 3


def outer(x: int) -> int:
    """外层。"""
    total = 0

    def inner(y):
        return y + 1

    for i in range(x):
        total += inner(i)
    return total


_CACHE = {}


async def fetch(client, url, *, retries: int = 2):
    try:
        r = await client.get(url)
    except (OSError, ValueError):
        raise
    async with lock():
        await asyncio.sleep(0.1)
    return r
'''


def _lines(src: str, title: str, lo: int, hi: int, *, test: bool = False) -> list[str]:
    return crn.Narrator(test=test).lines(crn.block_stmts(src, title, lo, hi))


def test_statement_phrases_follow_the_existing_notes_templates():
    src = SAMPLE
    got = _lines(src, "fetch", 21, 30)
    assert got == [
        "L23 执行try体；捕获 (OSError, ValueError)。",
        "L24 计算右侧并赋给 `r`：await 调用 client.get(url)；等待期间可让出事件循环，恢复后才执行下一句。",
        "L26 抛出 当前异常；本路径不再顺序执行后文，由匹配的except/上层处理。",
        "L27 进入 lock() 的上下文；退出时执行资源/锁/异常管理，入口和退出都可等待。",
        "L28 await 调用 asyncio.sleep(0.1)：等待计时器，不忙循环占CPU；等待期间可让出事件循环，恢复后才执行下一句。",
        "L29 结束当前函数，返回 r；不会自动commit未提交事务。",
    ]
    fn = next(n for n in ast.parse(src).body if getattr(n, "name", "") == "fetch")
    assert crn.signature(fn) == "输入签名：client, url, *, retries: int=2；异步调用需await才推进。 未写返回类型标注，以下方return为准。"


def test_test_mode_uses_assert_status_and_client_phrasing():
    src = (
        "async def test_x(client, monkeypatch):\n"
        "    monkeypatch.setattr(mod, 'X', 1)\n"
        "    r = await client.post('/a')\n"
        "    assert r.status_code == 429\n"
        "    assert r.status_code == 418\n"
        "    assert r.json() == {}\n"
        "    with pytest.raises(ValueError) as e:\n"
        "        pass\n"
    )
    got = _lines(src, "test_x", 1, 8, test=True)
    assert got[0] == "L2 调用 monkeypatch.setattr(mod, 'X', 1)：仅在本测试替换属性，fixture结束恢复。"
    assert got[1].endswith(f"：{crn.CLIENT_NOTE}{crn.AWAIT_TAIL}。")
    assert got[2] == "L4 核对 `r.status_code == 429`：要求限流拒绝；不满足即本用例失败。"
    # 状态码表里没有的码用通用写法，而不是编一个含义
    assert got[3] == "L5 核对 `r.status_code == 418`：此条件为假即测试失败；不是生产输入校验。"
    assert got[4] == "L6 核对 `r.json() == {}`：此条件为假即测试失败；不是生产输入校验。"
    assert got[5] == "L7 进入 pytest.raises(ValueError) as e（该体必须抛指定异常才通过） 的上下文；退出时执行资源/锁/异常管理。"


def test_nested_definitions_and_module_statements_are_assigned_like_the_notes():
    u, _ = crn.units(SAMPLE)
    assert sorted(u, key=lambda t: u[t][0]) == ["", "outer", "outer.inner", "fetch"]
    # 外层块只讲第一个嵌套定义之前的语句；嵌套块讲自己的体，再接外层体里其后的语句
    assert [ln.split(" ")[0] for ln in _lines(SAMPLE, "outer", 7, 10)] == ["L9"]
    assert [ln.split(" ")[0] for ln in _lines(SAMPLE, "outer.inner", 11, 20)] == ["L12", "L14", "L15", "L16", "L19"]
    # 模块级语句按块的行范围归属：同一条 _CACHE，块边界不同就归不同的块
    assert "L19" not in " ".join(_lines(SAMPLE, "fetch", 21, 30))
    assert _lines(SAMPLE, "fetch", 18, 30)[0].startswith("L19 计算右侧并赋给 `_CACHE`")
    # 模块块：导入与常量，不含 docstring
    assert [ln.split(" ")[0] for ln in _lines(SAMPLE, "", 1, 6)] == ["L2", "L4"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """一个临时 git 仓库：sample.py + 与之一致的 guided 导读，工具的 ROOT/NOTES 指向它。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "sample.py").write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(crn, "ROOT", tmp_path)
    monkeypatch.setattr(crn, "NOTES", tmp_path / "docs" / "code_reading_notes.json")
    (tmp_path / "docs" / "code_reading_notes.json").write_text(json.dumps({"version": 1, "files": []}), encoding="utf-8")
    heads = {"": "模块协作与边界：样本。", "outer": "功能契约：累加。", "outer.inner": "功能契约：加一。",
             "fetch": "功能契约：取数据。"}
    (tmp_path / "heads.json").write_text(json.dumps(heads, ensure_ascii=False), encoding="utf-8")
    assert crn.main(["init", "sample.py", "--heads", str(tmp_path / "heads.json")]) == 0
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], check=True)
    return tmp_path


def _validate(root: Path) -> dict:
    raw = (root / "sample.py").read_bytes()
    row = {"path": "sample.py", "owner": "README.md", "lines": len(raw.decode().splitlines()), "generated": False,
           "sha256": hashlib.sha256(raw).hexdigest()}
    return cr.build_reading(root, [row], require_complete=True)["files"][0]


def test_init_builds_notes_that_the_validator_accepts_and_check_reproduces(repo, capsys):
    entry = _validate(repo)
    assert entry["state"] == "annotated" and entry["method"] == "guided"
    assert [b["title"] for b in entry["blocks"]] == ["模块装配", "outer", "outer.inner", "fetch"]
    assert crn.main(["check", "sample.py"]) == 0
    assert "4/4" in capsys.readouterr().out


def test_init_refuses_to_invent_missing_contracts(repo, tmp_path):
    (repo / "other.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "partial.json").write_text(json.dumps({"": "模块。"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="a"):
        crn.main(["init", "other.py", "--heads", str(tmp_path / "partial.json")])


def test_remap_add_regen_drop_keep_the_notes_valid_after_a_source_edit(repo, capsys):
    # 在 outer 前插入两行、在文件末尾追加一个新函数：旧块的 end 与 Lnn 都要跟着移动
    src = SAMPLE.replace("LIMIT = 3\n", "LIMIT = 3\nEXTRA = 4\nMORE = 5\n") + "\n\ndef added(v):\n    return v * 2\n"
    (repo / "sample.py").write_text(src, encoding="utf-8")
    assert crn.main(["remap", "sample.py"]) == 0
    assert crn.main(["regen", "sample.py", "模块装配", "--note", "TD-X：新增两个常量。"]) == 0
    assert crn.main(["add", "sample.py", "added", "--head", "功能契约：乘二。"]) == 0
    entry = _validate(repo)
    assert [b["title"] for b in entry["blocks"]] == ["模块装配", "outer", "outer.inner", "fetch", "added"]
    capsys.readouterr()
    assert crn.main(["check", "sample.py"]) == 0, capsys.readouterr().out
    notes = json.loads((repo / "docs" / "code_reading_notes.json").read_text(encoding="utf-8"))["files"][0]["blocks"]
    assert notes[0]["explanation"].startswith("模块协作与边界：样本。 TD-X：新增两个常量。")
    assert "L6 计算右侧并赋给 `MORE`：5。" in notes[0]["explanation"]
    assert "L16 " in notes[2]["explanation"] and "L14 " not in notes[1]["explanation"]
    # 删除函数后用 drop 合并块，仍然连续有效
    (repo / "sample.py").write_text(src.replace("\n\ndef added(v):\n    return v * 2\n", "\n"), encoding="utf-8")
    assert crn.main(["drop", "sample.py", "added"]) == 0
    assert crn.main(["remap", "sample.py", "--base", "HEAD"]) == 0
    assert [b["title"] for b in _validate(repo)["blocks"]] == ["模块装配", "outer", "outer.inner", "fetch"]


# 近期用本工具维护的文件：生成结果必须与已提交的导读逐行一致。若有意手写不同写法，从名单里移除并说明。
MAINTAINED = [
    "app/tools/support.py", "app/tools/crawler.py", "app/tools/browser.py", "app/middleware.py",
    "tests/test_politeness.py", "tests/test_support.py",
]


@pytest.mark.parametrize("rel", MAINTAINED)
def test_generator_reproduces_the_committed_notes_of_maintained_files(rel, capsys):
    assert crn.main(["check", rel]) == 0, capsys.readouterr().out
