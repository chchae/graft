"""Dependency capture and check-before-freeze contracts for the T6 follow-up."""
from types import SimpleNamespace
import json

import pytest

from graft import _core, database, md_gbsa, md_gbsa_local, prime_gbsa_async, remote_cleanup
from graft import attraction_point, libinvent_vocabulary
from graft.chemistry import libinvent_decorations
from test_prime_orchestration_contracts import prime_case


@pytest.mark.parametrize('ready',[False,True])
def test_saved_prime_dependencies_are_bound_before_job_file_loading(prime_case,monkeypatch,ready):
    case=prime_case();active={'command':['/suite/prime'],'compound_ids':[1,2],'job_id':'kept'}
    job=case.directory/'prime_job.json';job.write_text('durable job')
    events=[]
    def submit(*args,**kwargs):events.append('bound-submit');return ready
    def failures(*args):events.append('bound-failures')
    def job_file(folder):events.append('bound-job-file');return job
    def forbidden(*a,**kw):pytest.fail('Saved-job dependencies were looked up after load')
    def load(folder):
        events.append('load')
        for name in ('submit_or_poll','persist_atomtyping_failures','job_file'):monkeypatch.setattr(prime_gbsa_async,name,forbidden)
        return active
    monkeypatch.setattr(prime_gbsa_async,'submit_or_poll',submit)
    monkeypatch.setattr(prime_gbsa_async,'persist_atomtyping_failures',failures)
    monkeypatch.setattr(prime_gbsa_async,'job_file',job_file)
    monkeypatch.setattr(prime_gbsa_async,'load_job',load)
    monkeypatch.setattr(_core,'finish_iteration_prime_gbsa',lambda *a:events.append('finish') or 7)
    assert case.run()==(7 if ready else None)
    assert events==['load','bound-submit']+(['finish','bound-failures','bound-job-file'] if ready else [])
    assert job.exists() is not ready


def test_training_dependencies_are_captured_before_prior_vocabulary_loading(tmp_path,monkeypatch):
    events=[]
    def forbidden(*a,**kw):pytest.fail('Vocabulary check was rebound during prior loading')
    def original(*args):events.append('original-tokens');return []
    def prior(*args):
        events.append('prior')
        monkeypatch.setattr(libinvent_vocabulary,'unsupported_pair_tokens',forbidden)
        return {'unchanged':'vocabulary'}
    def decompose(*args):events.append('decompose');return ('C[*]','*N')
    monkeypatch.setattr(libinvent_vocabulary,'load_prior_vocabulary',prior)
    monkeypatch.setattr(libinvent_vocabulary,'unsupported_pair_tokens',original)
    monkeypatch.setattr(libinvent_decorations,'decompose',decompose)
    rows=[(i,'elite'+str(i),'CCO',1,-10.,-40.) for i in (1,2)]
    _,train,valid,count=_core.write_elite_libinvent_training_files(rows,'C[*]',tmp_path,prior_file='/original.prior')
    assert count==2 and train.read_bytes()==valid.read_bytes()==b'C[*]\t*N\n'
    assert events==['prior','decompose','original-tokens','decompose','original-tokens']
    assert json.loads((tmp_path/'elite_vocabulary.json').read_text())=={'unchanged':'vocabulary'}


@pytest.mark.parametrize('stop_at,local',[(x,False) for x in ('priority','rank','settings','mkdir','upload','check','freeze','after-freeze')]+[('after-freeze',True)])
def test_md_remote_compatibility_and_local_preparation_precede_freeze(tmp_path,monkeypatch,stop_at,local):
    args=SimpleNamespace(db_path=tmp_path/'unused.sqlite',output=tmp_path,md_gbsa_enabled=True,
        target_count=4,prime_gbsa_input_count=4,md_gbsa_input_count=2,md_gbsa_input_ratio=.5)
    events=[];failure=RuntimeError('boundary stop')
    settings={'ssh_host':'remote','root':'/remote/run','helper':'/remote/run/native.py'}
    if local:settings['local_compute']={'enabled':True}
    def point(name):
        events.append(name)
        if name==stop_at:raise failure
    monkeypatch.setattr(database,'md_gbsa_selection',lambda *a:None)
    monkeypatch.setattr(md_gbsa,'_md_upstream_ready',lambda *a:True)
    monkeypatch.setattr(database,'md_gbsa_readiness_reason',lambda *a,**kw:None)
    monkeypatch.setattr(attraction_point,'prepare',lambda *a:point('priority'))
    def rank(*a,**kw):point('rank');assert kw==dict(count=2,ratio=.5,prime_enabled=True);return {'ids':[1,2],'ranking_source':'prime_gbsa'}
    monkeypatch.setattr(database,'md_gbsa_candidates',rank)
    monkeypatch.setattr(md_gbsa,'settings_for',lambda *a:point('settings') or settings)
    def mkdir(host,command):assert host=='remote' and command=='mkdir -p /remote/run';point('mkdir')
    monkeypatch.setattr(md_gbsa,'ssh',mkdir)
    def upload(host,source,destination):assert source.name=='md_gbsa_native.py' and destination==settings['helper'];point('upload')
    monkeypatch.setattr(md_gbsa,'upload',upload)
    def check(given,action,root,*extra):
        assert given is settings and action=='check' and root==settings['root']
        assert extra==('--ids',json.dumps(settings));point('check');return 'GRAFT_MD={"status":"compatible"}'
    monkeypatch.setattr(md_gbsa,'remote_call',check)
    monkeypatch.setattr(md_gbsa_local,'prepare',lambda given:point('local-prepare'))
    def freeze(path,iteration,given,**kw):
        point('freeze');assert given is settings
        assert kw==dict(count=2,ratio=.5,target_count=4,prime_count=4,amber_keep_count=None,prime_enabled=True)
        return {'ids':[1,2],'settings':settings}
    monkeypatch.setattr(database,'freeze_md_gbsa_selection',freeze)
    monkeypatch.setattr(md_gbsa_local,'cleanup_committed',lambda *a:point('local-cleanup'))
    monkeypatch.setattr(remote_cleanup,'md',lambda *a:point('remote-cleanup'))
    monkeypatch.setattr(remote_cleanup,'md_run',lambda *a:point('run-cleanup'))
    monkeypatch.setattr(database,'enable_md_cycle_retries',lambda *a:point('after-freeze'))
    sequence=['priority','rank','settings','mkdir','upload','check']+(['local-prepare'] if local else [])+['freeze']+(['local-cleanup'] if local else [])+['remote-cleanup','run-cleanup','after-freeze']
    with pytest.raises(RuntimeError) as caught:md_gbsa.run(args,1)
    assert caught.value is failure
    assert events==sequence[:sequence.index(stop_at)+1]
    assert not args.db_path.exists()
