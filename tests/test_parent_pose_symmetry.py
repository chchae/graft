"""Full-parent symmetry reduction preserves exhaustive physical correspondences."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from graft.chemistry.combichem3d import parent_pose
from test_parent_tautomer import parent_row


def test_actual_384_by_384_full_parent_preserves_exhaustive_minimum(monkeypatch):
    data = json.loads((Path(__file__).parent/'data/parent_tautomer/cmpid_332726.json').read_text())
    row, original = parent_row(Chem.MolFromMolBlock(data['original_molblock']), data['core_indices'])
    row['compound_id'] = 332726
    docked = Chem.MolFromMolBlock(data['docked_molblock'])
    _, query = parent_pose._growth_query(Chem.MolToSmarts(original))
    left = original.GetSubstructMatches(query, uniquify=False, useChirality=True, maxMatches=4097)
    right = docked.GetSubstructMatches(query, uniquify=False, useChirality=True, maxMatches=4097)
    assert len(left) == len(right) == 384
    assert len(left)*len(right) > 65536
    # Independently evaluate the former exhaustive Cartesian search in numeric
    # chunks. No symmetry reduction is used to construct this expected value.
    xyz = original.GetConformer().GetPositions()
    poses = docked.GetConformer().GetPositions()[np.asarray(right)]
    exhaustive = min(float(np.square(xyz[list(a)][None,:,:]-poses).sum(axis=(1,2)).min()) for a in left)
    before = copy.deepcopy(row)
    pose_block = Chem.MolToMolBlock(docked)
    monkeypatch.setattr(parent_pose.rdFMCS, 'FindMCS', lambda *a, **k: pytest.fail('Full match must avoid MCS'))
    bound = parent_pose.constrain_parent(row,docked,timeout=5)
    ref = bound['docking_reference']
    a = ref['parent_atom_indices']
    b = [i-1 for i in ref['reference_atom_indices']]
    assert tuple(a) == min(left)
    assert len(a) == len(set(a)) == len(set(b)) == 78
    actual = float(np.square(xyz[a]-docked.GetConformer().GetPositions()[b]).sum())
    assert actual == pytest.approx(exhaustive,abs=1e-8)
    assert row == before
    assert Chem.MolToMolBlock(docked) == pose_block
    assert Chem.MolToSmiles(Chem.MolFromMolBlock(bound['molblock'])) == Chem.MolToSmiles(original)
    assert len(bound['history']['constraint_core']) == 78
    assert {r['uid'] for r in row['history']['core']} <= {r['uid'] for r in bound['history']['constraint_core']}
    assert parent_pose.constrain_parent(row,docked,timeout=5)['docking_reference'] == ref


@pytest.mark.parametrize('explicit_h', [False,True])
def test_1296_fold_symmetry_keeps_coordinate_identity_after_reordering(explicit_h):
    original = Chem.MolFromSmiles('CC(C)(C)c1cc(C(C)(C)C)cc(C(C)(C)C)c1')
    row, original = parent_row(original,original.GetSubstructMatch(Chem.MolFromSmiles('c1ccccc1')))
    docked = Chem.AddHs(original,addCoords=True) if explicit_h else Chem.Mol(original)
    docked = Chem.RenumberAtoms(docked,list(reversed(range(docked.GetNumAtoms()))))
    bound = parent_pose.constrain_parent(row,docked,timeout=5)
    ref = bound['docking_reference']
    assert len(ref['parent_atom_indices']) == original.GetNumAtoms()
    for i,j in zip(ref['parent_atom_indices'],ref['reference_atom_indices']):
        assert list(original.GetConformer().GetAtomPosition(i)) == pytest.approx(list(docked.GetConformer().GetAtomPosition(j-1)))


def test_full_parent_single_match_limit_still_fails_closed(monkeypatch):
    row, original = parent_row(Chem.MolFromSmiles('CCc1ccccc1'),list(range(8)))
    monkeypatch.setattr(parent_pose.Chem.Mol,'GetSubstructMatches',lambda *a,**k:((),)*4097)
    with pytest.raises(ValueError,match='match limit'):
        parent_pose.constrain_parent(row,original,timeout=5)
