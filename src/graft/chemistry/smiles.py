"""Canonical isomeric SMILES using the existing project parsing rules."""
from rdkit import Chem


def canonicalize_smiles(smiles: str) -> str | None:
    """Return canonical isomeric SMILES or None; propagate RDKit call exceptions."""
    if not isinstance(smiles, str) or not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    return Chem.MolToSmiles(
        mol,
        canonical=True,
        isomericSmiles=True,
    )

