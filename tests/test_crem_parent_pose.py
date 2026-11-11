"""Parent-pose constraints and ancestry; synthetic geometry is not affinity validation."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from graft import crem_docking_poses, glide_inputs
from graft.chemistry.combichem3d import history as provenance
from graft.chemistry.combichem3d.assembly import assemble
from graft.chemistry.combichem3d.parent_pose import constrain_parent
from graft.chemistry.combichem3d.search import fixed_coordinates, core_deviation


@pytest.fixture
def parent():
    mol = Chem.AddHs(Chem.MolFromSmiles('CCc1ccccc1'))
    assert AllChem.EmbedMolecule(mol, randomSeed=37) == 0
    mol = Chem.RemoveHs(mol)
    for atom in mol.GetAtoms():
        for key, value in dict(uid=f'a{atom.GetIdx()}', branch='core', fragment='core', added_iteration='0').items():
            atom.SetProp('_mn_' + key, value)
    history = dict(iteration=1, lineage_id='test',
        core=[dict(core_index=i-2, uid=f'a{i}', xyz=list(mol.GetConformer().GetAtomPosition(i))) for i in range(2,8)])
    history = provenance.snapshot(mol, history)
    return dict(compound_id=42, molblock=Chem.MolToMolBlock(mol), history=history), mol


def test_parent_docking_mcs_freezes_sidechain_and_survives_new_child(parent):
    row, mol = parent
    original = copy.deepcopy(row)
    # Change atom order, include explicit hydrogens and move the docking pose.
    docked = Chem.RenumberAtoms(Chem.AddHs(mol, addCoords=True),
                               list(reversed(range(Chem.AddHs(mol).GetNumAtoms()))))
    for i, xyz in enumerate(docked.GetConformer().GetPositions()):
        docked.GetConformer().SetAtomPosition(i, xyz + [1,2,3])
    bound = constrain_parent(row, docked, timeout=5)
    assert row == original
    assert len(bound['history']['constraint_core']) == 8  # ring plus old sidechain
    assert len(bound['history']['core']) == 6  # branch/depth core stays distinct
    assert bound['history']['generation_parent_id'] == 42
    restored = provenance.restore(Chem.MolFromMolBlock(bound['molblock']), bound['history'])
    child, history = assemble(restored, bound['history'], ['a0'], ['*CO'], 'new')
    assert len(fixed_coordinates(child, history)) == 8
    assert history['generation_parent_id'] == 42
    assert set(fixed_coordinates(child, history)) == {f'a{i}' for i in range(8)}
    assert core_deviation(restored, bound['history']) == pytest.approx(0)
    restored.GetConformer().SetAtomPosition(0, [100,100,100])
    assert core_deviation(restored, bound['history']) > 1  # sidechain is checked too
    mapping = bound['docking_reference']['reference_atom_indices']
    for core, index in zip(bound['history']['constraint_core'], mapping):
        assert core['xyz'] == pytest.approx(list(docked.GetConformer().GetAtomPosition(index-1)))


def test_parent_mcs_still_matches_growth_replacing_a_chiral_hydrogen():
    mol = Chem.AddHs(Chem.MolFromSmiles('C[C@H](F)c1ccccc1'))
    assert AllChem.EmbedMolecule(mol, randomSeed=37) == 0
    mol = Chem.RemoveHs(mol)
    for atom in mol.GetAtoms():
        for key, value in dict(uid=f'a{atom.GetIdx()}', branch='core', fragment='core', added_iteration='0').items():
            atom.SetProp('_mn_' + key, value)
    history = provenance.snapshot(mol, dict(iteration=1, lineage_id='chiral',
        core=[dict(core_index=i, uid=f'a{i}', xyz=list(mol.GetConformer().GetAtomPosition(i)))
              for i in range(mol.GetNumAtoms())]))
    row = dict(compound_id=42, molblock=Chem.MolToMolBlock(mol), history=history)
    bound = constrain_parent(row, mol, timeout=5)
    original = provenance.restore(Chem.MolFromMolBlock(bound['molblock']), bound['history'])
    child, _ = assemble(original, bound['history'], ['a1'], ['*CO'], 'chiral-growth')
    query = Chem.MolFromSmarts(bound['docking_reference']['mcs_smarts'])
    assert child.HasSubstructMatch(query, useChirality=True)


@pytest.mark.parametrize('problem', ['timeout', 'incompatible', 'nonfinite'])
def test_parent_reference_failure_has_no_iter0_fallback(parent, monkeypatch, problem):
    row, mol = parent
    if problem == 'timeout':
        from graft.chemistry.combichem3d import parent_pose
        mol = Chem.Mol(mol)
        mol.GetAtomWithIdx(0).SetAtomicNum(7)
        Chem.SanitizeMol(mol)
        monkeypatch.setattr(parent_pose.rdFMCS, 'FindMCS', lambda *a, **k: SimpleNamespace(canceled=True))
    elif problem == 'incompatible':
        mol = Chem.MolFromSmiles('CCCC')
        AllChem.EmbedMolecule(mol, randomSeed=37)
    else:
        mol.GetConformer().SetAtomPosition(0, [float('nan'),0,0])
    with pytest.raises(ValueError):
        constrain_parent(row, mol, timeout=5)


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'score'])
def test_native_export_requires_exact_saved_best_pose(tmp_path, monkeypatch, problem):
    st = SimpleNamespace(title='CMPID_42', property={'r_i_docking_score': -7.0})
    rows = [] if problem == 'missing' else [st,st] if problem == 'duplicate' else [st]
    class Reader:
        def __init__(self, *a): pass
        def __enter__(self): return iter(rows)
        def __exit__(self, *a): pass
    monkeypatch.setitem(sys.modules, 'schrodinger', SimpleNamespace(
        structure=SimpleNamespace(StructureReader=Reader)))
    output = tmp_path/'parents.sdf'
    with pytest.raises(ValueError, match='Missing|Duplicate|differs'):
        crem_docking_poses.export(dict(source='best.maegz', output=str(output),
                                     scores={'42': -8 if problem == 'score' else -7}))
    assert not output.exists()


def test_native_glide_uses_each_parent_mcs_and_frozen_mapping(tmp_path, monkeypatch):
    refs = [SimpleNamespace(title='CMPID_42'), SimpleNamespace(title='CMPID_43')]
    seen = []
    class Reader:
        def __init__(self, *a): pass
        def __enter__(self): return iter(refs)
        def __exit__(self, *a): pass
    class Writer:
        def __init__(self, path): self.path = Path(path)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def append(self, st): self.path.write_text(st.title)
    def match(st, smarts, unique):
        seen.append((st.title, smarts, unique))
        return [[1,2], [3,4]]
    monkeypatch.setitem(sys.modules, 'schrodinger', SimpleNamespace(
        structure=SimpleNamespace(StructureReader=Reader, StructureWriter=Writer),
        adapter=SimpleNamespace(evaluate_smarts=match)))
    request = dict(references='parents.sdf', smarts='OLD', groups=[
        dict(id=42, name='CMPID_42', smarts='CC', core_atoms=[3,4], output=str(tmp_path/'42.maegz')),
        dict(id=43, name='CMPID_43', smarts='CN', core_atoms=[1,2], output=str(tmp_path/'43.maegz'))])
    assert glide_inputs.prepare(request) == {'42': '3,4', '43': '1,2'}
    assert seen == [('CMPID_42','CC',False), ('CMPID_43','CN',False)]
    request['groups'][0]['core_atoms'] = [99,100]
    with pytest.raises(RuntimeError, match='mapping'):
        glide_inputs.prepare(request)
