"""README contract negative cases; fixtures use real temporary Git repositories."""
import json
import subprocess

import pytest

from scripts import check_docs_contract as gate


@pytest.fixture
def repo(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/documentation_policy.json').write_text(json.dumps({'generated': []}))
    (tmp_path / 'docs/README.md').write_text(readme())
    gate.check(tmp_path, write=True)
    return tmp_path


def readme():
    return '\n'.join(f'## {s}\n\nReviewed explanation.\n' for s in gate.SECTIONS) + gate.START + '\n' + gate.END


def test_repository_passes():
    rows, errors = gate.check()
    assert rows and not errors, '\n'.join(errors)


def test_new_untracked_directory_cannot_hide(repo):
    (repo / 'new').mkdir()
    (repo / 'new/code.py').write_text('x = 1\n')
    (repo / 'new/future.unlisted-language').write_text('new language source')
    errors = gate.check(repo)[1]
    assert 'missing owner new/README.md' in '\n'.join(errors)
    assert any('future.unlisted-language' in e for e in errors)


def test_missing_sections_are_not_autofixed(repo):
    (repo / 'docs/README.md').write_text(gate.START + gate.END)
    assert len(gate.check(repo, write=True)[1]) == 4


def test_same_length_source_change_is_stale(repo):
    path = repo / 'docs/config.json'
    path.write_text('{"flag": 1}')
    assert gate.check(repo)[1]
    assert not gate.check(repo, write=True)[1]
    assert not gate.check(repo)[1]
    path.write_text('{"flag": 2}')
    assert gate.check(repo)[1]


def test_read_only_mode_does_not_rewrite(repo):
    before = (repo / 'docs/README.md').read_bytes()
    (repo / 'docs/new.json').write_text('{}')
    assert gate.check(repo)[1]
    assert (repo / 'docs/README.md').read_bytes() == before


def test_qualified_symbols_and_line_bounds(repo):
    path = repo / 'docs/test.py'
    path.write_text('class A:\n def run(self):\n  return 1\nclass B:\n def run(self):\n  return 2\n')
    result = gate.symbols(path)
    assert [s['name'] for s in result] == ['A', 'A.run', 'B', 'B.run']
    assert all(1 <= s['line'] <= s['end'] <= 6 for s in result)


def test_duplicate_block_rejected(repo):
    with (repo / 'docs/README.md').open('a') as f:
        f.write(gate.START + gate.END)
    assert 'exactly one' in '\n'.join(gate.check(repo, write=True)[1])
