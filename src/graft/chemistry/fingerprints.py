"""Fingerprint configuration for existing reference matching and similarity."""
from collections.abc import Callable

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator


def create_morgan_generator() -> rdFingerprintGenerator.FingerprintGenerator64:
    """Create a fresh radius-2/2048-bit generator with existing RDKit defaults."""
    return rdFingerprintGenerator.GetMorganGenerator(
        radius=2,
        fpSize=2048,
    )


def tanimoto_with_fingerprint(
    smiles_a: str, smiles_b: str,
    fingerprint: Callable[[Chem.Mol], DataStructs.ExplicitBitVect],
) -> float:
    """Parse both inputs before fingerprinting; propagate existing RDKit errors."""
    a = Chem.MolFromSmiles(smiles_a)
    b = Chem.MolFromSmiles(smiles_b)
    if a is None or b is None:
        raise ValueError("Invalid SMILES")
    fa = fingerprint(a)
    fb = fingerprint(b)
    return float(DataStructs.TanimotoSimilarity(fa, fb))

