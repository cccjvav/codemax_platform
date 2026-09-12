"""Deterministic README ownership, content and source-fingerprint gate (stdlib only).

Run --write after reviewing source changes; default mode never modifies files.
The generated inventory is evidence of structure, not a semantic-review certificate.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
START = '<!-- doc-contract:files:start -->'
END = '<!-- doc-contract:files:end -->'
SECTIONS = ('模块职责', '文件与入口', '数据流与约束', '变更与验证')
EXTENSIONS = {'.py', '.js', '.mjs', '.html', '.css', '.sql', '.json', '.yaml', '.yml', '.toml', '.ini', '.xml', '.svg'}
NAMES = {'Dockerfile', 'requirements.txt', '.env.example'}


def repository_files(root: Path) -> list[str]:
    """Include untracked, non-ignored additions: adding a module cannot evade the gate."""
    result = subprocess.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=root, check=True, capture_output=True,
    )
    return sorted({p for p in result.stdout.decode().split('\0') if p and (root / p).is_file()})


def symbols(path: Path) -> list[dict]:
    """Qualified Python identities include methods and nested functions; no regex claims for JS."""
    if path.suffix != '.py':
        return []
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    found = []

    def walk(node, parents):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = '.'.join([*parents, child.name])
                found.append({'name': name, 'line': child.lineno, 'end': child.end_lineno,
                              'kind': 'class' if isinstance(child, ast.ClassDef) else 'def',
                              'async': isinstance(child, ast.AsyncFunctionDef),
                              'signature': ast.unparse(child.args) if hasattr(child, 'args') else None,
                              'returns': ast.unparse(child.returns) if getattr(child, 'returns', None) else None,
                              'source_docstring': ast.get_docstring(child)})
                walk(child, [*parents, child.name])
            else:
                walk(child, parents)
    walk(tree, [])
    return found


def inventory(root: Path) -> tuple[list[dict], list[str]]:
    policy = json.loads((root / 'docs/documentation_policy.json').read_text(encoding='utf-8'))
    sources, errors = [], []
    for rel in repository_files(root):
        path = Path(rel)
        if path.suffix == '.md':
            continue
        if (path.name.startswith('.env') and path.name != '.env.example') or path.suffix in {'.pem', '.key', '.p12'}:
            errors.append(f'{rel}: secret-like material must not be published as documentation')
            continue
        if (root / rel).is_symlink():
            errors.append(f'{rel}: symlinks need explicit ownership review, refusing to follow them')
            continue
        if path.suffix not in EXTENSIONS and path.name not in NAMES:
            try:
                content = (root / rel).read_bytes().decode('utf-8')
            except UnicodeDecodeError:
                continue  # binary asset, not readable source
            if '\x00' in content:
                continue
            # Unknown UTF-8 text is still owned. New languages cannot hide behind the suffix list.
        owner = (path.parent / 'README.md').as_posix()
        generated = False
        for rule in policy['generated']:
            if rel == rule['path'] or (rule['path'].endswith('/') and rel.startswith(rule['path'])):
                if not rule.get('reason', '').strip():
                    errors.append(f'{rel}: generated exemption needs a reason')
                owner, generated = rule['owner'], True
                break
        if not (root / owner).is_file():
            errors.append(f'{rel}: missing owner {owner}')
        sources.append({
            'path': rel, 'owner': owner, 'generated': generated,
            'sha256': hashlib.sha256((root / rel).read_bytes()).hexdigest(),
            'lines': len((root / rel).read_text(encoding='utf-8').splitlines()),
            'symbols': [] if generated else symbols(root / rel),
            'symbol_coverage': 'python_ast' if path.suffix == '.py' and not generated else 'file_only',
            'semantic_review': 'not_automatically_verified',
        })
    return sources, errors


def file_table(owner: str, rows: list[dict]) -> str:
    lines = [START, '', '| 文件（源码） | SHA-256 前 12 位 | 定位范围 |', '| --- | --- | --- |']
    for row in rows:
        href = quote(os.path.relpath(row['path'], str(Path(owner).parent)).replace(os.sep, '/'), safe='/._-')
        label = row['path'].replace('|', '&#124;')
        scope = '生成物，见模块构建说明' if row['generated'] else (f"L1–L{row['lines']}" if row['lines'] else "空文件（无源码行）")
        lines.append(f"| [`{label}`]({href}) | `{row['sha256'][:12]}` | {scope} |")
    lines += ['', '完整 SHA-256、Python 限定名与行范围由文档构建写入 `docs/site/data/code-manifest.json`。',
              '其他语言只声明文件覆盖，不把正则命中冒充完整符号解析。', '', END]
    return '\n'.join(lines)


def check(root: Path = ROOT, *, write: bool = False) -> tuple[list[dict], list[str]]:
    rows, errors = inventory(root)
    for owner in sorted({r['owner'] for r in rows}):
        path = root / owner
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8')
        human = re.sub(re.escape(START) + r'.*?' + re.escape(END), '', text, flags=re.S)
        if '\ufffd' in human:
            errors.append(f'{owner}: replacement character indicates corrupted documentation')
        for section in SECTIONS:
            marker = f'## {section}\n'
            body = human.split(marker, 1)[1].split('\n## ', 1)[0].strip() if marker in human else ''
            if not body or body.strip(' .。!！') in {'TODO', 'TBD', '待补', '待完善'}:
                errors.append(f'{owner}: missing/nonempty section {section}')
        expected = file_table(owner, [r for r in rows if r['owner'] == owner])
        if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
            errors.append(f'{owner}: exactly one ordered generated block required')
            continue
        before, rest = text.split(START, 1)
        actual, after = rest.split(END, 1)
        if START + actual + END != expected:
            if write:
                path.write_text(before + expected + after, encoding='utf-8')
            else:
                errors.append(f'{owner}: source inventory/fingerprint changed; review and run --write')
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Refresh generated tables after human/agent review')
    args = parser.parse_args()
    rows, errors = check(write=args.write)
    for error in errors:
        print(error)
    print(f'README contract: {len(rows)} files, {len({r["owner"] for r in rows})} owners, {len(errors)} errors')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
