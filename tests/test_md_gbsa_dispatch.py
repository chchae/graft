"""GPU admission precedes CPU work; fake native status does not validate MD."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, database, md_gbsa
from graft import md_gbsa_dispatch as dispatch, md_gbsa_native as native
from graft import schrodinger_guard as guard


@pytest.mark.parametrize('local_compute', [False, True])
def test_twenty_gpu_owners_and_four_queued_start_before_any_gbsa(tmp_path, monkeypatch, local_compute):
    db = tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany('INSERT INTO compound(name,smiles,iteration,state,docking_score,prime_gbsa_score) '
                         "VALUES (?,'CC',1,'prime_gbsa_done',-10,?)", [(f'c{i}', -100+i) for i in range(24)])
    settings = dict(protocol=dict(snapshots=20, seed=1), replicates=1, root='/frozen', gpus=20)
    if local_compute:
        from graft import md_gbsa_local
        settings['local_compute'] = dict(root=str(tmp_path/'share'))
        monkeypatch.setattr(md_gbsa_local, 'prepare', lambda *a: None)
        monkeypatch.setattr(md_gbsa_local, 'cleanup_committed', lambda *a: None)
    saved = database.freeze_md_gbsa_selection(db, 1, settings, count=24, target_count=24, prime_count=24)
    directory = tmp_path/'iter01/09-md_gbsa'
    directory.mkdir(parents=True)
    progress = {str((cid, 0)): dict(phase='md', status='running') for cid in saved['ids'][:20]}
    native.save(directory/'progress.json', progress)
    events = []
    def remote(config, action, root):
        cid = int(root.split('CMPID_')[1].split('/')[0])
        events.append((action, cid))
        if action == 'md-status':
            result = dict(phase='frames', status='pending') if cid <= 20 else dict(phase='md', status='unstarted')
        elif action == 'md-start':
            result = dict(phase='md', status='running')
        else:
            assert action == 'poll'
            assert [cid for op, cid in events if op == 'md-start'] == [21, 22, 23, 24]
            result = dict(phase='prime', status='running')
        return 'GRAFT_MD='+json.dumps(result)
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    if local_compute:
        def local_poll(settings, cid, replica, root, archive, **kw):
            assert [cid for op, cid in events if op == 'md-start'] == [21, 22, 23, 24]
            events.append(('prepare-frame', cid))
            if not kw.get('prepare_only'):
                events.append(('poll', cid))
            return dict(phase='prime', status='running')
        monkeypatch.setattr(md_gbsa_local, 'poll', local_poll)
    monkeypatch.setattr(md_gbsa, 'prepare', lambda *a: None)
    monkeypatch.setattr(guard, 'register_md_remote', lambda *a: None)
    monkeypatch.setattr(guard, 'active_jobs', lambda *a: [])
    monkeypatch.setattr(guard, 'submission_guard', lambda *a, **kw: nullcontext())
    md_gbsa.run(SimpleNamespace(db_path=db, output=tmp_path, schrodinger='/suite'), 1)
    assert len([e for e in events if e[0] == 'poll']) == 20
    if local_compute:
        # Submit the first ready molecule before extracting/transferring peers.
        # A slow or failed later extraction must not delay the first launch.
        cpu_events=[e for e in events if e[0] in ('prepare-frame','poll')]
        assert cpu_events[:4]==[('prepare-frame',1),('poll',1),('prepare-frame',2),('poll',2)]
    assert database.completed_md_ids(db, 1) == set()  # Native MD is not an ensemble score.


@pytest.mark.parametrize('previous_file', [False, True])
def test_live_and_uncertain_jobs_reserve_gpu_even_after_lost_progress(tmp_path, monkeypatch, previous_file):
    progress = {'(1, 0)': dict(phase='md', status='pending')} if previous_file else {}
    calls = []
    def remote(settings, action, root):
        calls.append((action, root))
        state = dict(phase='md', status='pending' if root == '/one' else 'unstarted')
        return 'GRAFT_MD='+json.dumps(state)
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    md_gbsa.admit_md(dict(gpus=1), [(1, 0, '/one'), (2, 0, '/two')], progress, tmp_path/'progress.json')
    assert ('md-start', '/two') not in calls
    assert progress['(1, 0)']['status'] == 'pending'


def test_terminal_failure_releases_slot_but_prime_never_counts_as_gpu(tmp_path, monkeypatch):
    progress = {'(1, 0)': dict(phase='md', status='running'),
                '(2, 0)': dict(phase='prime', status='running')}
    starts = []
    def remote(settings, action, root):
        if action == 'md-start':
            starts.append(root)
            state = dict(phase='md', status='running')
        else:
            state = dict(phase='md', status='failed' if root == '/one' else 'unstarted')
        return 'GRAFT_MD='+json.dumps(state)
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    md_gbsa.admit_md(dict(gpus=1), [(1, 0, '/one'), (2, 0, '/two'), (3, 0, '/three')],
                     progress, tmp_path/'progress.json')
    assert starts == ['/three']


def test_lost_start_reply_keeps_intent_and_does_not_admit_another_job(tmp_path, monkeypatch):
    calls = []
    def remote(settings, action, root):
        calls.append((action, root))
        if action == 'md-start':
            raise RuntimeError('SSH lost reply')
        return 'GRAFT_MD='+json.dumps(dict(phase='md', status='unstarted'))
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    path = tmp_path/'progress.json'
    with pytest.raises(RuntimeError, match='lost reply'):
        md_gbsa.admit_md(dict(gpus=1), [(1, 0, '/one'), (2, 0, '/two')], {}, path)
    assert json.loads(path.read_text())['(1, 0)']['status'] == 'pending'
    assert ('md-start', '/two') not in calls


@pytest.fixture
def task(tmp_path, monkeypatch):
    root = tmp_path/'CMPID_1/replica01'
    root.mkdir(parents=True)
    native.save(root/'input.json', dict(protocol={}, gpu_host='slurm-gpu', cpu_host='slurm-cpu'))
    monkeypatch.setattr(native, 'workflow', lambda *a: 'unchanged workflow')
    monkeypatch.setattr(native, 'extract_frames', lambda *a: pytest.fail('extracted frames in GPU admission'))
    monkeypatch.setattr(native.subprocess, 'run', lambda *a, **k: pytest.fail('download or submit in status query'))
    command = ['/suite/utilities/multisim', '-HOST', 'slurm-gpu:1', '-maxjob', '1',
               '-JOBNAME', 'md', '-m', 'md.msj', '-mode', 'umbrella', '-o', 'md-out.cms', str(root/'complex.maegz')]
    return root, command


@pytest.mark.parametrize('parent,child,phase,status', [
    ('RUNNING', 'RUNNING', 'md', 'running'), ('DONE', 'RUNNING', 'md', 'running'),
    ('DONE', 'DONE', 'frames', 'pending'), ('FAILED', 'DONE', 'md', 'failed'),
])
def test_md_status_verifies_parent_and_children_without_download(task, monkeypatch, parent, child, phase, status):
    root, command = task
    (root/'md').mkdir()
    record = dict(command=command, job_id='saved-job')
    native.save(root/'md/md.job.json', record)
    monkeypatch.setattr(native, 'native_rows', lambda *a: [dict(jobId='saved-job', status=parent),
                                                        dict(jobId='child', status=child)])
    result = dispatch.poll_md(native, root, Path('/suite'))
    assert (result['phase'], result['status']) == (phase, status)
    assert json.loads((root/'md/md.job.json').read_text()) == record
    assert not (root/'frames.json').exists()


def test_unstarted_status_does_not_launch_and_start_uses_frozen_command_once(task, monkeypatch):
    root, command = task
    assert dispatch.poll_md(native, root, Path('/suite')) == dict(phase='md', status='unstarted')
    calls = []
    def launch(folder, phase, actual, suite):
        calls.append(actual)
        native.save(folder/'md.job.json', dict(command=actual, job_id='saved-job'))
        return dict(status='running')
    monkeypatch.setattr(native, 'poll_job', launch)
    monkeypatch.setattr(native, 'native_rows', lambda *a: [dict(jobId='saved-job', status='RUNNING')])
    for _ in range(2):
        assert dispatch.poll_md(native, root, Path('/suite'), start=True)['status'] == 'running'
    assert calls == [command]


def test_uncertain_intent_and_command_mismatch_never_resubmit(task):
    root, command = task
    (root/'md').mkdir()
    native.save(root/'md/md.job.json', dict(command=command, job_id=None))
    assert dispatch.poll_md(native, root, Path('/suite'))['status'] == 'pending'
    native.save(root/'md/md.job.json', dict(command=['other-command'], job_id='saved-job'))
    with pytest.raises(RuntimeError, match='command differs'):
        dispatch.poll_md(native, root, Path('/suite'), start=True)


def test_unjournaled_launch_artifacts_are_never_relaunched(task):
    root, command = task
    (root/'md').mkdir()
    (root/'md/md.launch.log').write_text('JobId: missing-journal')
    for start in (False, True):
        result = dispatch.poll_md(native, root, Path('/suite'), start=start)
        assert result['status'] == 'pending'
        assert 'Unjournaled' in result['reason']
    assert not (root/'md/md.job.json').exists()


def test_invalid_start_reply_keeps_gpu_intent(tmp_path, monkeypatch):
    def remote(settings, action, root):
        return 'GRAFT_MD='+json.dumps(dict(phase='md', status='unstarted') if action == 'md-status'
                                     else dict(status='done'))
    monkeypatch.setattr(md_gbsa, 'remote_call', remote)
    path = tmp_path/'progress.json'
    with pytest.raises(RuntimeError, match='Invalid MD admission response'):
        md_gbsa.admit_md(dict(gpus=1), [(1, 0, '/one')], {}, path)
    assert json.loads(path.read_text())['(1, 0)']['status'] == 'pending'


@pytest.mark.parametrize('changed', [False, True])
def test_deployed_adapter_imports_verified_frozen_helper_without_rewriting_it(tmp_path, monkeypatch, capsys, changed):
    import hashlib
    import runpy
    import shutil
    import sys
    base = tmp_path/'frozen-run'
    (base/'control').mkdir(parents=True)
    root = base/'CMPID_1/replica01'
    (root/'md').mkdir(parents=True)
    helper = base/'native.py'
    source = """TERMINAL = {'DONE'}
def native_rows(suite, identity):
    return [dict(jobId=identity, status='DONE')]
def poll_job(*args):
    raise AssertionError('native launch/download unexpectedly called')
def poll(root, suite, frames_only=False):
    assert frames_only
    return dict(phase='md', **poll_job(root/'md', 'md', ['frozen-command'], suite))
"""
    helper.write_text(source)
    native.save(base/'ready.json', dict(settings=dict(helper=str(helper),
                helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest())))
    native.save(root/'md/md.job.json', dict(job_id='frozen-job', command=['frozen-command']))
    if changed:
        helper.write_text(source+'\n# changed helper\n')
    adapter = base/'control/md-dispatch-test.py'
    shutil.copyfile(dispatch.__file__, adapter)
    monkeypatch.setattr(sys, 'argv', [str(adapter), 'md-status', str(root)])
    monkeypatch.setenv('SCHRODINGER', '/frozen-suite')
    if changed:
        with pytest.raises(RuntimeError, match='Frozen MD helper identity changed'):
            runpy.run_path(str(adapter), run_name='__main__')
    else:
        runpy.run_path(str(adapter), run_name='__main__')
        assert md_gbsa.response(capsys.readouterr().out)['phase'] == 'frames'
        assert helper.read_text() == source
        assert json.loads((root/'md/md.job.json').read_text())['job_id'] == 'frozen-job'


def test_remote_dispatch_uploads_versioned_control_once_and_preserves_frozen_helper(monkeypatch):
    import shlex
    calls, uploads = [], []
    monkeypatch.setattr(md_gbsa, '_dispatch_uploaded', set())
    monkeypatch.setattr(md_gbsa, 'ssh', lambda host, cmd: calls.append((host, cmd)) or 'reply')
    monkeypatch.setattr(md_gbsa, 'upload', lambda host, source, destination: uploads.append((host, source, destination)))
    settings = dict(ssh_host='slurm', suite='/suite', root='/frozen run', helper='/frozen run/native.py')
    for action in ('md-status', 'md-start'):
        md_gbsa.remote_call(settings, action, '/frozen run/CMPID_1/replica01')
    assert len(uploads) == 1
    assert uploads[0][2].startswith('/frozen run/control/md-dispatch-')
    assert uploads[0][2] != settings['helper']
    assert shlex.split(calls[-1][1]) == ['/suite/run', uploads[0][2], 'md-start',
                                       '/frozen run/CMPID_1/replica01']
