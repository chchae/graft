from rdkit import Chem, DataStructs
from graft import _core


def tanimoto(smiles_a: str, smiles_b: str) -> float:
    from graft.chemistry.fingerprints import tanimoto_with_fingerprint
    return tanimoto_with_fingerprint(
        smiles_a, smiles_b,
        lambda mol: _core.FP_GENERATOR.GetFingerprint(mol),
    )
