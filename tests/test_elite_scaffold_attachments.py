"""Chemical site changes and immutable restart behavior for adaptive LibInvent."""
import pytest
from rdkit import Chem

from graft import _core, database
from graft.chemistry import elite_scaffold as adaptive
from graft.chemistry.scaffold import prepare_iteration_scaffold
from test_iteration_scaffold import project


PREVIOUS = dict(mcs_smarts='c1ccccc1', scaffold_smiles='[*:0]c1ccc([*:1])cc1',
                source='previous_iteration_elites', source_ids=[1, 2])


def refs(smiles=('CCCc1ccccc1CC', 'CCc1ccccc1CCC'), n=10):
    return [dict(id=i + 100, name=f'E{i}', mol=Chem.MolFromSmiles(smiles[i % len(smiles)]))
            for i in range(n)]


def test_same_mcs_halves_10_5_2_then_keeps_core_and_moves_sites(monkeypatch):
    calls = []
    def same(selected, *a, **kw):
        calls.append(len(selected))
        return dict(mcs_smarts='[#6]1:[#6]:[#6]:[#6]:[#6]:[#6]:1',
                    scaffold_smiles='[*:1]c1ccc([*:0])cc1')
    monkeypatch.setattr(_core, 'prepare_libinvent_scaffold', same)
    saved = adaptive.prepare_ranked_scaffold(refs(), PREVIOUS, 'c1ccccc1')
    assert calls == [10, 5, 2]  # Never run single-molecule FindMCS.
    assert [a['elite_count'] for a in saved['elite_mcs_attempts']] == [10, 5, 2, 1]
    assert [a['status'] for a in saved['elite_mcs_attempts']] == ['same_as_previous_mcs'] * 3 + ['alternate_attachments']
    assert saved['mcs_smarts'] == PREVIOUS['mcs_smarts']
    assert saved['source'] == 'previous_mcs_alternate_attachments'
    assert adaptive._unlabelled(saved['scaffold_smiles']) != adaptive._unlabelled(PREVIOUS['scaffold_smiles'])
    assert adaptive._round_trips(refs(), saved['scaffold_smiles'])


@pytest.mark.parametrize('old', ['[*:0]c1ccc([*:1])cc1', '[*:1]c1ccc([*:0])cc1'])
def test_same_positions_and_symmetry_or_label_changes_reuse_exact_previous_sites(old):
    saved = adaptive.alternate_attachments(refs(('CCc1ccc(CC)cc1',)),
                                          dict(PREVIOUS, scaffold_smiles=old), 'c1ccccc1')
    assert saved['source'] == 'previous_mcs_reused_attachments'
    assert saved['scaffold_smiles'] == old
    assert saved['mcs_smarts'] == PREVIOUS['mcs_smarts']
    assert saved['fallback_reason'] == 'no_alternate_attachment_points'


def test_at_least_two_training_elites_and_top_elite_are_required():
    with pytest.raises(RuntimeError, match='at least two compatible'):
        adaptive.alternate_attachments(refs(n=1), PREVIOUS, 'c1ccccc1')
    saved = adaptive.alternate_attachments(refs(('CCc1ccc(CC)cc1', 'CCc1ccccc1CC')),
                                          PREVIOUS, 'c1ccccc1')
    assert saved['source'] == 'previous_mcs_reused_attachments'
    assert saved['compatible_elite_ids'] == [100, 102, 104, 106, 108]


def test_complete_supplied_scaffold_retains_its_core_but_replaces_dummy_sites():
    old = dict(PREVIOUS, mcs_smarts=PREVIOUS['scaffold_smiles'])
    saved = adaptive.alternate_attachments(refs(), old, 'c1ccccc1')
    query = Chem.MolFromSmarts(saved['mcs_smarts'])
    assert query.GetNumAtoms() == 6
    assert all(atom.GetAtomicNum() != 0 for atom in query.GetAtoms())
    assert adaptive._unlabelled(saved['scaffold_smiles']) != adaptive._unlabelled(old['scaffold_smiles'])
    assert adaptive._round_trips(refs(), saved['scaffold_smiles'])


def test_real_identical_elites_reach_singleton_without_accepting_entire_molecule():
    saved = adaptive.prepare_ranked_scaffold(refs(('CCc1ccccc1CC',)), PREVIOUS, 'c1ccccc1')
    assert [a['elite_count'] for a in saved['elite_mcs_attempts']] == [10, 5, 2, 1]
    assert saved['mcs_smarts'] == PREVIOUS['mcs_smarts']
    assert saved['source'] == 'previous_mcs_alternate_attachments'


def test_different_valid_mcs_is_accepted_at_first_smaller_subset(monkeypatch):
    def infer(selected, *a, **kw):
        return dict(PREVIOUS) if len(selected) == 10 else dict(
            mcs_smarts='Cc1ccccc1', scaffold_smiles='[*:0]Cc1ccccc1[*:1]')
    monkeypatch.setattr(_core, 'prepare_libinvent_scaffold', infer)
    saved = adaptive.prepare_ranked_scaffold(refs(), PREVIOUS, 'c1ccccc1')
    assert [a['elite_count'] for a in saved['elite_mcs_attempts']] == [10, 5]
    assert saved['source_ids'] == list(range(100, 105))


@pytest.mark.parametrize('reuse', [False, True])
def test_saved_positions_resume_without_recomputing(project, monkeypatch, reuse):
    database.iteration_scaffold(project.db_path, 0,
                                dict(mcs_smarts='c1ccccc1', source='reference', initial_elite_mcs=None))
    database.iteration_scaffold(project.db_path, 1, PREVIOUS)
    project.prime_gbsa_elite_count = 10
    smiles = 'CCc1ccc(CC)cc1' if reuse else 'CCc1ccccc1CC'
    selected = [(r['id'], r['name'], Chem.MolToSmiles(r['mol']), 1, -10, -100 + i)
                for i, r in enumerate(refs((smiles,)))]
    prepare_iteration_scaffold(project, 2, list(reversed(selected)), create=True)
    saved = database.iteration_scaffold(project.db_path, 2)
    assert saved['ranked_elite_ids'] == list(range(100, 110))
    assert saved['attachment_from_iteration'] == 1
    assert saved['source'] == ('previous_mcs_reused_attachments' if reuse else 'previous_mcs_alternate_attachments')
    if reuse:
        assert saved['source_ids'] == list(range(100, 110))
        assert saved['elite_mcs_attempts'][-1]['status'] == 'reused_attachments'
        assert saved['scaffold_smiles'] == PREVIOUS['scaffold_smiles']
    assert database.iteration_scaffold(project.db_path, 1) == PREVIOUS
    project.libinvent_scaffold_file.unlink()
    monkeypatch.setattr(adaptive, 'prepare_ranked_scaffold', lambda *a, **k: pytest.fail('recomputed'))
    prepare_iteration_scaffold(project, 2, create=True)
    assert database.iteration_scaffold(project.db_path, 2) == saved
    assert project.libinvent_scaffold_file.read_text().strip() == saved['scaffold_smiles']
