"""Frozen elite exports retain identity, rank and saved coordinates."""
from rdkit import Chem
import pytest

from graft.stages.crem_elites import export_elites


def plan():
    parents = []
    for cid, smiles, score in ((19, 'C[C@H](O)F', -71.5), (3, 'CCN', -70.0)):
        mol = Chem.MolFromSmiles(smiles)
        conf = Chem.Conformer(mol.GetNumAtoms())
        conf.Set3D(True)
        for index in range(mol.GetNumAtoms()):
            conf.SetAtomPosition(index, (index * 1.2, cid * 0.1, index * 0.3))
        mol.AddConformer(conf)
        parents.append(dict(compound_id=cid, smiles=smiles, score=score,
                            molblock=Chem.MolToMolBlock(mol)))
    return dict(score_source='md_gbsa', parents=parents)


def test_exports_saved_order_scores_stereo_and_coordinates(tmp_path):
    saved = plan()
    assert export_elites(tmp_path, 4, saved) == 2
    folder = tmp_path / 'iter03/19-elite'
    lines = (folder / 'elites.smi').read_text().splitlines()
    assert lines == ['C[C@H](O)F\tCMPID_19\t1\tmd_gbsa\t-71.5',
                     'CCN\tCMPID_3\t2\tmd_gbsa\t-70.0']
    mols = list(Chem.SDMolSupplier(str(folder / 'elites.sdf'), removeHs=False))
    for rank, (mol, parent) in enumerate(zip(mols, saved['parents'], strict=True), 1):
        original = Chem.MolFromMolBlock(parent['molblock'], removeHs=False)
        assert mol.GetProp('_Name') == f"CMPID_{parent['compound_id']}"
        assert mol.GetIntProp('elite_rank') == rank
        assert mol.GetIntProp('selected_cycle') == 3
        assert mol.GetDoubleProp('selection_score') == parent['score']
        assert Chem.MolToSmiles(mol) == Chem.MolToSmiles(original)
        assert mol.GetConformer().GetPositions() == pytest.approx(original.GetConformer().GetPositions())
    original_mtime = (folder / 'elites.smi').stat().st_mtime_ns
    (folder / 'elites.sdf').unlink()
    export_elites(tmp_path, 4, saved)
    assert (folder / 'elites.sdf').is_file()
    assert (folder / 'elites.smi').stat().st_mtime_ns == original_mtime


def test_initial_frame_is_not_exported_as_an_elite(tmp_path):
    assert export_elites(tmp_path, 1, dict(parents=[{}])) == 0
    assert not list(tmp_path.iterdir())


def test_relocates_only_identical_old_exports(tmp_path):
    saved = plan()
    export_elites(tmp_path, 4, saved)
    folder = tmp_path / 'iter03/19-elite'
    old = tmp_path / 'iter03/02-generate/crem'
    old.mkdir(parents=True)
    (old / 'elites.smi').write_text((folder / 'elites.smi').read_text())
    (old / 'elites.sdf').write_text('different contents retained')
    export_elites(tmp_path, 4, saved)
    assert not (old / 'elites.smi').exists()
    assert (old / 'elites.sdf').read_text() == 'different contents retained'
    assert (folder / 'elites.smi').is_file() and (folder / 'elites.sdf').is_file()


def test_invalid_saved_pose_does_not_publish_partial_exports(tmp_path):
    saved = plan()
    saved['parents'][1]['molblock'] = 'invalid'
    with pytest.raises(ValueError, match='CMPID_3'):
        export_elites(tmp_path, 4, saved)
    assert not list(tmp_path.rglob('elites.*'))
