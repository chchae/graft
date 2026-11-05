"""Hydrogen pairs must train with the original vocabulary and reconstruct exactly."""
from types import SimpleNamespace

import pytest
from rdkit import Chem

from graft import _core
from graft.chemistry.libinvent_decorations import assemble_pair, decompose
from graft.reinvent_launcher import support_hydrogen_decorations


@pytest.mark.parametrize('smiles,scaffold,hydrogens', [
    ('Cc1ccccc1', '[*:0]c1ccc([*:1])cc1', 1),
    ('c1ccccc1', '[*:0]c1ccc([*:1])cc1', 2),
    ('CC', '[*:0]C([*:1])C', 2),
    ('C[C@H](O)CC', '[*:0]C[C@H](O)CC[*:1]', 2),
    ('[13CH3]c1ccccc1', '[*:0]c1ccc([*:1])cc1', 1),
    ('[2H]Cc1ccccc1', '[*:0]c1ccc([*:1])cc1', 1),
])
def test_hydrogen_roundtrip_preserves_chemistry_and_shared_rules(smiles, scaffold, hydrogens):
    assert _core.decompose_elite_for_libinvent(smiles, scaffold) is None
    pair = decompose(smiles, scaffold)
    assert pair is not None
    assert pair[1].split('|').count('*') == hydrogens
    assert '[H]' not in pair[1]
    assert assemble_pair(*pair) == Chem.MolToSmiles(Chem.MolFromSmiles(smiles), isomericSmiles=True)


@pytest.mark.parametrize('smiles,scaffold', [
    ('Cc1cc(N)ccc1', '[*:0]c1ccc([*:1])cc1'),  # undeclared branch
    ('C1CCCCC1', '[*:0]CC[*:1]'),  # both roots enter the same component
    ('CC(C)(C)C', '[*:0]C(C)(C)(C)C'),  # no available H / invalid valence
    ('C[C@@H](O)CC', '[*:0]C[C@H](O)CC[*:1]'),  # opposite stereochemistry
])
def test_hydrogen_does_not_bypass_structure_checks(smiles, scaffold):
    assert decompose(smiles, scaffold) is None


@pytest.mark.parametrize('decorators', ['', '|*C', '*|', '*|*|*', '*C.*N|*', '*=O|*'])
def test_invalid_decorations_remain_invalid(decorators):
    assert assemble_pair('[*:0]c1ccc([*:1])cc1', decorators) is None


def test_training_writer_keeps_both_reported_elites_and_original_tokens(tmp_path, monkeypatch):
    from graft import libinvent_vocabulary
    from graft.libinvent_vocabulary import unsupported_pair_tokens
    scaffold = '[*:0]Cc1ccc(-c2cc3c(NC(c4ccccc4)[*:1])ncnc3[nH]2)cc1'
    molecules = [
        'Cc1ccc(-c2cc3c(NC(CN4CCCC4)c4ccccc4)ncnc3[nH]2)cc1',
        'CN1CCC(Cc2ccc(-c3cc4c(NCc5ccccc5)ncnc4[nH]3)cc2)CC1',
    ]
    # Use a minimal prior-like vocabulary without an explicit hydrogen token.
    tokens = list('CNcn1234()') + ['[*]', '*', '|', '[nH]', '-']
    vocabulary = dict(scaffold=tokens, decorators=tokens,
                      reader_tokens=[tokens, tokens], token_pattern=r'\[[^]]+\]|.')
    monkeypatch.setattr(libinvent_vocabulary, 'load_prior_vocabulary', lambda *a: vocabulary)
    elites = [(i, f'elite{i}', smi, 1, -9, -76 + i) for i, smi in enumerate(molecules, 1)]
    _, train, valid, usable = _core.write_elite_libinvent_training_files(
        elites, scaffold, tmp_path, prior_file='unchanged.prior')
    assert usable == 2
    for smi in molecules:
        pair = decompose(smi, scaffold)
        assert not unsupported_pair_tokens(*pair, vocabulary)
        assert assemble_pair(*pair) == Chem.MolToSmiles(Chem.MolFromSmiles(smi))
    assert '[H]' not in train.read_text() + valid.read_text()
    assert (tmp_path / 'elite_skipped.tsv').read_text().count('\n') == 1


def test_sampling_adapter_expands_only_standalone_star_and_preserves_labels():
    calls = []
    def native(scaffold, decorators, keep_labels_on_atoms=False):
        calls.append((scaffold, decorators, keep_labels_on_atoms))
        return Chem.MolFromSmiles('Cc1ccccc1')
    runtime = SimpleNamespace(join_scaffolds_and_decorations=native)
    support_hydrogen_decorations(runtime)
    result = runtime.join_scaffolds_and_decorations('[*:0]c1ccc([*:1])cc1', '*|*C', True)
    assert calls[-1][1:] == ('*[H]|*C', True)
    assert Chem.MolToSmiles(result) == 'Cc1ccccc1'
    runtime.join_scaffolds_and_decorations('scaffold', '*N|*C', False)
    assert calls[-1] == ('scaffold', '*N|*C', False)
    runtime.join_scaffolds_and_decorations('scaffold', '|*C')
    assert calls[-1][1] == '|*C'


def test_sampling_adapter_native_failure_is_not_accepted():
    runtime = SimpleNamespace(join_scaffolds_and_decorations=lambda *a: None)
    support_hydrogen_decorations(runtime)
    assert runtime.join_scaffolds_and_decorations('[*:0]C', '*') is None
