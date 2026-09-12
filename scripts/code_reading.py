"""Validate human-authored reading blocks and render an honest learning inventory.

No code execution, LLM inference or semantic certification. Unannotated source
remains visible. A source hash/range mismatch fails rather than moving prose
silently to different statements. Called by the existing docs build in CI.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from html import escape
from pathlib import Path

LABELS = {
    'annotated': '有分段精读（契约与语句导读，不是语义认证）',
    'note_data': '讲解数据本身：格式、来源和维护规则见模块说明',
    'contract_only': '源码与模块契约；分段精读待补',
    'generated': '生成物：追溯构建源，不逐段解释依赖',
    'empty': '空文件：无语句，作用见模块说明',
}


def build_reading(root: Path, sources: list[dict], *, require_complete: bool = False) -> dict:
    """Validate notes against the source inventory; return files/counts or ValueError.

    Block end lines form a contiguous partition from line 1 to EOF. The count
    measures availability of prose, not correctness or reader comprehension.
    """
    data = json.loads((root / 'docs/code_reading_notes.json').read_text(encoding='utf-8'))
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1 or not isinstance(data.get('files'), list):
        raise ValueError('reading notes: expected version 1 and files array')
    known = {row['path']: row for row in sources}
    notes = {}
    methods = {}
    for entry in data['files']:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('reading notes: each file needs a string path')
        rel = entry.get('path')
        if rel not in known or rel in notes:
            raise ValueError(f'reading notes: unknown/duplicate source {rel}')
        if rel == 'docs/code_reading_notes.json':
            raise ValueError('reading notes: self-hashing annotation is not allowed; use the schema guide')
        source = known[rel]
        if source['generated'] or not source['lines']:
            raise ValueError(f'reading notes: generated/empty source {rel} cannot claim annotation')
        digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        if entry.get('sha256') != digest or source['sha256'] != digest:
            raise ValueError(f'reading notes: stale source hash for {rel}; review prose and ranges first')
        blocks = entry.get('blocks')
        if not isinstance(blocks, list) or not blocks:
            raise ValueError(f'reading notes: missing blocks for {rel}')
        end = 0
        clean = []
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError(f'reading notes: each block must be an object in {rel}')
            next_end = block.get('end')
            if type(next_end) is not int or not end < next_end <= source['lines']:
                raise ValueError(f'reading notes: unordered/out-of-bounds range in {rel}')
            for key in ('title', 'explanation'):
                text = block.get(key)
                if not isinstance(text, str) or not text.strip() or '\ufffd' in text or text.strip() in {'TODO', 'TBD', '待补'}:
                    raise ValueError(f'reading notes: missing/placeholder {key} in {rel}')
            clean.append({'start': end + 1, 'end': next_end,
                          'title': block['title'], 'explanation': block['explanation']})
            end = next_end
        if end != source['lines']:
            raise ValueError(f'reading notes: uncovered tail in {rel}')
        method = entry.get('method', 'manual')
        if not isinstance(method, str) or method not in {'manual', 'guided'}:
            raise ValueError(f'reading notes: unknown explanation method in {rel}')
        methods[rel] = method
        notes[rel] = clean
    files = []
    for source in sources:
        rel = source['path']
        state = ('note_data' if rel == 'docs/code_reading_notes.json' else
                 'generated' if source['generated'] else 'empty' if not source['lines']
                 else 'annotated' if rel in notes else 'contract_only')
        files.append({'path': rel, 'owner': source['owner'], 'lines': source['lines'],
                      'state': state, 'label': LABELS[state], 'blocks': notes.get(rel, []),
                      'method': methods.get(rel),
                      'semantic_review': 'not_automatically_verified'})
    missing = [row['path'] for row in files if row['state'] == 'contract_only']
    if require_complete and missing:
        raise ValueError('reading notes: missing explanations for ' + ', '.join(missing))
    return {'files': files, 'counts': dict(Counter(row['state'] for row in files)),
            'note_blocks': sum(len(row['blocks']) for row in files)}


def render_notes(entry: dict, text: str) -> str:
    """Render escaped prose beside the exact numbered source; no Markdown/HTML execution."""
    if not entry['blocks']:
        return f'<p class="reading-status">{escape(entry["label"])}</p>'
    method = ('人工整理的段落说明' if entry.get('method', 'manual') == 'manual' else
              '人工整理功能契约＋AST 语句导读；语句导读只说明语法事实，不推断设计意图')
    lines = text.splitlines()
    out = ['<section id="reading-notes"><h2>分段精读</h2>',
           f'<p>{escape(method)}</p>',
           '<p>按连续逻辑块对照每一行。范围/指纹有校验，解释仍需人工复核；不是自动语义认证。</p>']
    for block in entry['blocks']:
        start, end = block['start'], block['end']
        code = '\n'.join(f'{i:>4}  {lines[i-1]}' for i in range(start, end + 1))
        out.append(f'<details class="reading-block" open><summary>L{start}–L{end} · '
                   f'{escape(block["title"])}</summary><div class="reading-grid">'
                   f'<p class="reading-explanation">{escape(block["explanation"])}</p>'
                   f'<pre tabindex="0" aria-label="源码 L{start} 到 L{end}"><code>{escape(code)}</code></pre>'
                   f'</div><a href="?end={end}#L{start}">定位完整源码中的这一段</a></details>')
    out.append('</section>')
    return '\n'.join(out)


def render_inventory(report: dict, doc_href: dict, src_href: dict) -> str:
    """List ALL inventoried source, including pending files; keep history out of certification."""
    out = ['<h1>代码精读覆盖与缺口</h1>',
           '<p>这张表统计分段说明是否存在；不统计历史报告是否正确，也不把 docstring、'
           '函数名或文件指纹当作业务解释。没有分段讲解不等于没有模块文档。</p>']
    if 'docs/CODE_READING_GUIDE.md' in doc_href:
        out.append(f'<p><a href="{escape(doc_href["docs/CODE_READING_GUIDE.md"])}">从零复盘：顺序、术语与功能链路</a></p>')
    out.append('<ul>')
    for state, label in LABELS.items():
        out.append(f'<li>{escape(label)}：{report["counts"].get(state, 0)} 个文件</li>')
    out.append('</ul><p>查找文件可用 Ctrl+F。未精读项全部保留，下方没有隐藏未完成项。</p>')
    out.append('<table><thead><tr><th>文件 / 源码</th><th>现行模块说明</th><th>精读状态</th></tr></thead><tbody>')
    for row in report['files']:
        rel, owner = row['path'], row['owner']
        href = src_href[rel] + ('#reading-notes' if row['blocks'] else '')
        contract = (f'<a href="{escape(doc_href[owner])}">{escape(owner)}</a>'
                    if owner in doc_href else escape(owner))
        out.append(f'<tr><td><a href="{escape(href)}"><code>{escape(rel)}</code></a></td>'
                   f'<td>{contract}</td><td>{escape(row["label"])}</td></tr>')
    out.append('</tbody></table>')
    return '\n'.join(out)
