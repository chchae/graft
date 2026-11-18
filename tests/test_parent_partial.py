"""Exact partial pose constraints retain the declared core, not full equivalence."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rdkit import Chem

from graft.chemistry.combichem3d import parent_pose
from test_parent_tautomer import parent_row


@pytest.fixture
def actual_partial_parent():
    data = json.loads((Path(__file__).parent/'data/parent_tautomer/cmpid_256496.json').read_text())
    row, original = parent_row(Chem.MolFromMolBlock(data['original_molblock']), data['core_indices'])
    row['compound_id'] = 256496
    return row, original, Chem.MolFromMolBlock(data['docked_molblock'])


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('explicit_reordered', [False, True])
def test_actual_ring_tautomer_maps_partial_core_without_editing_molecules(actual_partial_parent, reverse, explicit_reordered):
    row, original, docked = actual_partial_parent
    if reverse:
        core = docked.GetSubstructMatch(Chem.MolFromSmarts('c1nc(N)c2cc(-c3ccc(C)cc3)[nH]c2n1'))
        assert core
        row, original = parent_row(docked, core)
        docked = actual_partial_parent[1]
    if explicit_reordered:
        docked = Chem.AddHs(docked, addCoords=True)
        docked = Chem.RenumberAtoms(docked, list(reversed(range(docked.GetNumAtoms()))))
    saved = copy.deepcopy(row)
    pose_block = Chem.MolToMolBlock(docked)
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    reference = bound['docking_reference']
    proof = reference['partial_mapping']
    if reverse:
        # A bounded reverse search may fall back to the exact declared core.
        assert proof['policy'] in ('reference_core_seeded_mcs_v1', 'declared_reference_core_partial_v1')
    else:
        assert proof['policy'] == 'reference_core_seeded_mcs_v1'
    assert len(row['history']['core']) <= proof['matched_atoms'] < proof['original_atoms']
    assert set(proof['omitted_parent_atom_indices']) == set(range(original.GetNumAtoms())) - set(reference['parent_atom_indices'])
    query = Chem.MolFromSmarts(reference['mcs_smarts'])
    assert len(Chem.GetMolFrags(query)) == 1
    assert tuple(reference['parent_atom_indices']) in original.GetSubstructMatches(query, uniquify=False, useChirality=True)
    assert tuple(i-1 for i in reference['reference_atom_indices']) in docked.GetSubstructMatches(query, uniquify=False, useChirality=True)
    core_uids = {r['uid'] for r in row['history']['core']}
    assert core_uids <= {r['uid'] for r in bound['history']['constraint_core']}
    restored = Chem.MolFromMolBlock(bound['molblock'])
    assert Chem.MolToSmiles(restored) == Chem.MolToSmiles(original)
    for i in proof['omitted_parent_atom_indices']:
        assert list(restored.GetConformer().GetAtomPosition(i)) == pytest.approx(
            list(original.GetConformer().GetAtomPosition(i)), abs=1e-4)
    for entry, index in zip(bound['history']['constraint_core'], reference['reference_atom_indices']):
        assert entry['xyz'] == pytest.approx(list(docked.GetConformer().GetAtomPosition(index-1)))
    assert row == saved
    assert Chem.MolToMolBlock(docked) == pose_block
    assert bound['history']['constraint_reference'] == reference
    assert 'tautomer_mapping' not in reference  # No full-molecule equivalence claim.


@pytest.mark.parametrize('result', [
    SimpleNamespace(canceled=True),
    SimpleNamespace(canceled=False, numAtoms=0),
    SimpleNamespace(canceled=False, numAtoms=2, smartsString='CC'),
])
def test_mcs_failure_or_unanchored_result_uses_independently_validated_core(monkeypatch, result):
    original = Chem.MolFromSmiles('CCc1ccccc1')
    row, _ = parent_row(original, original.GetSubstructMatch(Chem.MolFromSmiles('c1ccccc1')))
    _, docked = parent_row(Chem.MolFromSmiles('NCc1ccccc1'), [])
    seen = []
    def search(mols, params):
        seen.append(params)
        return result
    monkeypatch.setattr(parent_pose.rdFMCS, 'FindMCS', search)
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    assert bound['docking_reference']['partial_mapping']['policy'] == 'declared_reference_core_partial_v1'
    assert len(bound['history']['constraint_core']) == 6
    assert seen[0].InitialSeed
    assert seen[0].Timeout == 5
    assert seen[0].AtomCompareParameters.MatchFormalCharge
    assert seen[0].BondCompareParameters.MatchStereo


def test_changed_declared_core_is_rejected_before_mcs(monkeypatch):
    original = Chem.MolFromSmiles('CCc1ccccc1')
    row, _ = parent_row(original, original.GetSubstructMatch(Chem.MolFromSmiles('c1ccccc1')))
    _, docked = parent_row(Chem.MolFromSmiles('CCc1ccccn1'), [])
    monkeypatch.setattr(parent_pose.rdFMCS, 'FindMCS', lambda *a, **k: pytest.fail('Changed core cannot be rescued by MCS'))
    with pytest.raises(ValueError, match='unchanged declared reference core'):
        parent_pose.constrain_parent(row, docked, timeout=5)


def test_partial_mapping_rejects_changed_core_stereochemistry():
    original = Chem.MolFromSmiles('C[C@H](F)c1ccccc1')
    row, _ = parent_row(original, list(range(original.GetNumAtoms())))
    _, docked = parent_row(Chem.MolFromSmiles('C[C@@H](F)c1ccccc1'), [])
    with pytest.raises(ValueError, match='unchanged declared reference core'):
        parent_pose.constrain_parent(row, docked, timeout=5)


def test_partial_mapping_can_omit_unrelated_stereo_change():
    original = Chem.MolFromSmiles('C[C@H](F)c1ccccc1')
    row, _ = parent_row(original, original.GetSubstructMatch(Chem.MolFromSmiles('c1ccccc1')))
    _, docked = parent_row(Chem.MolFromSmiles('C[C@@H](F)c1ccccc1'), [])
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    proof = bound['docking_reference']['partial_mapping']
    assert proof['matched_atoms'] < original.GetNumAtoms()
    assert 1 in proof['omitted_parent_atom_indices']


def test_partial_mapping_records_failed_local_proof(actual_partial_parent, monkeypatch):
    row, _, docked = actual_partial_parent
    def rejected(*a, **k):
        raise ValueError('Unsupported local chemistry')
    monkeypatch.setattr(parent_pose, 'map_keto_enol', rejected)
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    assert bound['docking_reference']['partial_mapping']['full_mapping_rejection'] == 'Unsupported local chemistry'


def test_partial_mapping_symmetry_limit_is_not_bypassed(actual_partial_parent, monkeypatch):
    row, _, docked = actual_partial_parent
    monkeypatch.setattr(parent_pose.Chem.Mol, 'GetSubstructMatches', lambda *a, **k: ((),)*4097)
    with pytest.raises(ValueError, match='match limit'):
        parent_pose.constrain_parent(row, docked, timeout=5)
