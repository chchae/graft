"""Prove local keto/enol equivalence before deriving an exact shared core.

Generic connectivity is used only to propose full atom bijections, never as a
docking SMARTS. No tautomer canonicalization or production molecule edit occurs.
"""
import time

from rdkit import Chem


MATCH_LIMIT = 4097
POLICY = 'local_keto_enol_shared_core_v1'
AMINE_POLICY = 'local_keto_enol_alpha_amine_shared_core_v1'


def _state(mol, oxygen, carbon, alpha):
    o, c, a = (mol.GetAtomWithIdx(i) for i in (oxygen, carbon, alpha))
    if (o.GetAtomicNum(), c.GetAtomicNum(), a.GetAtomicNum()) != (8, 6, 6):
        return None
    if o.GetDegree() != 1 or any(x.GetIsAromatic() or x.IsInRing() for x in (o, c, a)):
        return None
    if c.GetFormalCharge() or a.GetFormalCharge():
        return None
    bonds = (mol.GetBondBetweenAtoms(oxygen, carbon).GetBondTypeAsDouble(),
             mol.GetBondBetweenAtoms(carbon, alpha).GetBondTypeAsDouble())
    if bonds == (2, 1) and o.GetFormalCharge() == 0 and o.GetTotalNumHs() == 0:
        return 'keto'
    if bonds == (1, 2) and (o.GetFormalCharge(), o.GetTotalNumHs()) in ((0, 1), (-1, 0)):
        return 'enol'
    return None


def _sites(original, target, mapping):
    changed = {b.GetIdx() for b in original.GetBonds() if b.GetBondType() !=
               target.GetBondBetweenAtoms(mapping[b.GetBeginAtomIdx()],
                                         mapping[b.GetEndAtomIdx()]).GetBondType()}
    if not changed:
        return None
    sites, covered, atoms = [], set(), set()
    for oxygen in original.GetAtoms():
        if oxygen.GetAtomicNum() != 8 or oxygen.GetDegree() != 1:
            continue
        oi = oxygen.GetIdx()
        carbon = oxygen.GetNeighbors()[0]
        ci = carbon.GetIdx()
        for alpha in carbon.GetNeighbors():
            ai = alpha.GetIdx()
            if ai == oi:
                continue
            edges = {original.GetBondBetweenAtoms(oi, ci).GetIdx(),
                     original.GetBondBetweenAtoms(ci, ai).GetIdx()}
            if not edges <= changed:
                continue
            left = _state(original, oi, ci, ai)
            right = _state(target, mapping[oi], mapping[ci], mapping[ai])
            if {left, right} != {'keto', 'enol'}:
                continue
            keto, enol = (original, target) if left == 'keto' else (target, original)
            ki, ei = (ai, mapping[ai]) if left == 'keto' else (mapping[ai], ai)
            if keto.GetAtomWithIdx(ki).GetTotalNumHs() != enol.GetAtomWithIdx(ei).GetTotalNumHs() + 1:
                continue
            if atoms & {oi, ci, ai}:
                return None  # Overlapping/conjugated transformations need a separate protocol.
            atoms.update((oi, ci, ai))
            covered.update(edges)
            sites.append((oi, ci, ai))
    return sites if covered == changed else None


def _signature(atom):
    return (atom.GetAtomicNum(), atom.GetIsotope(), atom.GetFormalCharge(),
            atom.GetTotalNumHs(), atom.GetIsAromatic(), atom.GetNumRadicalElectrons())


def _protonated_amines(original, target, mapping, sites):
    """Recognize only the adjacent secondary-amine/enolate zwitterion pair."""
    allowed = []
    for oxygen, carbon, alpha in sites:
        keto, enol = (original, target) if _state(original, oxygen, carbon, alpha) == 'keto' else (target, original)
        ko, eo = (oxygen, mapping[oxygen]) if keto is original else (mapping[oxygen], oxygen)
        if enol.GetAtomWithIdx(eo).GetFormalCharge() != -1 or keto.GetAtomWithIdx(ko).GetFormalCharge() != 0:
            continue
        candidates = []
        for atom in original.GetAtomWithIdx(alpha).GetNeighbors():
            i = atom.GetIdx()
            if atom.GetAtomicNum() != 7:
                continue
            k, e = (atom, target.GetAtomWithIdx(mapping[i])) if keto is original else (target.GetAtomWithIdx(mapping[i]), atom)
            if ((k.GetFormalCharge(), k.GetTotalNumHs()) != (0, 1)
                    or (e.GetFormalCharge(), e.GetTotalNumHs()) != (1, 2)):
                continue
            # Restrict this exception to an acyclic aliphatic secondary amine;
            # amides, aromatic/conjugated nitrogens and other ionizations fail.
            if any(n.GetDegree() != 2 or n.IsInRing() or n.GetIsAromatic()
                   or n.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED for n in (k, e)):
                continue
            if any(b.GetBondType() != Chem.BondType.SINGLE for n in (k, e) for b in n.GetBonds()):
                continue
            neighbors = [a for n in (k, e) for a in n.GetNeighbors()]
            if any(a.GetAtomicNum() != 6 or a.GetIsAromatic() for a in neighbors):
                continue
            if any(b.GetBondType() != Chem.BondType.SINGLE and b.GetOtherAtom(a).GetAtomicNum() in (7, 8, 16)
                   for a in neighbors for b in a.GetBonds()):
                continue
            # The other N substituent must be saturated. Only conjugation at
            # the already proven alpha carbon belongs to this local rule.
            other = [a for a in atom.GetNeighbors() if a.GetIdx() != alpha]
            if any(b.GetBondType() != Chem.BondType.SINGLE for a in other for b in a.GetBonds()):
                continue
            candidates.append(i)
        if len(candidates) == 1:
            allowed.extend(candidates)
    return tuple(sorted(set(allowed)))


def _verify(original, target, mapping, sites, protonated_amines=()):
    reactive = {i for site in sites for i in site} | set(protonated_amines)
    # Only the proven oxygen/alpha changes and paired alpha-amine protonation
    # are authorized. All other atom signatures must remain identical.
    for i, j in enumerate(mapping):
        a, b = original.GetAtomWithIdx(i), target.GetAtomWithIdx(j)
        if i not in reactive and _signature(a) != _signature(b):
            return False
        if a.GetIsotope() != b.GetIsotope() or a.GetNumRadicalElectrons() != b.GetNumRadicalElectrons():
            return False
    for _, carbon, _ in sites:
        if _signature(original.GetAtomWithIdx(carbon)) != _signature(target.GetAtomWithIdx(mapping[carbon])):
            return False
    # Normalize copies at the proven reaction sites only. Keep all other stereo,
    # including E/Z, and require the exact proposed bijection in both directions.
    left, right = Chem.RWMol(original), Chem.RWMol(target)
    for site in list(sites) + [(i,) for i in protonated_amines]:
        for i in site:
            a, b = left.GetAtomWithIdx(i), right.GetAtomWithIdx(mapping[i])
            a.SetFormalCharge(b.GetFormalCharge())
            a.SetNumExplicitHs(b.GetTotalNumHs())
            a.SetNoImplicit(True)
            a.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
            b.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
            # SDF E/Z can also live on adjacent directed single bonds. Clear
            # only directions at the reactive site; unrelated bond stereo stays.
            for atom in (a, b):
                for bond in atom.GetBonds():
                    bond.SetBondDir(Chem.BondDir.NONE)
        for i, j in zip(site, site[1:]):
            bond = left.GetBondBetweenAtoms(i, j)
            bond.SetBondType(right.GetBondBetweenAtoms(mapping[i], mapping[j]).GetBondType())
            bond.SetStereo(Chem.BondStereo.STEREONONE)
            right.GetBondBetweenAtoms(mapping[i], mapping[j]).SetStereo(Chem.BondStereo.STEREONONE)
    left, right = left.GetMol(), right.GetMol()
    Chem.SanitizeMol(left)
    Chem.SanitizeMol(right)
    inverse = tuple(mapping.index(i) for i in range(len(mapping)))
    return (mapping in right.GetSubstructMatches(left,
                uniquify=False, useChirality=True, maxMatches=MATCH_LIMIT)
            and inverse in left.GetSubstructMatches(right,
                uniquify=False, useChirality=True, maxMatches=MATCH_LIMIT))


def map_keto_enol(original, target, core_indices, *, timeout):
    """Return a full proven map and unchanged connected core, or no local rule.

    Atom-map numbers do not identify ancestry. Isotope/element/connectivity,
    local H/charge/bond accounting and unaffected stereo must all agree.
    """
    if original.GetNumAtoms() != target.GetNumAtoms() or original.GetNumBonds() != target.GetNumBonds():
        return None
    started = time.monotonic()
    query = Chem.RWMol(original)
    for atom in original.GetAtoms():
        isotope = str(atom.GetIsotope()) if atom.GetIsotope() else ''
        query.ReplaceAtom(atom.GetIdx(), Chem.AtomFromSmarts(f'[{isotope}#{atom.GetAtomicNum()}]'))
    for bond in original.GetBonds():
        query.ReplaceBond(bond.GetIdx(), Chem.BondFromSmarts('~'))
    mappings = target.GetSubstructMatches(query.GetMol(), uniquify=False, maxMatches=MATCH_LIMIT)
    if len(mappings) >= MATCH_LIMIT:
        raise ValueError('Elite keto/enol full mapping exceeds the match limit')
    candidates, recognized = [], False
    for mapping in mappings:
        if time.monotonic() - started > timeout:
            raise ValueError('Elite keto/enol mapping timed out')
        sites = _sites(original, target, mapping)
        if not sites:
            continue
        recognized = True
        amines = _protonated_amines(original, target, mapping, sites)
        if not _verify(original, target, mapping, sites, amines):
            continue
        reactive = {i for site in sites for i in site} | set(amines)
        if reactive & set(core_indices):
            raise ValueError('Elite keto/enol conversion overlaps the declared reference core')
        cut = Chem.RWMol(original)
        for i in sorted(reactive, reverse=True):
            cut.RemoveAtom(i)
        retained = [i for i in range(original.GetNumAtoms()) if i not in reactive]
        fragments = [tuple(retained[i] for i in fragment)
                     for fragment in Chem.GetMolFrags(cut.GetMol())]
        fragments = [f for f in fragments if set(core_indices) <= set(f)]
        if not fragments:
            raise ValueError('Elite keto/enol unchanged core disconnects the declared reference core')
        keep = min(fragments, key=lambda f: (-len(f), f))
        distance = sum((original.GetConformer().GetAtomPosition(i) -
                        target.GetConformer().GetAtomPosition(j)).LengthSq()
                       for i, j in enumerate(mapping))
        candidates.append((distance, mapping, keep, sites, amines))
    if not candidates:
        if recognized:
            raise ValueError('Elite keto/enol mapping changes unrelated chemistry or stereochemistry')
        return None
    _, mapping, keep, sites, amines = min(candidates)
    return dict(mapping=mapping, core=keep, sites=sites, protonated_amines=amines,
                policy=AMINE_POLICY if amines else POLICY)
