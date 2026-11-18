"""Map a selected elite's saved ancestry onto its actual best Glide pose."""
import copy
import math
import re

from rdkit import Chem
from rdkit.Chem import rdFMCS

from graft.chemistry.combichem3d import history as provenance
from graft.chemistry.combichem3d.parent_tautomer import map_keto_enol, _signature


def _growth_query(smarts):
    query = Chem.MolFromSmarts(smarts)
    for atom in query.GetAtoms():
        relaxed = re.sub(r'(@@?)H\d*', r'\1', atom.GetSmarts())
        if relaxed != atom.GetSmarts():
            # Keep ring-closure stereochemistry while relaxing H for growth.
            atom.SetQuery(Chem.AtomFromSmarts(relaxed))
            atom.SetNumExplicitHs(0)
    smarts = Chem.MolToSmarts(query)
    return smarts, Chem.MolFromSmarts(smarts)


def _partial_query(original, docked, core_indices, *, timeout):
    """Prefer an exact core-seeded MCS; retain the exact core if search fails."""
    seed, seed_query = _growth_query(Chem.MolFragmentToSmarts(original, atomsToUse=sorted(core_indices)))

    def compatible(query):
        if len(Chem.GetMolFrags(query)) != 1:
            return False
        left = original.GetSubstructMatches(query, uniquify=False, useChirality=True, maxMatches=4097)
        right = docked.GetSubstructMatches(query, uniquify=False, useChirality=True, maxMatches=4097)
        if max(len(left), len(right)) >= 4097 or len(left) * len(right) > 65536:
            raise ValueError('Elite docking partial mapping exceeds the match limit')
        return any(core_indices <= set(a) and all(
            _signature(original.GetAtomWithIdx(i)) == _signature(docked.GetAtomWithIdx(j))
            for i, j in zip(a, b)) for a in left for b in right)

    if not compatible(seed_query):
        raise ValueError('Elite docking partial mapping does not retain the unchanged declared reference core')
    params = rdFMCS.MCSParameters()
    params.Timeout = timeout
    params.InitialSeed = seed
    params.BondTyper = rdFMCS.BondCompare.CompareOrderExact
    params.AtomCompareParameters.MatchFormalCharge = True
    params.AtomCompareParameters.MatchIsotope = True
    params.AtomCompareParameters.MatchChiralTag = True
    params.AtomCompareParameters.RingMatchesRingOnly = True
    params.BondCompareParameters.RingMatchesRingOnly = True
    params.BondCompareParameters.CompleteRingsOnly = True
    params.BondCompareParameters.MatchStereo = True
    result = rdFMCS.FindMCS([original, docked], params)
    if not result.canceled and result.numAtoms:
        query = Chem.MolFromSmarts(result.smartsString)
        matches = original.GetSubstructMatches(query, uniquify=False, useChirality=True, maxMatches=4097)
        if len(matches) >= 4097:
            raise ValueError('Elite docking partial MCS exceeds the match limit')
        # Serialize an exact original fragment, including its charge/stereo,
        # rather than the generic atom comparators used during MCS search.
        fragments = sorted({tuple(sorted(m)) for m in matches if core_indices <= set(m)})
        for atoms in fragments:
            smarts, query = _growth_query(Chem.MolFragmentToSmarts(original, atomsToUse=list(atoms)))
            if compatible(query):
                return smarts, query, 'reference_core_seeded_mcs_v1'
    # A cancelled MCS is never accepted as a maximum. The independently
    # validated declared core remains a usable exact partial constraint.
    return seed, seed_query, 'declared_reference_core_partial_v1'


def constrain_parent(parent, docked, *, timeout):
    original = Chem.MolFromMolBlock(parent['molblock'], removeHs=False)
    if original is None or docked is None:
        raise ValueError('Missing elite or docking pose')
    original = provenance.restore(original, parent['history'])
    original = Chem.RemoveHs(original)
    docked = Chem.Mol(docked)
    for atom in docked.GetAtoms():
        atom.SetIntProp('_docking_index', atom.GetIdx() + 1)
    docked = Chem.RemoveHs(docked)
    if docked.GetNumConformers() != 1 or not docked.GetConformer().Is3D():
        raise ValueError('Elite docking reference needs one 3D pose')
    if any(not math.isfinite(float(x)) for xyz in docked.GetConformer().GetPositions() for x in xyz):
        raise ValueError('Nonfinite elite docking coordinates')
    if original.GetNumHeavyAtoms() != docked.GetNumHeavyAtoms():
        raise ValueError('Elite docking pose has a different heavy atom count')
    # An exact full-parent match attains the maximum possible common graph.
    # Avoid exponential MCS search on large, otherwise identical elite graphs.
    # Growth replaces an H, including at a specified tetrahedral centre. Keep
    # its handedness but do not require the old H count in the shared graph.
    smarts, query = _growth_query(Chem.MolToSmarts(original))
    tautomer = None
    partial_policy = None
    partial_reason = None
    if not docked.HasSubstructMatch(query, useChirality=True):
        core_uids = {r['uid'] for r in parent['history']['core']}
        core_indices = {a.GetIdx() for a in original.GetAtoms() if a.GetProp('_mn_uid') in core_uids}
        try:
            tautomer = map_keto_enol(original, docked, core_indices, timeout=timeout)
        except ValueError as exc:
            # A partial match makes no equivalence claim about omitted atoms.
            partial_reason = str(exc)
        if tautomer:
            smarts, query = _growth_query(Chem.MolFragmentToSmarts(original, atomsToUse=list(tautomer['core'])))
        else:
            smarts, query, partial_policy = _partial_query(original, docked, core_indices, timeout=timeout)
    left = original.GetSubstructMatches(query, uniquify=False, useChirality=True, maxMatches=4097)
    right = docked.GetSubstructMatches(query, uniquify=False, useChirality=True, maxMatches=4097)
    if not left or not right or max(len(left), len(right)) >= 4097:
        raise ValueError('Elite docking MCS mapping is missing or exceeds the match limit')
    initial = {r['uid'] for r in parent['history']['core']}
    left = [m for m in left if initial <= {original.GetAtomWithIdx(i).GetProp('_mn_uid') for i in m}]
    if not left:
        raise ValueError('Elite docking MCS does not retain the declared reference core')
    if len(left) * len(right) > 65536:
        raise ValueError('Elite docking MCS symmetry mapping exceeds the pair limit')
    # Both structures are already in the receptor frame. Resolve symmetry by
    # minimum coordinate displacement, with explicit deterministic index ties.
    def distance(pair):
        a, b = pair
        value = sum((original.GetConformer().GetAtomPosition(i) -
                     docked.GetConformer().GetAtomPosition(j)).LengthSq() for i, j in zip(a, b))
        return value, a, b
    pairs = ((a, b) for a in left for b in right)
    if partial_policy:
        pairs = [(a, b) for a, b in pairs if all(
            _signature(original.GetAtomWithIdx(i)) == _signature(docked.GetAtomWithIdx(j))
            for i, j in zip(a, b))]
        if not pairs:
            raise ValueError('Elite docking partial mapping changes matched atom chemistry')
    if tautomer:
        pairs = [(a, b) for a, b in pairs if set(a) == set(tautomer['core'])
                 and b == tuple(tautomer['mapping'][i] for i in a)]
        if not pairs:
            raise ValueError('Elite keto/enol shared core is not an exact stereo-compatible match')
    a, b = min(pairs, key=distance)
    history = copy.deepcopy(parent['history'])
    core = []
    for i, j in zip(a, b):
        xyz = list(docked.GetConformer().GetAtomPosition(j))
        original.GetConformer().SetAtomPosition(i, xyz)
        core.append(dict(uid=original.GetAtomWithIdx(i).GetProp('_mn_uid'), xyz=xyz))
    coordinates = {r['uid']: r['xyz'] for r in core}
    for row in history['core']:
        row['xyz'] = coordinates[row['uid']]
    history['constraint_core'] = core
    history['generation_parent_id'] = parent['compound_id']
    reference = dict(compound_id=parent['compound_id'], mcs_smarts=smarts,
        reference_atom_indices=[docked.GetAtomWithIdx(i).GetIntProp('_docking_index') for i in b],
        parent_atom_indices=list(a), mapping_policy='receptor_frame_minimum_displacement_v1')
    if partial_policy:
        reference['partial_mapping'] = dict(policy=partial_policy, original_atoms=original.GetNumAtoms(),
            matched_atoms=len(a), omitted_parent_atom_indices=sorted(set(range(original.GetNumAtoms())) - set(a)))
        if partial_reason:
            reference['partial_mapping']['full_mapping_rejection'] = partial_reason
    if tautomer:
        reference['tautomer_mapping'] = dict(policy=tautomer['policy'],
            parent_atom_indices=list(range(original.GetNumAtoms())),
            reference_atom_indices=[docked.GetAtomWithIdx(i).GetIntProp('_docking_index')
                                    for i in tautomer['mapping']],
            reactive_parent_atom_indices=[list(site) for site in tautomer['sites']],
            original_charge=Chem.GetFormalCharge(original), pose_charge=Chem.GetFormalCharge(docked))
        if tautomer['protonated_amines']:
            reference['tautomer_mapping']['protonated_amine_parent_atom_indices'] = list(tautomer['protonated_amines'])
    history['constraint_reference'] = copy.deepcopy(reference)
    return dict(parent, molblock=Chem.MolToMolBlock(original),
                history=provenance.snapshot(original, history), docking_reference=reference)
