"""TD-267 / ROADMAP O-08：工作流供应链的静态守卫。

三条只读检查，全部针对仓库里的 YAML 文本，不访问网络：

1. 每个第三方 `uses:` 都钉到 40 位 commit SHA，并带 `# vX.Y.Z` 注释——可变标签会随上游 force-push
   移动（tj-actions/changed-files 事件），SHA 不会。升级时同时改 SHA 与注释，用
   `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` 核对。
2. 顶层 `permissions` 只有 `contents: read`；`pull-requests: write` 只出现在真正发 PR 评论的
   两个 test job 上，lint/audit/docs/frontend 拿不到写权限。
3. 第三方依赖的两个锁：`requirements.txt` 每个包都是 `==` 精确版本（漏洞扫描才有意义），
   `package-lock.json` 每个包都带 `integrity`（`npm ci` 才会校验）。

它不证明上游 SHA 本身可信，也不替代 pip-audit / 漂移检查 job。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)(?:\s+#\s*(\S+))?\s*$", re.M)


def test_workflows_exist():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "agnes-connectivity.yml"}


def test_every_third_party_action_is_pinned_to_a_full_sha_with_version_comment():
    offenders = []
    for path in WORKFLOWS:
        for match in USES.finditer(path.read_text(encoding="utf-8")):
            ref, comment = match.group(1), match.group(2)
            if ref.startswith("./"):
                continue  # 本仓库内的本地 action 随代码一起评审
            owner_repo, _, version = ref.partition("@")
            if not re.fullmatch(r"[0-9a-f]{40}", version):
                offenders.append(f"{path.name}: {ref} 不是 40 位 commit SHA")
            if not comment or not re.fullmatch(r"v\d+(\.\d+){1,2}", comment):
                offenders.append(f"{path.name}: {ref} 缺少 `# vX.Y.Z` 版本注释")
    assert offenders == [], "\n".join(offenders)


def test_pins_are_the_same_sha_for_the_same_action_across_workflows():
    """同一个 action 在所有工作流里必须是同一个 SHA：升级漏改一处就会出现两个版本并存。"""
    seen: dict[str, set[str]] = {}
    for path in WORKFLOWS:
        for match in USES.finditer(path.read_text(encoding="utf-8")):
            owner_repo, _, version = match.group(1).partition("@")
            seen.setdefault(owner_repo, set()).add(version)
    mixed = {k: v for k, v in seen.items() if len(v) > 1}
    assert mixed == {}, mixed


def test_ci_write_permission_is_scoped_to_the_commenting_jobs_only():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert ci["permissions"] == {"contents": "read"}, "顶层只允许只读"
    commenting = {name for name, job in ci["jobs"].items()
                  if any("gh pr comment" in (step.get("run") or "") for step in job.get("steps", []))}
    assert commenting == {"test-sqlite", "test-postgres"}, commenting
    for name, job in ci["jobs"].items():
        perms = job.get("permissions")
        if name in commenting:
            assert perms == {"contents": "read", "pull-requests": "write"}, f"{name}: {perms}"
        else:
            assert perms is None or set(perms.values()) <= {"read"}, f"{name} 不该有写权限：{perms}"


def test_agnes_workflow_stays_read_only_and_manual():
    wf = yaml.safe_load((ROOT / ".github/workflows/agnes-connectivity.yml").read_text(encoding="utf-8"))
    assert wf["permissions"] == {"contents": "read"}
    triggers = wf.get("on") or wf.get(True)  # PyYAML 把裸 `on:` 解析成 True
    assert "workflow_dispatch" in triggers, "带密钥的探测必须由人手动触发，或 push 特定分支的本文件"


def test_python_requirements_are_exactly_pinned():
    loose = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        spec = line.split("#", 1)[0].strip()
        if not spec:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?==[0-9][A-Za-z0-9.+_\-]*", spec):
            loose.append(spec)
    assert loose == [], f"这些依赖没有 == 精确钉版本：{loose}"


def test_npm_lockfile_has_integrity_for_every_package():
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert lock.get("lockfileVersion", 0) >= 2, "需要 lockfileVersion ≥ 2 的 packages 表"
    missing = [name for name, meta in lock["packages"].items() if name and not meta.get("integrity") and not meta.get("link")]
    assert missing == [], f"这些 npm 包缺 integrity，npm ci 无法校验：{missing}"


def test_ci_installs_frontend_with_npm_ci_not_install():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "npm ci" in ci and not re.search(r"\bnpm install\b", ci), "CI 必须用 npm ci 按锁文件安装"
