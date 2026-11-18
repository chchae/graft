"""Local state equivalence and exact shared-core constraints, not affinity tests."""
import copy
import json
from pathlib import Path

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from graft.chemistry.combichem3d import history as provenance, parent_pose, parent_tautomer


def parent_row(mol, core):
    mol = Chem.Mol(mol)
    if not mol.GetNumConformers():
        mol = Chem.AddHs(mol)
        assert AllChem.EmbedMolecule(mol, randomSeed=37) == 0
        mol = Chem.RemoveHs(mol)
    for atom in mol.GetAtoms():
        for key, value in dict(uid=f'a{atom.GetIdx()}', branch='core', fragment='core', added_iteration='0').items():
            atom.SetProp('_mn_' + key, value)
    history = provenance.snapshot(mol, dict(iteration=6, lineage_id='tautomer-test',
        core=[dict(core_index=j, uid=f'a{i}', xyz=list(mol.GetConformer().GetAtomPosition(i)))
              for j, i in enumerate(core)]))
    return dict(compound_id=218153, molblock=Chem.MolToMolBlock(mol), history=history), mol


@pytest.fixture
def actual_parent():
    data = json.loads((Path(__file__).parent/'data/parent_tautomer/cmpid_218153.json').read_text())
    row, original = parent_row(Chem.MolFromMolBlock(data['original_molblock']), data['core_indices'])
    return row, original, Chem.MolFromMolBlock(data['docked_molblock'])


@pytest.fixture(params=[258505, 267607])
def alpha_amine_parent(request):
    data = json.loads((Path(__file__).parent/f'data/parent_tautomer/cmpid_{request.param}.json').read_text())
    row, original = parent_row(Chem.MolFromMolBlock(data['original_molblock']), data['core_indices'])
    row['compound_id'] = request.param
    return row, original, Chem.MolFromMolBlock(data['docked_molblock'])


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('explicit_reordered', [False, True])
def test_actual_alpha_amine_zwitterion_preserves_chemistry_and_core(alpha_amine_parent, monkeypatch, reverse, explicit_reordered):
    row, original, docked = alpha_amine_parent
    if reverse:
        original, docked = docked, original
        row, original = parent_row(original, original.GetSubstructMatch(Chem.MolFromSmarts('c1ncnc2[nH]ccc12')))
    if explicit_reordered:
        docked = Chem.AddHs(docked, addCoords=True)
        docked = Chem.RenumberAtoms(docked, list(reversed(range(docked.GetNumAtoms()))))
    saved = copy.deepcopy(row)
    pose_block = Chem.MolToMolBlock(docked)
    no_mcs(monkeypatch)
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    reference = bound['docking_reference']
    proof = reference['tautomer_mapping']
    assert proof['policy'] == parent_tautomer.AMINE_POLICY
    assert (proof['original_charge'], proof['pose_charge']) == (0, 0)
    assert len(proof['parent_atom_indices']) == original.GetNumAtoms()
    assert len(set(proof['reference_atom_indices'])) == original.GetNumAtoms()
    assert len(proof['protonated_amine_parent_atom_indices']) == 1
    changed = set(proof['protonated_amine_parent_atom_indices']) | {
        i for site in proof['reactive_parent_atom_indices'] for i in site}
    assert len(changed) == 4
    assert not changed & set(reference['parent_atom_indices'])
    query = Chem.MolFromSmarts(reference['mcs_smarts'])
    assert len(Chem.GetMolFrags(query)) == 1
    assert tuple(reference['parent_atom_indices']) in original.GetSubstructMatches(query, uniquify=False, useChirality=True)
    assert tuple(i-1 for i in reference['reference_atom_indices']) in docked.GetSubstructMatches(query, uniquify=False, useChirality=True)
    assert {r['uid'] for r in row['history']['core']} <= {r['uid'] for r in bound['history']['constraint_core']}
    for entry, index in zip(bound['history']['constraint_core'], reference['reference_atom_indices']):
        assert entry['xyz'] == pytest.approx(list(docked.GetConformer().GetAtomPosition(index-1)))
    assert Chem.MolToSmiles(Chem.MolFromMolBlock(bound['molblock'])) == Chem.MolToSmiles(original)
    assert row == saved
    assert Chem.MolToMolBlock(docked) == pose_block
    assert bound['history']['constraint_reference'] == reference


@pytest.mark.parametrize('problem', ['neutral_enol', 'inverted', 'lost_stereo', 'isotope', 'extra_charge', 'core'])
def test_alpha_amine_exception_refuses_other_changes(alpha_amine_parent, monkeypatch, problem):
    row, original, docked = alpha_amine_parent
    if problem == 'neutral_enol':
        oxygen = next(a for a in docked.GetAtoms() if a.GetFormalCharge() == -1)
        oxygen.SetFormalCharge(0)
        oxygen.SetNumExplicitHs(1)
        Chem.SanitizeMol(docked)
    elif problem in ('inverted', 'lost_stereo'):
        atom = next(a for a in docked.GetAtoms() if a.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED)
        if problem == 'inverted':
            atom.InvertChirality()
        else:
            atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    elif problem == 'isotope':
        next(a for a in docked.GetAtoms() if a.GetFormalCharge() == 1).SetIsotope(15)
    elif problem == 'extra_charge':
        atom = next(a for a in docked.GetAtoms() if a.GetAtomicNum() == 7 and a.GetDegree() == 3)
        atom.SetFormalCharge(1)
        atom.SetNumExplicitHs(1)
        Chem.SanitizeMol(docked)
    else:
        # A protonated amine must be excluded from the shared core too.
        amine = next(a.GetIdx() for a in original.GetAtoms() if a.GetAtomicNum() == 7
                     and any(n.GetIdx() == 36 for n in a.GetNeighbors()))
        row['history']['core'].append(dict(core_index=17, uid=f'a{amine}', xyz=[0, 0, 0]))
    no_mcs(monkeypatch)
    with pytest.raises(ValueError, match='keto/enol'):
        parent_tautomer.map_keto_enol(original, docked,
            {int(r['uid'][1:]) for r in row['history']['core']}, timeout=5)


@pytest.mark.parametrize(('keto', 'enolate'), [
    # A second ammonium cannot be compensated by the one enolate oxygen.
    ('CNC(NC)C(=O)c1ccccc1', 'C[NH2+]C([NH2+]C)=C([O-])c1ccccc1'),
    # An adjacent amide is outside the secondary-amine exception.
    ('CC(=O)NC(C)C(=O)c1ccccc1', 'CC(=O)[NH2+]C(C)=C([O-])c1ccccc1'),
    # An aniline nitrogen is outside the aliphatic-amine exception.
    ('c1ccccc1NC(C)C(=O)c1ccccc1', 'c1ccccc1[NH2+]C(C)=C([O-])c1ccccc1'),
    # Conjugation on the other N substituent is not the proven local change.
    ('C=CNC(C)C(=O)c1ccccc1', 'C=C[NH2+]C(C)=C([O-])c1ccccc1'),
])
def test_alpha_amine_exception_has_bounded_chemistry(keto, enolate):
    original = Chem.MolFromSmiles(keto)
    _, original = parent_row(original, [])
    _, target = parent_row(Chem.MolFromSmiles(enolate), [])
    with pytest.raises(ValueError, match='unrelated chemistry'):
        parent_tautomer.map_keto_enol(original, target, set(), timeout=5)


def no_mcs(monkeypatch):
    monkeypatch.setattr(parent_pose.rdFMCS, 'FindMCS',
                        lambda *a, **k: pytest.fail('Proven local tautomer must avoid global MCS'))


def test_actual_63_atom_enolate_preserves_parent_and_exact_core(actual_parent, monkeypatch):
    row, original, docked = actual_parent
    saved = copy.deepcopy(row)
    pose_block = Chem.MolToMolBlock(docked)
    no_mcs(monkeypatch)
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    reference = bound['docking_reference']
    proof = reference['tautomer_mapping']
    assert (proof['original_charge'], proof['pose_charge']) == (0, -1)
    assert len(proof['reference_atom_indices']) == 63
    assert len(set(proof['reference_atom_indices'])) == 63
    assert len(bound['history']['constraint_core']) == 52
    assert {r['uid'] for r in row['history']['core']} <= {r['uid'] for r in bound['history']['constraint_core']}
    query = Chem.MolFromSmarts(reference['mcs_smarts'])
    assert len(Chem.GetMolFrags(query)) == 1
    assert original.HasSubstructMatch(query, useChirality=True)
    assert docked.HasSubstructMatch(query, useChirality=True)
    assert tuple(reference['parent_atom_indices']) in original.GetSubstructMatches(query, uniquify=False, useChirality=True)
    assert tuple(i-1 for i in reference['reference_atom_indices']) in docked.GetSubstructMatches(query, uniquify=False, useChirality=True)
    for entry, index in zip(bound['history']['constraint_core'], reference['reference_atom_indices']):
        assert entry['xyz'] == pytest.approx(list(docked.GetConformer().GetAtomPosition(index-1)))
    restored = Chem.MolFromMolBlock(bound['molblock'])
    assert Chem.MolToSmiles(restored) == Chem.MolToSmiles(original)
    assert row == saved
    assert Chem.MolToMolBlock(docked) == pose_block
    assert bound['history']['constraint_reference'] == reference


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('enolate', [False, True])
def test_neutral_enol_and_enolate_both_directions(actual_parent, monkeypatch, reverse, enolate):
    _, keto, enol = actual_parent
    if not enolate:
        oxygen = next(a for a in enol.GetAtoms() if a.GetFormalCharge() == -1)
        oxygen.SetFormalCharge(0)
        oxygen.SetNumExplicitHs(1)
        Chem.SanitizeMol(enol)
    original, docked = (enol, keto) if reverse else (keto, enol)
    # The ring scaffold is unchanged; locate it by matching the actual core query.
    core_query = Chem.MolFromSmarts('c1ncnc2[nH]ccc12')
    core = original.GetSubstructMatch(core_query)
    assert core
    row, _ = parent_row(original, core)
    no_mcs(monkeypatch)
    docked = Chem.AddHs(docked, addCoords=True)
    docked = Chem.RenumberAtoms(docked, list(reversed(range(docked.GetNumAtoms()))))
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    assert len(bound['docking_reference']['tautomer_mapping']['parent_atom_indices']) == 63
    query = Chem.MolFromSmarts(bound['docking_reference']['mcs_smarts'])
    assert original.HasSubstructMatch(query, useChirality=True)
    assert docked.HasSubstructMatch(query, useChirality=True)


@pytest.mark.parametrize('problem', ['inverted', 'lost_stereo', 'isotope', 'extra_charge', 'core'])
def test_local_mapping_refuses_unrelated_changes(actual_parent, monkeypatch, problem):
    row, original, docked = actual_parent
    if problem in ('inverted', 'lost_stereo'):
        atom = next(a for a in docked.GetAtoms() if a.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED)
        if problem == 'inverted':
            atom.InvertChirality()
        else:
            atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    elif problem == 'isotope':
        docked.GetAtomWithIdx(0).SetIsotope(13)
    elif problem == 'extra_charge':
        atom = next(a for a in docked.GetAtoms() if a.GetAtomicNum() == 7 and a.GetDegree() == 3)
        atom.SetFormalCharge(1)
        atom.SetNumExplicitHs(1)
        Chem.SanitizeMol(docked)
    else:
        # Original alpha carbon loses tetrahedral stereo on enolization.
        row['history']['core'].append(dict(core_index=17, uid='a36', xyz=[0, 0, 0]))
    no_mcs(monkeypatch)
    with pytest.raises(ValueError, match='keto/enol'):
        parent_tautomer.map_keto_enol(original, docked,
            {int(r['uid'][1:]) for r in row['history']['core']}, timeout=5)


def test_symmetry_limit_and_timeout_fail_closed(actual_parent, monkeypatch):
    _, original, docked = actual_parent
    monkeypatch.setattr(parent_tautomer, 'MATCH_LIMIT', 2)
    with pytest.raises(ValueError, match='match limit'):
        parent_tautomer.map_keto_enol(original, docked, set(range(17)), timeout=5)
    monkeypatch.setattr(parent_tautomer, 'MATCH_LIMIT', 4097)
    ticks = iter([0, 6])
    monkeypatch.setattr(parent_tautomer.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(ValueError, match='timed out'):
        parent_tautomer.map_keto_enol(original, docked, set(range(17)), timeout=5)


def test_unsupported_bond_change_has_no_tautomer_proof(actual_parent):
    _, original, docked = actual_parent
    bond = next(b for b in docked.GetBonds() if not b.IsInRing() and b.GetBondType() == Chem.BondType.SINGLE
                and all(a.GetSymbol() == 'C' and a.GetDegree() == 2 for a in (b.GetBeginAtom(), b.GetEndAtom())))
    bond.SetBondType(Chem.BondType.DOUBLE)
    Chem.SanitizeMol(docked)
    assert parent_tautomer.map_keto_enol(original, docked, set(range(17)), timeout=5) is None


@pytest.mark.parametrize(('keto', 'enol', 'site_count'), [
    ('CC(=O)Cc1ccccc1', 'CC(O)=Cc1ccccc1', 1),
    ('CC(=O)Cc1ccc(CC(C)=O)cc1', 'CC(O)=Cc1ccc(C=C(C)O)cc1', 2),
])
def test_one_or_two_disjoint_local_sites(keto, enol, site_count, monkeypatch):
    original = Chem.MolFromSmiles(keto)
    core = original.GetSubstructMatch(Chem.MolFromSmiles('c1ccccc1'))
    row, _ = parent_row(original, core)
    _, docked = parent_row(Chem.MolFromSmiles(enol), [])
    no_mcs(monkeypatch)
    bound = parent_pose.constrain_parent(row, docked, timeout=5)
    assert len(bound['docking_reference']['tautomer_mapping']['reactive_parent_atom_indices']) == site_count
    assert len(bound['history']['core']) == 6
    assert Chem.MolFromSmiles(enol).HasSubstructMatch(
        Chem.MolFromSmarts(bound['docking_reference']['mcs_smarts']), useChirality=True)


def test_ring_transformation_is_outside_the_local_protocol():
    original = Chem.MolFromSmiles('O=C1CCCCC1')
    target = Chem.MolFromSmiles('OC1=CCCCC1')
    assert parent_tautomer.map_keto_enol(original, target, {3}, timeout=5) is None
