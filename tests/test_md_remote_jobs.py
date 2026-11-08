"""Deleted MD helpers must not disable native submission exclusion."""
import json
import shlex
import sys

import pytest

from graft import md_gbsa, md_gbsa_native as native, schrodinger_guard as guard


def test_remote_jobs_stream_read_only_query_without_frozen_helper(monkeypatch):
    settings = dict(ssh_host='remote', suite='/suite', root='/deleted run',
                    helper='/deleted run/native.py')
    calls = []

    def ssh(host, command, **kwargs):
        calls.append((host, shlex.split(command), kwargs))
        return 'GRAFT_MD=[]'

    monkeypatch.setattr(md_gbsa, 'ssh', ssh)
    monkeypatch.setattr(md_gbsa, 'upload', lambda *a: pytest.fail('query uploaded a helper'))
    assert md_gbsa.response(md_gbsa.remote_call(settings, 'jobs', settings['root'])) == []
    host, command, kwargs = calls[0]
    assert host == 'remote'
    assert command == ['/suite/run', 'python3', '-', 'jobs', '/deleted run']
    assert kwargs['input'] == md_gbsa.Path(native.__file__).read_text()
    # Scientific polling retains the frozen helper path and does not stream code.
    md_gbsa.remote_call(settings, 'poll', '/deleted run/CMPID_1/replica01')
    assert calls[1][1][:3] == ['/suite/run', '/deleted run/native.py', 'poll']
    assert calls[1][2] == {}


def test_jobs_for_missing_root_still_report_global_live_children(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'deleted'
    child = dict(jobId='child', parentJobId='done-parent', status='RUNNING', jobName='prime_mmgbsa')
    monkeypatch.setattr(sys, 'argv', ['native.py', 'jobs', str(root)])
    monkeypatch.setenv('SCHRODINGER', '/suite')
    monkeypatch.setattr(native, 'active_jobs', lambda suite: [child])
    native.main()
    assert md_gbsa.response(capsys.readouterr().out) == [child]
    assert not root.exists()


def test_jobs_preserve_uncertain_prime_intent(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'existing'
    journal = root / 'CMPID_1/replica01/prime/prime.job.json'
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps(dict(job_id=None, launching=True)))
    monkeypatch.setattr(sys, 'argv', ['native.py', 'jobs', str(root)])
    monkeypatch.setenv('SCHRODINGER', '/suite')
    monkeypatch.setattr(native, 'active_jobs', lambda suite: [])
    native.main()
    result = md_gbsa.response(capsys.readouterr().out)
    assert len(result) == 1 and result[0]['status'] == 'UNCERTAIN'


def test_missing_root_does_not_hide_native_lookup_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['native.py', 'jobs', str(tmp_path / 'deleted')])
    monkeypatch.setenv('SCHRODINGER', '/suite')
    monkeypatch.setattr(native, 'active_jobs', lambda suite: (_ for _ in ()).throw(RuntimeError('offline')))
    with pytest.raises(RuntimeError, match='offline'):
        native.main()


def test_registry_retains_deleted_runs_and_remote_prime_blocks_glide(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.json'
    settings = dict(ssh_host='remote', suite='/suite', root='/deleted', helper='/deleted/native.py')
    registry.write_text(json.dumps({'remote-run': settings}))
    monkeypatch.setattr(guard, '_md_registry', lambda: registry)
    monkeypatch.setattr(guard, '_local_active_jobs', lambda suite: [])
    live = dict(jobId='remote-prime', status='RUNNING', jobName='prime_mmgbsa')
    monkeypatch.setattr(md_gbsa, 'ssh', lambda *a, **kw: 'GRAFT_MD='+json.dumps([live]))
    with pytest.raises(guard.SubmissionBlocked, match='prime_gbsa still active'):
        with guard.submission_guard('/local-suite', 'glide'):
            pytest.fail('submitted despite remote Prime')
    assert json.loads(registry.read_text()) == {'remote-run': settings}
