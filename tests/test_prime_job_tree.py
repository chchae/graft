"""Complete native status trees and read-only repair of frozen Prime queries."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from graft import md_gbsa, md_gbsa_native as native, schrodinger_guard as guard


@pytest.mark.parametrize('query', [native.native_rows, guard.job_details])
def test_queries_request_parent_and_nested_children(monkeypatch, query):
    rows = [dict(jobId='parent', status='DONE'),
            dict(jobId='child', parentJobId='parent', status='DONE'),
            dict(jobId='grandchild', parentJobId='child', status='RUNNING')]

    def run(command, **kw):
        assert command == ['/suite/jsc', 'info', '--json', '--tree', 'parent']
        return subprocess.CompletedProcess(command, 0, '\n'.join(map(json.dumps, rows)))

    monkeypatch.setattr(subprocess, 'run', run)
    assert query(Path('/suite'), 'parent') == rows


@pytest.mark.parametrize('query', [native.native_rows, guard.job_details])
def test_missing_parent_still_blocks_after_complete_tree_query(monkeypatch, query):
    monkeypatch.setattr(subprocess, 'run', lambda command, **kw:
                        subprocess.CompletedProcess(command, 0,
                            json.dumps(dict(jobId='child', status='DONE'))))
    with pytest.raises(RuntimeError, match='parent'):
        query(Path('/suite'), 'parent')


def test_frozen_prime_jobs_use_current_query_and_preserve_journals(tmp_path, monkeypatch):
    root = tmp_path/'frozen'
    root.mkdir()
    suite = tmp_path/'suite'
    suite.mkdir()
    identity = 'c65d4f1c-e264-11f1-b29f-5e18d0b893d6'
    for name in ('md_gbsa_native.py', 'md_gbsa_jobcontrol.py', 'prime_gbsa_local_native.py'):
        shutil.copy2(Path(native.__file__).with_name(name), root/name)
    # Reproduce the historical helper: parent absent unless --tree is requested.
    path = root/'md_gbsa_native.py'
    path.write_text(path.read_text().replace("'--json', '--tree', job_id", "'--json', job_id"))
    (root/'prime').mkdir()
    native.save(root/'prime/prime.job.json', dict(job_id=identity, command=[str(suite/'prime_mmgbsa')]))
    # Keep uncertain reservations visible alongside known running children.
    pending = root/'pools/pending'
    pending.mkdir(parents=True)
    native.save(pending/'request.json', dict())
    jsc = suite/'jsc'
    jsc.write_text(f'''#!{sys.executable}
import json, sys
parent = {identity!r}
if sys.argv[1] == 'list':
    print(parent)
else:
    if '--tree' in sys.argv:
        print(json.dumps(dict(jobId=parent, status='DONE')))
    print(json.dumps(dict(jobId='child', parentJobId=parent, status='DONE')))
    print(json.dumps(dict(jobId='grandchild', parentJobId='child', status='RUNNING')))
''')
    jsc.chmod(0o755)
    jobcontrol = suite/'jobcontrol'
    jobcontrol.write_text('#!/bin/sh\necho "** no active jobs were found **"\n')
    jobcontrol.chmod(0o755)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    calls = []

    def ssh(host, command, **kwargs):
        calls.append((host, command, kwargs))
        argv = shlex.split(command)
        assert argv[:4] == [str(suite/'run'), 'python3', '-', 'jobs']
        result = subprocess.run([sys.executable, *argv[2:]], input=kwargs['input'],
                                capture_output=True, text=True, check=True,
                                env=dict(os.environ, SCHRODINGER=str(suite), PYTHONDONTWRITEBYTECODE='1'))
        return result.stdout

    monkeypatch.setattr(md_gbsa, 'ssh', ssh)
    settings = dict(ssh_host='prime', suite=str(suite), root=str(root),
                    helper=str(root/'prime_gbsa_local_native.py'))
    rows = md_gbsa.response(md_gbsa.remote_call(settings, 'jobs', str(root)))
    assert {row['jobId'] for row in rows} == {'grandchild', 'uncertain:'+str(pending/'request.json')}
    assert all(p.read_bytes() == data for p, data in before.items())
    assert {p for p in root.rglob('*') if p.is_file()} == set(before)
    assert len(calls) == 1


@pytest.mark.parametrize('action', ['poll', 'verify', 'retry', 'check'])
def test_scientific_prime_actions_keep_frozen_entrypoint(monkeypatch, action):
    def ssh(host, command, **kwargs):
        assert host == 'prime'
        assert shlex.split(command) == ['/suite/run', '/frozen/prime_gbsa_local_native.py', action, '/frozen']
        assert not kwargs
        return 'unchanged'

    monkeypatch.setattr(md_gbsa, 'ssh', ssh)
    settings = dict(ssh_host='prime', suite='/suite', helper='/frozen/prime_gbsa_local_native.py')
    assert md_gbsa.remote_call(settings, action, '/frozen') == 'unchanged'
