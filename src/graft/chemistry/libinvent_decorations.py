"""LibInvent-only hydrogen decorations, using the existing bare-star token."""
from rdkit import Chem, rdBase


def assemble_pair(scaffold, decorators):
    """Join sites in SMILES order; a standalone * means ordinary hydrogen."""
    try:
        core = Chem.MolFromSmiles(scaffold)
        if core is None:
            return None
        sites = [a for a in core.GetAtoms() if a.GetAtomicNum() == 0]
        parts = decorators.split('|')
        if len(parts) != len(sites) or not sites:
            return None
        for atom in core.GetAtoms():
            atom.SetAtomMapNum(0)
        for label, atom in enumerate(sites, 1):
            if atom.GetDegree() != 1 or atom.GetBonds()[0].GetBondType() != Chem.BondType.SINGLE:
                return None
            atom.SetAtomMapNum(label)
        for label, part in enumerate(parts, 1):
            if not part:
                return None
            with rdBase.BlockLogs():
                fragment = Chem.MolFromSmiles('*[H]' if part == '*' else part)
            if fragment is None or len(Chem.GetMolFrags(fragment)) != 1:
                return None
            dummies = [a for a in fragment.GetAtoms() if a.GetAtomicNum() == 0]
            if len(dummies) != 1 or dummies[0].GetDegree() != 1:
                return None
            if dummies[0].GetBonds()[0].GetBondType() != Chem.BondType.SINGLE:
                return None
            for atom in fragment.GetAtoms():
                atom.SetAtomMapNum(0)
            dummies[0].SetAtomMapNum(label)
            core = Chem.CombineMols(core, fragment)
        with rdBase.BlockLogs():
            product = Chem.molzip(core)
            Chem.SanitizeMol(product)
            product = Chem.RemoveHs(product)
        if any(a.GetAtomicNum() == 0 for a in product.GetAtoms()):
            return None
        return Chem.MolToSmiles(product, isomericSmiles=True)
    except (ValueError, RuntimeError):
        return None


def decompose(smiles, scaffold):
    """Preserve heavy-only matching; accept H fallbacks only after exact assembly."""
    from graft import _core
    pair = _core.decompose_elite_for_libinvent(smiles, scaffold)
    if pair is not None:
        return pair
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    expanded = Chem.AddHs(mol)
    query, sites, labels, dummies, plain = _core._scaffold_attachment_layout(scaffold)
    for match in expanded.GetSubstructMatches(query, useChirality=False, uniquify=False):
        core = {match[i] for i in range(len(match)) if i not in dummies}
        roots = [match[sites[label]['dummy_qidx']] for label in labels]
        if not any(expanded.GetAtomWithIdx(i).GetAtomicNum() == 1 for i in roots):
            continue
        if any(expanded.GetAtomWithIdx(i).GetAtomicNum() == 1 for i in core):
            continue
        # Retain all existing undeclared-branch, overlap and core-contact checks.
        parts = _core._decompose_elite_match(expanded, match, sites, labels, dummies)
        if parts is None:
            continue
        normalized = []
        for part, root in zip(parts, roots):
            atom = expanded.GetAtomWithIdx(root)
            if atom.GetAtomicNum() == 1 and atom.GetIsotope() == 0:
                normalized.append('*')
            else:
                with rdBase.BlockLogs():
                    fragment = Chem.RemoveHs(Chem.MolFromSmiles(part))
                normalized.append(_core._normalize_libinvent_decorator_dummy(
                    Chem.MolToSmiles(fragment, isomericSmiles=True)))
        decorators = '|'.join(normalized)
        if assemble_pair(plain, decorators) == Chem.MolToSmiles(mol, isomericSmiles=True):
            return plain, decorators
    return None
