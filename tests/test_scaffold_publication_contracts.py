"""Saved scaffold validation must precede SQLite/publication/Namespace changes."""
from pathlib import Path
import json

import pytest

from graft import _core, database
from graft.chemistry import scaffold as module
from graft.paths import stage_directory
from test_iteration_scaffold import project


@pytest.fixture
def saved_case(project,monkeypatch):
    events=[]
    snapshot=dict(mcs_smarts='[#6]',scaffold_smiles='CC([*])C[*]',source='preserved',source_ids=[1],generation_method='reinvent4')
    project.mcs_smarts='original args core'
    project.libinvent_scaffold_smiles='original args scaffold'
    project.iteration_mcs=True
    original=database.iteration_scaffold
    def query(path,iteration,record=None):
        events.append('save' if record is not None else 'read')
        return original(path,iteration,record)
    monkeypatch.setattr(database,'iteration_scaffold',query)
    monkeypatch.setattr(module,'log_iteration_mcs',lambda *a,**kw:events.append('log'))
    def forbid(*a,**kw):
        pytest.fail('Saved scaffold recovery recomputed the scientific MCS')
    monkeypatch.setattr(_core,'prepare_libinvent_scaffold',forbid)
    return project,snapshot,events,original


@pytest.mark.parametrize('explicit_docking',[False,True])
def test_saved_scaffold_publication_bytes_and_namespace_update_order(saved_case,monkeypatch,explicit_docking):
    args,snapshot,events,db=saved_case
    if explicit_docking:snapshot['docking_mcs_smarts']='[#6]-[#6]'
    db(args.db_path,3,snapshot);events.clear()
    directory=stage_directory(args.output,3,'scaffold');publish=module._publish
    expected=[('mcs.smarts','[#6]\n'),('libinvent_scaffold.smi','CC([*])C[*]\n'),
              ('scaffold_info.json',json.dumps(snapshot,indent=2,sort_keys=True)+'\n'),
              ('docking_mcs.smarts',snapshot.get('docking_mcs_smarts','[#6]')+'\n')]
    def write(path,text):
        events.append(('publish',path.name,text))
        assert args.mcs_smarts=='original args core'
        assert args.libinvent_scaffold_smiles=='original args scaffold'
        assert db(args.db_path,3)==snapshot
        publish(path,text)
    monkeypatch.setattr(module,'_publish',write)
    module.prepare_iteration_scaffold(args,3,include_generation=False)
    assert events==['read','save']+[('publish',name,text) for name,text in expected]+['log']
    assert {p.name:p.read_bytes() for p in directory.iterdir()}=={name:text.encode() for name,text in expected}
    assert args.generation_mcs_smarts=='[#6]'
    assert args.mcs_smarts==snapshot.get('docking_mcs_smarts','[#6]')
    assert args.libinvent_scaffold_smiles==snapshot['scaffold_smiles']
    assert args.libinvent_scaffold_file==directory/'libinvent_scaffold.smi'


@pytest.mark.parametrize('problem,message',[
    ('saved_mcs','Invalid saved MCS/scaffold.'),
    ('docking_mcs','Iteration 3: invalid docking constraint.'),
    ('containment','Iteration 3: generation scaffold does not contain the fixed docking core.'),
    ('references','Iteration 3: docking core matches no original reference pose; provide a compatible reference pose or an explicit reference-mcs.'),
    ('attachments','REINVENT4 generation requires exactly two scaffold attachments'),
])
def test_saved_scaffold_validation_errors_do_not_publish_or_change_namespace(saved_case,monkeypatch,problem,message):
    args,snapshot,events,db=saved_case
    if problem=='saved_mcs':snapshot['mcs_smarts']='not[smarts'
    elif problem=='docking_mcs':snapshot['docking_mcs_smarts']='not[smarts'
    elif problem=='containment':snapshot['docking_mcs_smarts']='[#8]'
    elif problem=='attachments':snapshot['scaffold_smiles']='CC[*]'
    refs=[]
    def references(*a):
        refs.append('read')
        return []
    monkeypatch.setattr(_core,'get_reference_compounds',references)
    db(args.db_path,3,snapshot);events.clear()
    with pytest.raises(RuntimeError) as caught:
        module.prepare_iteration_scaffold(args,3,create=(problem=='attachments'))
    assert str(caught.value)==message
    assert events==['read']
    assert refs==(['read'] if problem=='references' else [])
    assert db(args.db_path,3)==snapshot
    assert args.mcs_smarts=='original args core'
    assert args.libinvent_scaffold_smiles=='original args scaffold'
    assert not stage_directory(args.output,3,'scaffold').exists()


def test_scaffold_publish_failure_preserves_prior_order_and_namespace(saved_case,monkeypatch):
    args,snapshot,events,db=saved_case;db(args.db_path,3,snapshot);events.clear()
    publish=module._publish;failure=OSError('scaffold output unavailable')
    def write(path,text):
        events.append(path.name)
        if path.name=='libinvent_scaffold.smi':raise failure
        publish(path,text)
    monkeypatch.setattr(module,'_publish',write)
    with pytest.raises(OSError) as caught:
        module.prepare_iteration_scaffold(args,3)
    assert caught.value is failure
    assert events==['read','save','mcs.smarts','libinvent_scaffold.smi']
    directory=stage_directory(args.output,3,'scaffold')
    assert (directory/'mcs.smarts').read_bytes()==b'[#6]\n'
    assert not (directory/'scaffold_info.json').exists()
    assert args.mcs_smarts=='original args core'
