"""Independent fingerprint factory and frozen original Morgan observations."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from rdkit import Chem

from graft.chemistry import fingerprints

OBSERVED=json.loads((Path(__file__).parent/'data/refactoring/fingerprint-current.json').read_text())


def test_fingerprint_leaf_import_constructs_no_generator_and_loads_no_workflow():
    code='''
import importlib
import sqlite3
import subprocess
import sys
from rdkit.Chem import rdFingerprintGenerator
real=rdFingerprintGenerator.GetMorganGenerator
calls=[]
def factory(*args,**kwargs):
    calls.append((args,kwargs));return real(*args,**kwargs)
def forbidden(*args,**kwargs):
    raise AssertionError('Fingerprint leaf initialized workflow I/O')
rdFingerprintGenerator.GetMorganGenerator=factory
sqlite3.connect=forbidden
subprocess.run=forbidden
subprocess.Popen=forbidden
module=importlib.import_module('graft.chemistry.fingerprints')
assert calls==[]
assert not {'graft._core','graft.database','graft.config'} & sys.modules.keys()
first=module.create_morgan_generator()
second=module.create_morgan_generator()
assert first is not second
assert calls==[((),{'radius':2,'fpSize':2048})]*2
'''
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)


@pytest.mark.parametrize('record',OBSERVED['fingerprints'],ids=[r['smiles'] for r in OBSERVED['fingerprints']])
def test_factory_preserves_original_info_bit_size_and_entire_bit_vector(record):
    generator=fingerprints.create_morgan_generator()
    assert generator.GetInfoString()==OBSERVED['generator_info']
    options=generator.GetOptions()
    assert (options.radius,options.fpSize,options.includeChirality)==(2,2048,False)
    vector=generator.GetFingerprint(Chem.MolFromSmiles(record['smiles']))
    assert vector.GetNumBits()==record['num_bits']
    assert list(vector.GetOnBits())==record['on_bits']


@pytest.mark.parametrize('record',OBSERVED['similarities'],ids=[str(i) for i in range(len(OBSERVED['similarities']))])
def test_pure_and_legacy_tanimoto_match_the_original_frozen_outcomes(record):
    from graft.chemistry.similarity import tanimoto
    def decode(value):
        return value['bytes'].encode('ascii') if isinstance(value,dict) else value
    a,b=decode(record['a']),decode(record['b'])
    generator=fingerprints.create_morgan_generator()
    operations=[lambda:tanimoto(a,b),
                lambda:fingerprints.tanimoto_with_fingerprint(a,b,generator.GetFingerprint)]
    for operation in operations:
        if 'error' in record:
            error={'TypeError':TypeError,'ValueError':ValueError}[record['error']]
            with pytest.raises(error) as caught:operation()
            if 'message' in record:assert str(caught.value)==record['message']
        else:
            result=operation()
            assert type(result) is float and result==record['value']


def test_pure_tanimoto_runs_without_loading_core_database_or_config():
    code='''
import sqlite3
import subprocess
import sys
from graft.chemistry.fingerprints import create_morgan_generator,tanimoto_with_fingerprint

def forbidden(*args,**kwargs):
    raise AssertionError('Pure similarity initialized workflow I/O')

sqlite3.connect=forbidden
subprocess.run=forbidden
subprocess.Popen=forbidden
generator=create_morgan_generator()
assert tanimoto_with_fingerprint('CCO','CCN',generator.GetFingerprint)==1/3
assert not {'graft._core','graft.database','graft.config'} & sys.modules.keys()
'''
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)
