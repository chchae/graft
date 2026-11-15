"""Characterize MD upstream gates before selection/claims or any remote action."""
from types import SimpleNamespace

import pytest

from graft import database, driver, md_gbsa, prime_gbsa_async

NATIVE_STAGES = ('ligprep', 'glide', 'amber-gbsa', 'prime_gbsa')
WORKERS = ('generate', 'druglike', 'synthetic_feasibility', 'ligprep', 'glide', 'amber_gbsa', 'prime_gbsa', 'fep')


@pytest.fixture
def gates(tmp_path, monkeypatch):
    args = SimpleNamespace(db_path=tmp_path/'never-opened.sqlite', output=tmp_path,
                           md_gbsa_enabled=True, target_count=4, prime_gbsa_input_count=4)
    events=[]
    fixture=SimpleNamespace(args=args,events=events,block=None,error=None)
    def mark(event):
        events.append(event)
        if fixture.error and fixture.error[0]==event:
            raise fixture.error[1]
    monkeypatch.setattr(database,'md_gbsa_selection',lambda *a:events.append('selection') or None)
    def load(folder):
        mark('saved-prime')
        return {} if fixture.block=='saved-prime' else None
    def external(args,iteration,stage):
        mark('native:'+stage)
        return 'native work remains' if fixture.block=='native:'+stage else None
    def active(path,worker):
        mark('worker:'+worker)
        return fixture.block=='worker:'+worker
    def readiness(path,iteration,**kwargs):
        mark('db-readiness')
        assert kwargs==dict(target_count=4,prime_count=4,amber_keep_count=None,prime_enabled=True)
        return 'DB upstream waiting'
    monkeypatch.setattr(prime_gbsa_async,'load_job',load)
    monkeypatch.setattr(driver,'external_completion_reason',external)
    monkeypatch.setattr(database,'stage_is_running',active)
    monkeypatch.setattr(database,'md_gbsa_readiness_reason',readiness)
    def forbidden(*a,**kw):
        pytest.fail('Blocked MD gate reached selection/remote calculation')
    monkeypatch.setattr(database,'freeze_md_gbsa_selection',forbidden)
    monkeypatch.setattr(md_gbsa,'ssh',forbidden)
    monkeypatch.setattr(md_gbsa,'upload',forbidden)
    fixture.sequence=['selection','saved-prime']+['native:'+stage for stage in NATIVE_STAGES]+['worker:'+worker for worker in WORKERS]+['db-readiness']
    return fixture


@pytest.mark.parametrize('block',['saved-prime']+['native:'+x for x in NATIVE_STAGES]+['worker:'+x for x in WORKERS])
def test_md_blockers_stop_before_later_native_worker_and_claim_checks(gates,capsys,block):
    gates.block=block
    assert md_gbsa.run(gates.args,5) is None
    assert gates.events==gates.sequence[:gates.sequence.index(block)+1]
    expected=('MD-GBSA waiting for Prime submission recovery.' if block=='saved-prime' else
              'MD-GBSA waiting: native work remains' if block.startswith('native:') else
              f'MD-GBSA waiting: {block.removeprefix("worker:")} worker is still running')
    assert capsys.readouterr().out==expected+'\n'
    assert not gates.args.db_path.exists()


def test_md_reaches_database_readiness_only_after_all_external_and_worker_gates(gates,capsys):
    assert md_gbsa.run(gates.args,5) is None
    assert gates.events==gates.sequence
    assert capsys.readouterr().out=='DB upstream waiting\n'


@pytest.mark.parametrize('step',['saved-prime','native:glide','worker:amber_gbsa'])
def test_md_upstream_query_exceptions_propagate_before_selection_or_native_actions(gates,step):
    failure=RuntimeError('unknown upstream status');gates.error=(step,failure)
    with pytest.raises(RuntimeError) as caught:
        md_gbsa.run(gates.args,5)
    assert caught.value is failure
    assert gates.events==gates.sequence[:gates.sequence.index(step)+1]


def test_disabled_md_without_saved_selection_skips_upstream_inspection(gates,capsys):
    gates.args.md_gbsa_enabled=False
    assert md_gbsa.run(gates.args,5) is None
    assert gates.events==['selection']
    assert capsys.readouterr().out=='MD-GBSA disabled; no saved work to recover.\n'
