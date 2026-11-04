"""Best-elite cores with two internal sidechain cuts and immutable recovery."""
import pytest
from rdkit import Chem

from graft import _core, database
from graft.chemistry import elite_scaffold as adaptive
from graft.chemistry.scaffold import prepare_iteration_scaffold
from test_iteration_scaffold import project


PREVIOUS = dict(mcs_smarts='c1ccccc1', scaffold_smiles='[*:0]c1ccc([*:1])cc1',
                source='previous_iteration_elites', source_ids=[1, 2])


def refs(smiles=('CCCc1ccc(CC)cc1', 'CCCCc1ccc(CCC)cc1'), n=10):
    return [dict(id=i + 100, name=f'E{i}', mol=Chem.MolFromSmiles(smiles[i % len(smiles)]))
            for i in range(n)]


def assert_internal_cuts(saved, selected):
    assert saved['source'] == 'single_elite_sidechain_mcs'
    assert saved['source_ids'] == [selected[0]['id']]
    assert len(saved['replacement_cuts']) == 2
    base = set(saved['previous_core_atom_indices'])
    mol = Chem.MolFromSmiles(saved['replacement_elite_smiles'])
    for cut in saved['replacement_cuts']:
        assert cut['retained_atom_index'] not in base
        assert cut['distal_atom_index'] not in base
        bond = mol.GetBondWithIdx(cut['bond_index'])
        assert not bond.IsInRing() and bond.GetBondType() == Chem.BondType.SINGLE
    scaffold = Chem.MolFromSmiles(saved['scaffold_smiles'])
    assert sorted(a.GetAtomMapNum() for a in scaffold.GetAtoms() if a.GetAtomicNum() == 0) == [0, 1]
    core = Chem.MolFromSmarts(saved['mcs_smarts'])
    assert core.GetNumAtoms() >= 8  # Both old attachment roots must enter the new retained core.
    assert all(a.GetAtomicNum() for a in core.GetAtoms())
    assert scaffold.HasSubstructMatch(Chem.MolFromSmarts(PREVIOUS['mcs_smarts']))
    assert adaptive._round_trips(selected[:1], saved['scaffold_smiles'])


def test_same_mcs_halves_then_builds_from_one_without_single_molecule_findmcs(monkeypatch):
    calls = []
    def same(selected, *a, **kw):
        calls.append(len(selected))
        return dict(mcs_smarts='[#6]1:[#6]:[#6]:[#6]:[#6]:[#6]:1',
                    scaffold_smiles='[*:1]c1ccc([*:0])cc1')
    monkeypatch.setattr(_core, 'prepare_libinvent_scaffold', same)
    selected = refs()
    saved = adaptive.prepare_ranked_scaffold(selected, PREVIOUS, 'c1ccccc1')
    assert calls == [10, 5, 2]
    assert [a['elite_count'] for a in saved['elite_mcs_attempts']] == [10, 5, 2, 1]
    assert [a['status'] for a in saved['elite_mcs_attempts']] == ['same_as_previous_mcs'] * 3 + ['single_elite_sidechain_mcs']
    assert_internal_cuts(saved, selected)


@pytest.mark.parametrize('old', ['[*:0]c1ccc([*:1])cc1', '[*:1]c1ccc([*:0])cc1'])
def test_same_positions_are_not_reused_and_labels_are_preserved(old):
    selected = refs(('CCc1ccc(CC)cc1',))
    saved = adaptive.best_elite_scaffold(selected, dict(PREVIOUS, scaffold_smiles=old), 'c1ccccc1')
    assert saved['scaffold_smiles'] != old
    assert_internal_cuts(saved, selected)


@pytest.mark.parametrize('smiles', ['COc1ccc(Cl)cc1', 'Cc1ccc(CC)cc1',
                                    'c1ccc(-c2ccc(-c3ccccc3)cc2)cc1'])
def test_missing_internal_cut_reuses_previous_without_cutting_ring_or_old_boundary(smiles):
    selected = refs((smiles,))
    saved = adaptive.best_elite_scaffold(selected, PREVIOUS, 'c1ccccc1')
    assert saved['source'] == 'previous_mcs_reused_attachments'
    assert saved['fallback_reason'] == 'missing_internal_sidechain_cut'
    assert saved['fallback_sidechain_label'] in [0, 1]
    assert saved['scaffold_smiles'] == PREVIOUS['scaffold_smiles']
    assert saved['mcs_smarts'] == PREVIOUS['mcs_smarts']
    assert saved['compatible_elite_ids'] == [ref['id'] for ref in selected]
    assert adaptive._round_trips(selected, saved['scaffold_smiles'])


@pytest.mark.parametrize('smiles', [('COc1ccc(Cl)cc1',),
                                    ('COc1ccc(Cl)cc1', 'CCCc1ccccc1')])
def test_missing_internal_cut_reuse_requires_two_compatible_training_elites(smiles):
    with pytest.raises(RuntimeError, match='Previous scaffold reuse requires.*at least two'):
        adaptive.best_elite_scaffold(refs(smiles, n=len(smiles)), PREVIOUS, 'c1ccccc1')


def test_reported_iteration4_methyl_sidechain_reuses_previous_scaffold():
    selected = refs((
        'Cc1ccc(-c2cc3c(NC(CN4CCN(C)CC4)c4ccccc4)ncnc3[nH]2)cc1',
        'Cc1ccc(-c2cc3c(NC(CN4CCN(C)CC4)c4cccs4)ncnc3[nH]2)cc1',
    ), n=2)
    scaffold = 'c1nc(N[*:0])c2cc(-c3ccc([*:1])cc3)[nH]c2n1'
    previous = dict(mcs_smarts=scaffold, scaffold_smiles=scaffold)
    saved = adaptive.prepare_ranked_scaffold(selected, previous, scaffold)
    assert saved['source'] == 'previous_mcs_reused_attachments'
    assert saved['fallback_reason'] == 'missing_internal_sidechain_cut'
    assert saved['fallback_sidechain_label'] == 1
    assert saved['scaffold_smiles'] == saved['mcs_smarts'] == scaffold
    assert saved['compatible_elite_ids'] == [100, 101]
    assert saved['elite_mcs_attempts'][-1]['status'] == 'reused_attachments'



def test_singleton_is_not_duplicated_to_satisfy_training_minimum():
    with pytest.raises(RuntimeError, match='at least two compatible training elites'):
        adaptive.best_elite_scaffold(refs(n=1), PREVIOUS, 'c1ccccc1')


def test_insufficient_new_core_training_reuses_exact_previous_scaffold(monkeypatch, capsys):
    selected = refs(('CCc1ccc(CC)cc1',) + ('COc1ccc(OC)cc1',) * 9)
    monkeypatch.setattr(_core, 'prepare_libinvent_scaffold', lambda *a, **k: dict(PREVIOUS))
    saved = adaptive.prepare_ranked_scaffold(selected, PREVIOUS, 'c1ccccc1')
    assert saved['mcs_smarts'] == PREVIOUS['mcs_smarts']
    assert saved['scaffold_smiles'] == PREVIOUS['scaffold_smiles']
    assert saved['source'] == 'previous_mcs_reused_attachments'
    assert saved['fallback_reason'] == 'insufficient_sidechain_training_elites'
    assert saved['source_ids'] == saved['compatible_elite_ids'] == list(range(100, 110))
    assert [a['elite_count'] for a in saved['elite_mcs_attempts']] == [10, 5, 2, 1]
    assert saved['elite_mcs_attempts'][-1]['status'] == 'reused_attachments'
    assert saved['elite_mcs_policy'] == 'ranked_halving_single_elite_sidechains_v3'
    assert adaptive._round_trips(selected, saved['scaffold_smiles'])
    assert 'reusing the previous MCS and attachment positions' in capsys.readouterr().out


def test_previous_reuse_still_requires_two_exactly_reconstructable_elites():
    selected = refs(('CCc1ccc(CC)cc1', 'CCCc1ccccc1'), n=2)
    with pytest.raises(RuntimeError, match='Previous scaffold reuse requires.*at least two'):
        adaptive.best_elite_scaffold(selected, PREVIOUS, 'c1ccccc1')


def test_iteration3_egfr_lactam_core_reuses_previous_training_scaffold():
    selected = refs((
        'CN1CC(Nc2ncnc3[nH]c(-c4ccc(N5CCC(CN6CCOCC6)CC5)cc4)cc23)CC1=O',
        'CN(C)c1ccc(-c2cc3c(NC(CN4CCOCC4)c4ccccc4)ncnc3[nH]2)cc1',
    ), n=2)
    old_smiles = 'c1nc(N[*:0])c2cc(-c3ccc([*:1])cc3)[nH]c2n1'
    old = dict(mcs_smarts=old_smiles, scaffold_smiles=old_smiles)
    saved = adaptive.prepare_ranked_scaffold(selected, old, old_smiles)
    assert saved['source'] == 'previous_mcs_reused_attachments'
    assert saved['scaffold_smiles'] == saved['mcs_smarts'] == old_smiles
    assert saved['compatible_elite_ids'] == [100, 101]
    assert saved['elite_mcs_attempts'][-1]['status'] == 'reused_attachments'


def test_complete_supplied_scaffold_preserves_docking_constraint():
    old = dict(PREVIOUS, mcs_smarts=PREVIOUS['scaffold_smiles'])
    selected = refs()
    saved = adaptive.best_elite_scaffold(selected, old, old['mcs_smarts'])
    assert_internal_cuts(saved, selected)
    assert Chem.MolFromSmiles(saved['scaffold_smiles']).HasSubstructMatch(Chem.MolFromSmarts(old['mcs_smarts']))


def test_real_identical_elites_do_not_adopt_whole_molecule_as_core():
    selected = refs(('CCc1ccc(CC)cc1',))
    saved = adaptive.prepare_ranked_scaffold(selected, PREVIOUS, 'c1ccccc1')
    assert [a['elite_count'] for a in saved['elite_mcs_attempts']] == [10, 5, 2, 1]
    assert_internal_cuts(saved, selected)
    assert Chem.MolFromSmarts(saved['mcs_smarts']).GetNumAtoms() < selected[0]['mol'].GetNumHeavyAtoms()


def test_selection_is_deterministic_even_when_input_atoms_are_reordered():
    selected = refs()
    first = adaptive.best_elite_scaffold(selected, PREVIOUS, 'c1ccccc1')
    selected[0]['mol'] = Chem.RenumberAtoms(selected[0]['mol'], list(reversed(range(selected[0]['mol'].GetNumAtoms()))))
    again = adaptive.best_elite_scaffold(selected, PREVIOUS, 'c1ccccc1')
    assert first == again


@pytest.mark.parametrize('smiles', [
    'C[C@H](F)c1ccc(CCN(C)C)cc1',
    'C[N+](C)(CCc1ccc(CCO)cc1)C',
])
def test_internal_cuts_preserve_stereo_charge_and_exact_elite_reassembly(smiles):
    selected = refs((smiles,), n=2)
    saved = adaptive.best_elite_scaffold(selected, PREVIOUS, 'c1ccccc1')
    assert_internal_cuts(saved, selected)
    assert adaptive._round_trips(selected, saved['scaffold_smiles'])


def test_real_egfr_best_elite_uses_two_sidechain_cuts_with_original_elite_training_support():
    smiles = [
        'Cc1c(Cl)cccc1CNc1ncnc2[nH]c(-c3ccc(N4CCN(C)CC4)cc3)cc12',
        'CN1CCN(c2ccc(-c3cc4c(Nc5cccc6c5C(=O)N(C)C6)ncnc4[nH]3)cc2)CC1',
        'CN1CCN(c2ccc(-c3cc4c(NCc5cccc(Cl)c5)ncnc4[nH]3)cc2)CC1',
    ]
    old_smiles = 'c1nc(N[*:0])c2cc(-c3ccc([*:1])cc3)[nH]c2n1'
    old = dict(mcs_smarts=old_smiles, scaffold_smiles=old_smiles)
    selected = refs(smiles, n=3)
    saved = adaptive.best_elite_scaffold(selected, old, old_smiles)
    assert saved['compatible_elite_ids'] == [100, 102]
    assert saved['source_ids'] == [100]
    assert len(saved['replacement_cuts']) == 2
    base = set(saved['previous_core_atom_indices'])
    assert all(cut['retained_atom_index'] not in base for cut in saved['replacement_cuts'])
    assert Chem.MolFromSmarts(saved['mcs_smarts']).GetNumAtoms() == 23


def test_different_valid_mcs_is_accepted_at_first_smaller_subset(monkeypatch):
    def infer(selected, *a, **kw):
        return dict(PREVIOUS) if len(selected) == 10 else dict(
            mcs_smarts='Cc1ccccc1', scaffold_smiles='[*:0]Cc1ccc([*:1])cc1')
    monkeypatch.setattr(_core, 'prepare_libinvent_scaffold', infer)
    saved = adaptive.prepare_ranked_scaffold(refs(), PREVIOUS, 'c1ccccc1')
    assert [a['elite_count'] for a in saved['elite_mcs_attempts']] == [10, 5]
    assert saved['source_ids'] == list(range(100, 105))


@pytest.mark.parametrize('reuse', [False, True, 'missing_cut'])
def test_saved_new_core_and_cuts_resume_without_recomputing(project, monkeypatch, reuse):
    database.iteration_scaffold(project.db_path, 0,
                                dict(mcs_smarts='c1ccccc1', source='reference', initial_elite_mcs=None))
    database.iteration_scaffold(project.db_path, 1, PREVIOUS)
    project.prime_gbsa_elite_count = 10
    smiles = (('COc1ccc(Cl)cc1',) if reuse == 'missing_cut' else
              ('CCc1ccc(CC)cc1',) + ('COc1ccc(OC)cc1',) * 9
              if reuse else ('CCc1ccc(CC)cc1',))
    selected = [(r['id'], r['name'], Chem.MolToSmiles(r['mol']), 1, -10, -100 + i)
                for i, r in enumerate(refs(smiles))]
    prepare_iteration_scaffold(project, 2, list(reversed(selected)), create=True)
    saved = database.iteration_scaffold(project.db_path, 2)
    assert saved['ranked_elite_ids'] == list(range(100, 110))
    assert saved['attachment_from_iteration'] == 1
    assert saved['source'] == ('previous_mcs_reused_attachments' if reuse else 'single_elite_sidechain_mcs')
    assert saved['source_ids'] == (list(range(100, 110)) if reuse else [100])
    assert saved['elite_mcs_attempts'][-1]['status'] == ('reused_attachments' if reuse else 'single_elite_sidechain_mcs')
    assert database.iteration_scaffold(project.db_path, 1) == PREVIOUS
    project.libinvent_scaffold_file.unlink()
    monkeypatch.setattr(adaptive, 'prepare_ranked_scaffold', lambda *a, **k: pytest.fail('recomputed'))
    prepare_iteration_scaffold(project, 2, create=True)
    assert database.iteration_scaffold(project.db_path, 2) == saved
    assert project.libinvent_scaffold_file.read_text().strip() == saved['scaffold_smiles']
