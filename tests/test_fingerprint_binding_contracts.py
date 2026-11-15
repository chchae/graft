"""Existing shared-generator lifetime, dynamic lookup and reference-selection rules."""
from contextlib import closing
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from rdkit import Chem

from graft import _core
from graft.chemistry import similarity


def test_core_import_and_reload_construct_one_fresh_generator_each_without_workflow_io():
    code = '''
import importlib
import sqlite3
import subprocess
from rdkit.Chem import rdFingerprintGenerator
real = rdFingerprintGenerator.GetMorganGenerator
calls = []
def factory(*args, **kwargs):
    calls.append((args, kwargs))
    return real(*args, **kwargs)
def forbidden(*args, **kwargs):
    raise AssertionError('Core import initialized workflow I/O')
rdFingerprintGenerator.GetMorganGenerator = factory
sqlite3.connect = forbidden
subprocess.run = forbidden
subprocess.Popen = forbidden
core = importlib.import_module('graft._core')
first = core.FP_GENERATOR
assert calls == [((), {'radius':2, 'fpSize':2048})]
importlib.reload(core)
assert calls == [((), {'radius':2, 'fpSize':2048})] * 2
assert core.FP_GENERATOR is not first
assert core.FP_GENERATOR.GetInfoString() == first.GetInfoString()
'''
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)


def test_core_factory_failure_propagates_the_original_exception():
    code = '''
from rdkit.Chem import rdFingerprintGenerator
failure = RuntimeError('injected generator construction failure')
def fail(*args, **kwargs):
    assert not args and kwargs == {'radius':2, 'fpSize':2048}
    raise failure
rdFingerprintGenerator.GetMorganGenerator = fail
try:
    import graft._core
except RuntimeError as error:
    assert error is failure
else:
    raise AssertionError('Generator construction failure was swallowed')
'''
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)


@pytest.mark.parametrize('binding',['generator','core_module'])
def test_similarity_resolves_generator_again_after_the_first_fingerprint(monkeypatch,binding):
    original=_core.FP_GENERATOR;events=[]
    class Second:
        def GetFingerprint(self,mol):
            events.append(('second',Chem.MolToSmiles(mol)))
            return original.GetFingerprint(mol)
    second=Second()
    class First:
        def GetFingerprint(self,mol):
            events.append(('first',Chem.MolToSmiles(mol)))
            if binding=='generator':
                monkeypatch.setattr(_core,'FP_GENERATOR',second)
            else:
                monkeypatch.setattr(similarity,'_core',SimpleNamespace(FP_GENERATOR=second))
            return original.GetFingerprint(mol)
    monkeypatch.setattr(_core,'FP_GENERATOR',First())
    assert similarity.tanimoto('CCO','CCN') == 1/3
    assert events==[('first','CCO'),('second','CCN')]


@pytest.mark.parametrize('fail_at',[None,'parse:CCO','parse:CCN','fp:CCO','fp:CCN','similarity'])
def test_similarity_parser_fingerprint_and_metric_order_and_exception_identity(monkeypatch,fail_at):
    events=[];failure=RuntimeError('injected chemistry boundary failure')
    parse=Chem.MolFromSmiles;generator=_core.FP_GENERATOR;metric=similarity.DataStructs.TanimotoSimilarity
    def step(name):
        events.append(name)
        if fail_at==name:raise failure
    def parsing(value):
        step('parse:'+value);return parse(value)
    class Generator:
        def GetFingerprint(self,mol):
            step('fp:'+Chem.MolToSmiles(mol));return generator.GetFingerprint(mol)
    def tanimoto(a,b):
        step('similarity');return metric(a,b)
    monkeypatch.setattr(Chem,'MolFromSmiles',parsing)
    monkeypatch.setattr(_core,'FP_GENERATOR',Generator())
    monkeypatch.setattr(similarity.DataStructs,'TanimotoSimilarity',tanimoto)
    sequence=['parse:CCO','parse:CCN','fp:CCO','fp:CCN','similarity']
    if fail_at:
        with pytest.raises(RuntimeError) as caught:similarity.tanimoto('CCO','CCN')
        assert caught.value is failure
        assert events==sequence[:sequence.index(fail_at)+1]
    else:
        assert similarity.tanimoto('CCO','CCN')==1/3
        assert events==sequence


def test_reference_fingerprints_and_assignment_share_current_generator_and_keep_ties(tmp_path,monkeypatch):
    path=tmp_path/'references.sqlite'
    with closing(sqlite3.connect(path)) as conn,conn:
        _core.create_sqlite_schema(conn)
        conn.executemany('INSERT INTO compound(id,name,smiles,iteration,state,docking_score,similar_to,similarity) '
                         'VALUES (?,?,?,?,?,?,?,?)',[
            (10,'REF1','CCO',0,'reference',None,None,None),
            (20,'REF2','OCC',0,'reference',None,None,None),
            (30,'REF3','CCN',0,'reference',None,None,None),
            (101,'CMPID_101','OCC',1,'ligprepped',None,None,None),
            (102,'CMPID_102','CCN',1,'ligprepped',None,None,None),
            (103,'CMPID_103','not_smiles',1,'ligprepped',None,20,.5),
            (104,'CMPID_104','CCO',1,'docked',-8.,30,.25)])
    original=_core.FP_GENERATOR;calls=[]
    class Generator:
        def GetFingerprint(self,mol):
            calls.append(Chem.MolToSmiles(mol));return original.GetFingerprint(mol)
    monkeypatch.setattr(_core,'FP_GENERATOR',Generator())
    references=_core.get_reference_compounds(path)
    assert [r['id'] for r in references]==[10,20,30]
    assert calls==['CCO','CCO','CCN']
    assert tuple(references[0]['fp'].GetOnBits())==(80,222,294,807,1057,1410)
    calls.clear()
    assert _core.assign_reference_compounds(1,SimpleNamespace(db_path=path,mcs_smarts=None))==2
    assert calls==['CCO','CCO','CCN','CCO','CCN']
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT id,similar_to,similarity,state FROM compound WHERE iteration=1 ORDER BY id').fetchall()==[
            (101,10,1.,'ligprepped'),(102,30,1.,'ligprepped'),
            (103,None,None,'ligprepped'),(104,30,.25,'docked')]
