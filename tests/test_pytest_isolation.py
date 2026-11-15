"""Plain pytest isolates fallback submission resources for its whole lifetime."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_plain_pytest_isolates_collection_tests_teardown_and_child_tmpdir(tmp_path):
    suite = tmp_path/'probe'
    suite.mkdir()
    (suite/'conftest.py').write_bytes(Path(__file__).with_name('conftest.py').read_bytes())
    (suite/'test_probe.py').write_text('''
import json, os, tempfile
from pathlib import Path
import pytest
from graft import schrodinger_guard

snapshot = {'tmpdir': schrodinger_guard.tempfile.gettempdir(), 'stdlib_tmpdir': tempfile.gettempdir(), 'collection': str(schrodinger_guard._md_registry())}

@pytest.fixture(scope='session', autouse=True)
def record_after_function_fixtures():
    yield
    snapshot['teardown'] = str(schrodinger_guard._md_registry())
    Path(__file__).with_name('observed.json').write_text(json.dumps(snapshot))

def test_probe(tmp_path):
    snapshot['fixture'] = str(schrodinger_guard._md_registry())
    snapshot['child_tmpdir'] = os.environ['TMPDIR']
    from multiprocessing.util import get_temp_dir
    snapshot['multiprocessing_tmpdir'] = get_temp_dir()
    assert snapshot['fixture'] == str(tmp_path/'md-remotes.json')
''')
    env = dict(os.environ)
    env.pop('GRAFT_REFACTOR_TEST_TMP', None)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(suite)],
                            cwd=suite, env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Traceback' not in result.stderr, result.stderr
    observed = json.loads((suite/'observed.json').read_text())
    temporary = Path(observed['tmpdir'])
    assert temporary.name.startswith('graft-pytest-')
    assert temporary != Path(os.environ['TMPDIR'])
    assert str(temporary/'session-md-remotes.json') == observed['collection'] == observed['teardown']
    assert observed['child_tmpdir'] == observed['stdlib_tmpdir'] == str(temporary)
    assert Path(observed['fixture']) != Path(observed['collection'])
    assert not Path(observed['multiprocessing_tmpdir']).is_relative_to(temporary)
    assert not temporary.exists()
