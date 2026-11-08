"""Retirement checks and transactional reset; synthetic MD outputs only."""
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from test_deleted_crem_recovery import deleted
from test_crem_pipeline import project
from test_combichem_crem import project as growth_project
from graft import database
from graft.stages import _deleted_md
from graft.stages._workspace import prepare_iteration_directory, WorkspaceReconciliationPending


@pytest.mark.parametrize('case', ['done', 'active_child', 'uncertain', 'failed_launch', 'held_lock', 'wrong_owner'])
def test_md_probe_requires_terminal_native_and_exact_owner(tmp_path, monkeypatch, capsys, case):
    root = tmp_path/'md'
    folder = root/'CMPID_7/replica01'
    (folder/'md').mkdir(parents=True)
    state = 'RUNNING' if case == 'active_child' else 'DONE'
    source = ("TERMINAL={'DONE','FAILED','STOPPED','CANCELED'}\n"
              "def native_rows(suite, identity):\n"
              " return [dict(jobId=identity,status='DONE'),dict(jobId='child',status="+repr(state)+")]\n")
    (root/'native.py').write_text(source)
    selection = dict(ids=[7],settings=dict(root=str(root),suite='/suite',replicates=1,
                        helper_sha256=hashlib.sha256(source.encode()).hexdigest()))
    (root/'ready.json').write_text(json.dumps(dict(selection, ids=[8]) if case=='wrong_owner' else selection))
    record = dict(job_id=None if case in ('uncertain','failed_launch') else 'parent',
                  command=['/suite/utilities/multisim'],client_exit=1 if case=='failed_launch' else None)
    (folder/'md/md.job.json').write_text(json.dumps(record))
    result = folder/'result.json'
    result.write_text('{"mean": -20}')
    request = dict(selection=selection,local=False)
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(request)))
    monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.setattr(subprocess,'run',lambda cmd,**kw: subprocess.CompletedProcess(cmd,0,'found\n',''))
    with (folder/'controller.lock').open('a') as stream:
        if case=='held_lock':
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        if case in ('done','failed_launch'):
            exec(_deleted_md.PROBE,{})
            proof=json.loads(capsys.readouterr().out)
            assert len(proof['native'])==2
            if case=='failed_launch':
                assert proof['recovered_launches'][0]['job_ids']==['found']
        else:
            with pytest.raises((RuntimeError,BlockingIOError)):
                exec(_deleted_md.PROBE,{})
    assert result.read_text()=='{"mean": -20}'
    assert json.loads((folder/'md/md.job.json').read_text())==record


def install_selection(args):
    context=database.deleted_crem_recovery_context(args.db_path,1)
    selection=dict(ids=context['ids'],settings=dict(root='/retained/md'))
    with database.connect(args.db_path) as c:
        c.execute('INSERT INTO iteration_md_gbsa_selection VALUES (?,?,?)',
                  (1,json.dumps(selection['ids']),json.dumps(selection['settings'])))
    # Simulate scientific rows orphaned by an operator using FK-disabled SQLite.
    with sqlite3.connect(args.db_path) as c:
        c.execute('INSERT INTO compound_md_gbsa_result VALUES (?,?)',
                  (selection['ids'][0],json.dumps(dict(id=selection['ids'][0],mean=-20))))
    return selection


def test_retired_md_selection_and_orphan_scores_are_backed_up_before_reset(deleted,monkeypatch):
    selection=install_selection(deleted)
    monkeypatch.setattr(_deleted_md,'reconcile',lambda s:dict(selection=s,roots=[dict(metadata={'result':-20})]))
    archive=prepare_iteration_directory(deleted,1)
    assert database.md_gbsa_selection(deleted.db_path,1) is None
    backup,=archive.glob('database-before-reset-*.sqlite')
    with sqlite3.connect(backup) as c:
        assert c.execute('SELECT candidates FROM iteration_md_gbsa_selection').fetchone()[0]==json.dumps(selection['ids'])
        assert c.execute('SELECT count(*) FROM compound_md_gbsa_result').fetchone()[0]==1
    with database.connect(deleted.db_path) as c:
        assert c.execute('SELECT count(*) FROM compound_md_gbsa_result').fetchone()[0]==0
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_md_selection_changed_since_probe_blocks_reset_without_losing_results(deleted,tmp_path):
    selection=install_selection(deleted)
    stale=dict(selection,settings=dict(root='/different'))
    backup=tmp_path/'must-not-reset.sqlite'
    with pytest.raises(RuntimeError,match='durable MD'):
        database.reset_deleted_iteration(deleted.db_path,1,backup,allow_deleted_crem=True,reconciled_md=stale)
    assert not backup.exists()
    assert database.md_gbsa_selection(deleted.db_path,1)==selection


def test_live_md_work_keeps_selection_and_database_unchanged(deleted,monkeypatch):
    selection=install_selection(deleted)
    def live(s):
        raise RuntimeError('MD/Prime jobs are active')
    monkeypatch.setattr(_deleted_md,'reconcile',live)
    with pytest.raises(WorkspaceReconciliationPending,match='active'):
        prepare_iteration_directory(deleted,1)
    assert database.md_gbsa_selection(deleted.db_path,1)==selection
