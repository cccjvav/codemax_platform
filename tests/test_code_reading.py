"""Reading blocks prove location/availability, never automatic semantic correctness."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import code_reading as cr  # noqa: E402


@pytest.fixture
def specimen(tmp_path):
    (tmp_path / 'sample.py').write_text('def example():\n    return 1\n', encoding='utf-8')
    (tmp_path / 'docs').mkdir()
    row = {'path': 'sample.py', 'owner': 'README.md', 'lines': 2, 'generated': False,
           'sha256': hashlib.sha256((tmp_path / 'sample.py').read_bytes()).hexdigest()}
    data = {'version': 1, 'files': [{'path': 'sample.py', 'sha256': row['sha256'], 'blocks': [
        {'end': 1, 'title': '定义入口', 'explanation': '只定义函数；导入不会执行其返回语句。'},
        {'end': 2, 'title': '返回值', 'explanation': '调用时返回整数 1，不写文件或数据库。'},
    ]}]}
    return tmp_path, row, data


def save_notes(root, data):
    (root / 'docs/code_reading_notes.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def test_contiguous_notes_do_not_claim_semantic_certification(specimen):
    root, row, data = specimen
    save_notes(root, data)
    report = cr.build_reading(root, [row])
    entry = report['files'][0]
    assert entry['state'] == 'annotated'
    assert entry['semantic_review'] == 'not_automatically_verified'
    assert [(b['start'], b['end']) for b in entry['blocks']] == [(1, 1), (2, 2)]
    assert report['note_blocks'] == 2


def test_missing_notes_stay_visible_in_inventory(specimen):
    root, row, data = specimen
    data['files'] = []
    save_notes(root, data)
    report = cr.build_reading(root, [row])
    assert report['counts'] == {'contract_only': 1}
    html = cr.render_inventory(report, {'README.md': 'd/README.html'}, {'sample.py': 's/sample.py.html'})
    assert '分段精读待补' in html and 'sample.py' in html
    assert '#reading-notes' not in html


@pytest.mark.parametrize('mutation,reason', [
    ('stale', 'stale source hash'), ('unknown', 'unknown/duplicate'), ('duplicate', 'unknown/duplicate'),
    ('reverse', 'unordered/out-of-bounds'), ('overflow', 'unordered/out-of-bounds'),
    ('boolean', 'unordered/out-of-bounds'), ('tail', 'uncovered tail'),
    ('placeholder', 'missing/placeholder'), ('empty', 'missing blocks'),
])
def test_invalid_notes_fail_instead_of_being_silently_retargeted(specimen, mutation, reason):
    root, row, data = specimen
    entry = data['files'][0]
    if mutation == 'stale':
        # Simulate a maintainer refreshing the general source manifest, but NOT reviewing notes.
        (root / 'sample.py').write_text('def example():\n    return 2\n', encoding='utf-8')
        row['sha256'] = hashlib.sha256((root / 'sample.py').read_bytes()).hexdigest()
    elif mutation == 'unknown':
        entry['path'] = '../secret.py'
    elif mutation == 'duplicate':
        data['files'].append(copy.deepcopy(entry))
    elif mutation == 'reverse':
        entry['blocks'].reverse()
    elif mutation == 'overflow':
        entry['blocks'][-1]['end'] = 3
    elif mutation == 'boolean':
        entry['blocks'][0]['end'] = True
    elif mutation == 'tail':
        entry['blocks'].pop()
    elif mutation == 'placeholder':
        entry['blocks'][0]['explanation'] = 'TODO'
    else:
        entry['blocks'] = []
    save_notes(root, data)
    with pytest.raises(ValueError, match=reason):
        cr.build_reading(root, [row])


@pytest.mark.parametrize('kind', ['generated', 'empty'])
def test_generated_and_empty_files_cannot_claim_full_annotation(specimen, kind):
    root, row, data = specimen
    row['generated'] = kind == 'generated'
    if kind == 'empty':
        row['lines'] = 0
    save_notes(root, data)
    with pytest.raises(ValueError, match='generated/empty'):
        cr.build_reading(root, [row])
    data['files'] = []
    save_notes(root, data)
    assert cr.build_reading(root, [row])['files'][0]['state'] == kind


def test_source_and_human_explanations_are_escaped(specimen):
    root, row, data = specimen
    data['files'][0]['blocks'][0]['title'] = '<script>not code</script>'
    data['files'][0]['blocks'][0]['explanation'] = '<img src=x onerror=alert(1)>'
    save_notes(root, data)
    entry = cr.build_reading(root, [row])['files'][0]
    html = cr.render_notes(entry, '<script>source</script>\n& not html\n')
    assert '<script>' not in html and '<img ' not in html
    assert '&lt;script&gt;source&lt;/script&gt;' in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert 'tabindex="0"' in html and '?end=2#L2' in html


def test_repository_notes_match_current_source_and_include_other_languages():
    from check_docs_contract import inventory

    rows, errors = inventory(ROOT)
    assert not errors
    report = cr.build_reading(ROOT, rows, require_complete=True)
    annotated = {f['path'] for f in report['files'] if f['state'] == 'annotated'}
    assert {'main.py', 'app/routers/messages.py', 'app/frontend/support-page.js',
            'app/templates/support-center.html', 'app/static/support.css',
            'database init/migrate_0007_support_messages.sql', 'tests/test_support_messages.py'} <= annotated
    assert len(report['files']) == len(rows)  # No pending file may disappear.


def test_docs_cli_stops_on_stale_notes_before_render(specimen, monkeypatch, capsys):
    import build_docs_site as bds
    import check_docs_contract as contract

    root, row, data = specimen
    data['files'][0]['sha256'] = '0' * 64
    save_notes(root, data)
    monkeypatch.setattr(bds, 'ROOT', root)
    monkeypatch.setattr(contract, 'check', lambda root: ([row], []))
    monkeypatch.setattr(sys, 'argv', ['build_docs_site.py', '--data-only'])
    assert bds.main() == 1
    assert 'stale source hash' in capsys.readouterr().err


def test_complete_mode_rejects_missing_explanations(specimen):
    root, row, data = specimen
    data['files'] = []
    save_notes(root, data)
    with pytest.raises(ValueError, match='missing explanations for sample.py'):
        cr.build_reading(root, [row], require_complete=True)


@pytest.mark.parametrize('method', ['automatic_certification', '', None, [], {}])
def test_unknown_provenance_is_rejected(specimen, method):
    root, row, data = specimen
    data['files'][0]['method'] = method
    save_notes(root, data)
    with pytest.raises(ValueError, match='unknown explanation method'):
        cr.build_reading(root, [row])


def test_guided_notes_disclose_syntax_not_semantic_certification(specimen):
    root, row, data = specimen
    data['files'][0]['method'] = 'guided'
    save_notes(root, data)
    entry = cr.build_reading(root, [row], require_complete=True)['files'][0]
    html = cr.render_notes(entry, (root / row['path']).read_text())
    assert entry['method'] == 'guided'
    assert 'AST 语句导读' in html and '不推断设计意图' in html
    assert entry['semantic_review'] == 'not_automatically_verified'


def test_notes_data_has_one_narrow_documented_self_reference_exception(specimen):
    root, row, data = specimen
    save_notes(root, data)
    note_row = {**row, 'path': 'docs/code_reading_notes.json', 'lines': 1}
    note_row['sha256'] = hashlib.sha256((root / note_row['path']).read_bytes()).hexdigest()
    report = cr.build_reading(root, [row, note_row], require_complete=True)
    assert report['counts'] == {'annotated': 1, 'note_data': 1}
    assert report['files'][1]['blocks'] == []
    html = cr.render_inventory(report, {'README.md': 'README.html'},
                               {r['path']: r['path'] + '.html' for r in [row, note_row]})
    assert '格式、来源和维护规则' in html
    # A different JSON source cannot borrow the exact-path exception.
    other = {**note_row, 'path': 'docs/other_notes.json'}
    with pytest.raises(ValueError, match='missing explanations for docs/other_notes.json'):
        cr.build_reading(root, [row, note_row, other], require_complete=True)
    data['files'].append({'path': note_row['path'], 'sha256': note_row['sha256'],
                          'blocks': [{'end': 1, 'title': '数据', 'explanation': '不允许自引用摘要。'}]})
    save_notes(root, data)
    with pytest.raises(ValueError, match='self-hashing annotation is not allowed'):
        cr.build_reading(root, [row, note_row])


def test_docs_cli_requires_complete_notes_even_in_data_only_mode(specimen, monkeypatch, capsys):
    import build_docs_site as bds
    import check_docs_contract as contract

    root, row, data = specimen
    data['files'] = []
    save_notes(root, data)
    monkeypatch.setattr(bds, 'ROOT', root)
    monkeypatch.setattr(contract, 'check', lambda root: ([row], []))
    monkeypatch.setattr(sys, 'argv', ['build_docs_site.py', '--data-only'])
    assert bds.main() == 1
    assert 'missing explanations for sample.py' in capsys.readouterr().err
