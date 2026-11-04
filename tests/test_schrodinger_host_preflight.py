"""Native API incompatibility must be detected before a launch record exists."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from graft import schrodinger_guard as guard

HOSTS='''Configured hosts:
  slurm:8030
    Hosts for /share/suite:
      slurm-cpu
        Name: slurm-cpu
  local:8030
    Hosts for /local/suite:
      localhost
        Name: localhost
'''

@pytest.mark.parametrize('server,client,allowed',[('1.19.4','1.21.2',False),('1.21.2','1.21.2',True),('1.22.0','1.21.2',True)])
def test_only_selected_host_server_is_checked_before_launch(monkeypatch,server,client,allowed):
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if command[1]=='hosts':return SimpleNamespace(stdout=HOSTS)
        assert command[-1]=='slurm:8030'
        return SimpleNamespace(stdout=f'Server API version: v{server}\nClient API version: v{client}\n')
    monkeypatch.setattr(guard.subprocess,'run',run)
    if allowed:guard.verify_submission_host(Path('/suite'),'slurm-cpu:124')
    else:
        with pytest.raises(guard.SubmissionBlocked,match='older than client API'):
            guard.verify_submission_host(Path('/suite'),'slurm-cpu:124')
    assert len(calls)==2


def test_unregistered_native_host_does_not_launch(monkeypatch):
    monkeypatch.setattr(guard.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout=HOSTS))
    with pytest.raises(guard.SubmissionBlocked,match='not available'):
        guard.verify_submission_host(Path('/suite'),'missing:4')


def test_ligprep_api_mismatch_waits_before_claim_or_launch(tmp_path,monkeypatch,capsys):
    from graft import _core,database
    from graft.stages import ligprep
    db=tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(name,smiles,iteration,state,sa_score) VALUES ('new','CC',1,'generated',2)")
    args=SimpleNamespace(db_path=db,output=tmp_path,schrodinger=Path('/suite'),target_count=1,
        synthetic_feasibility_enabled=False,use_slurm=True,host='old:8',ligprep_host='slurm-cpu')
    def incompatible(*a):raise guard.SubmissionBlocked('API 1.19.4 is older than client API 1.21.2')
    monkeypatch.setattr(guard,'verify_submission_host',incompatible)
    monkeypatch.setattr(_core,'run_ligprep_once',lambda **k:pytest.fail('launched incompatible native client'))
    ligprep._run_stage(args,1)
    assert 'LigPrep waiting: API 1.19.4' in capsys.readouterr().out
    with database.connect(db) as conn:assert conn.execute('SELECT state FROM compound').fetchone()[0]=='generated'
    assert not list(tmp_path.rglob('ligprep_job.json'))


def test_ligprep_discovery_failure_waits_without_claiming_or_submitting(tmp_path,monkeypatch,capsys):
    from graft import _core,database,ligprep_recovery
    from graft.stages import ligprep
    db=tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(name,smiles,iteration,state,sa_score) VALUES ('new','CC',1,'generated',2)")
    args=SimpleNamespace(db_path=db,output=tmp_path,schrodinger=Path('/suite'),target_count=1,
        synthetic_feasibility_enabled=False,host='old:8')
    folder=tmp_path/'iter01/05-ligprep';folder.mkdir(parents=True)
    (folder/'input_all.smi').write_text('retained input')
    def unavailable(*a):raise RuntimeError('MD remote query failed:\nTraceback (most recent call last):\nRuntimeError: certificate verification failed')
    monkeypatch.setattr(guard,'active_jobs',unavailable)
    monkeypatch.setattr(_core,'run_ligprep_once',lambda **k:pytest.fail('submitted while status unavailable'))
    ligprep._run_stage(args,1)
    output=capsys.readouterr().out
    assert 'LigPrep waiting: Cannot verify previous jobs' in output
    assert 'Traceback' not in output
    assert (folder/'input_all.smi').read_text()=='retained input'
    with database.connect(db) as conn:assert conn.execute('SELECT state FROM compound').fetchone()[0]=='generated'
