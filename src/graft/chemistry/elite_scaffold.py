"""Ranked MCS search, alternate sites first, validated site reuse if exhausted."""
import re
import tempfile
from pathlib import Path

from rdkit import Chem

from graft import _core
from graft.chemistry.combichem import (
    _retained_core_query, attachment_atoms,
)
from graft.chemistry.libinvent_scaffold import _round_trips, reduce_to_two_attachments


def _unlabelled(smiles):
    mol = Chem.MolFromSmiles(smiles)
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    # Stereo/isotope-only changes do not relocate an attachment either.
    return Chem.MolToSmiles(mol, isomericSmiles=False)


def alternate_attachments(inputs, previous, docking_smarts):
    """Use only actual single-bond boundaries of the top elite's saved core.

    Never cut into or extend the previous core, invent hydrogen decorators, or
    count a label permutation/symmetry-equivalent scaffold as a new site choice.
    """
    if previous is None:
        raise RuntimeError('Changing attachment points requires the preceding iteration: no saved scaffold.')
    old = Chem.MolFromSmiles(previous['scaffold_smiles'])
    query = Chem.MolFromSmarts(previous['mcs_smarts'])
    if old is None or query is None or not query.GetNumHeavyAtoms():
        raise RuntimeError('Invalid saved MCS/scaffold.')
    # Supplied complete scaffolds encode their old attachment markers in the
    # SMARTS too. Preserve their retained MCS, not those terminal dummy markers.
    generation_smarts = previous['mcs_smarts']
    if any(atom.GetAtomicNum() == 0 for atom in query.GetAtoms()):
        retained_query = Chem.RWMol(query)
        for index in sorted((a.GetIdx() for a in query.GetAtoms() if a.GetAtomicNum() == 0), reverse=True):
            retained_query.RemoveAtom(index)
        query = retained_query.GetMol()
        generation_smarts = Chem.MolToSmarts(query)
    count = len(attachment_atoms(old))
    labels = sorted(a.GetAtomMapNum() for a in old.GetAtoms() if a.GetAtomicNum() == 0)
    if count != 2 or labels != [0, 1]:
        raise RuntimeError('Saved scaffold requires exactly two terminal single-bond attachments.')
    if not old.HasSubstructMatch(query, useChirality=False):
        raise RuntimeError('Invalid saved MCS/scaffold: saved scaffold does not contain its MCS.')
    core = _retained_core_query(old)
    docking = Chem.MolFromSmarts(docking_smarts)
    if not old.HasSubstructMatch(docking, useChirality=False):
        raise RuntimeError('Preceding scaffold does not contain the fixed docking core.')
    mol = Chem.RemoveHs(Chem.Mol(inputs[0]['mol']))
    candidates = set()
    for match in mol.GetSubstructMatches(core, useChirality=True, uniquify=False):
        retained = set(match)
        boundaries = [b for b in mol.GetBonds()
                      if (b.GetBeginAtomIdx() in retained) != (b.GetEndAtomIdx() in retained)]
        if (len(boundaries) != count or any(b.IsInRing() or b.GetBondType() != Chem.BondType.SINGLE
                                           for b in boundaries)):
            continue
        fragmented = Chem.FragmentOnBonds(mol, [b.GetIdx() for b in boundaries],
                                         dummyLabels=[(i + 1, i + 1) for i in range(count)])
        mappings = []
        fragments = Chem.GetMolFrags(fragmented, asMols=True, fragsMolAtomMapping=mappings)
        for fragment, mapping in zip(fragments, mappings):
            if {i for i in mapping if i < mol.GetNumAtoms()} != retained:
                continue
            for atom in fragment.GetAtoms():
                atom.SetAtomMapNum(0)
                if atom.GetAtomicNum() == 0:
                    atom.SetAtomMapNum(atom.GetIsotope() - 1)
                    atom.SetIsotope(0)
            smiles = re.sub(r'(?<!\[)\*', '[*:0]', Chem.MolToSmiles(fragment, isomericSmiles=True))
            if _unlabelled(smiles) != _unlabelled(previous['scaffold_smiles']):
                candidates.add(smiles)
    for smiles in sorted(candidates):
        scaffold = Chem.MolFromSmiles(smiles)
        if (not scaffold.HasSubstructMatch(query, useChirality=False)
                or not scaffold.HasSubstructMatch(docking, useChirality=False)
                or not _round_trips(inputs[:1], smiles)):
            continue
        compatible = [ref['id'] for ref in inputs if _round_trips([ref], smiles)]
        if len(compatible) < 2:
            continue
        return dict(mcs_smarts=generation_smarts, scaffold_smiles=smiles,
                    source='previous_mcs_alternate_attachments', source_ids=[inputs[0]['id']],
                    compatible_elite_ids=compatible,
                    previous_scaffold_smiles=previous['scaffold_smiles'])
    compatible = [ref['id'] for ref in inputs if _round_trips([ref], previous['scaffold_smiles'])]
    if inputs[0]['id'] in compatible and len(compatible) >= 2:
        return dict(mcs_smarts=previous['mcs_smarts'], scaffold_smiles=previous['scaffold_smiles'],
                    source='previous_mcs_reused_attachments', source_ids=[ref['id'] for ref in inputs],
                    compatible_elite_ids=compatible, fallback_reason='no_alternate_attachment_points',
                    previous_scaffold_smiles=previous['scaffold_smiles'])
    raise RuntimeError('No chemically different valid attachment points for the preceding MCS; '
                       'the preceding scaffold also cannot be reused with the top elite and '
                       'at least two compatible training elites.')


def prepare_ranked_scaffold(inputs, previous, docking_smarts, *, mcs_timeout=60):
    """Halve the ranked prefix; retain old core and prefer different sites."""
    if not inputs:
        raise RuntimeError('Adaptive MCS requires ranked preceding-iteration elites.')
    old_core = (_retained_core_query(Chem.MolFromSmiles(previous['scaffold_smiles']))
                if previous else None)
    attempts = []
    count = len(inputs)
    while count > 1:
        selected = inputs[:count]
        attempt = dict(elite_count=count, source_ids=[ref['id'] for ref in selected])
        print(f'[MCS] Trying highest-ranked {count}/{len(inputs)} elites (timeout={mcs_timeout}s).', flush=True)
        try:
            with tempfile.TemporaryDirectory() as folder:
                info = _core.prepare_libinvent_scaffold(
                    selected, None, Path(folder), mcs_timeout=mcs_timeout,
                    reject_excess_attachments=True)
        except (_core.InsufficientAttachmentSitesError, _core.ExcessAttachmentSitesError) as error:
            attempt.update(status='invalid_attachment_count', mcs_smarts=error.mcs_smarts)
            if isinstance(error, _core.ExcessAttachmentSitesError) and previous:
                adjusted = reduce_to_two_attachments(selected, error.mcs_smarts, previous, docking_smarts)
                if adjusted is not None:
                    attempt['status'] = 'different_mcs'
                    attempts.append(attempt)
                    result = dict(adjusted, source='previous_attachment_guided_mcs',
                                  source_ids=attempt['source_ids'],
                                  attempted_mcs_smarts=error.mcs_smarts,
                                  attempted_attachment_count=error.count)
                    break
        else:
            scaffold = Chem.MolFromSmiles(info['scaffold_smiles'])
            core = _retained_core_query(scaffold)
            same = (old_core is not None and core.GetNumAtoms() == old_core.GetNumAtoms()
                    and core.GetNumBonds() == old_core.GetNumBonds()
                    and core.HasSubstructMatch(old_core, useChirality=False))
            attempt['mcs_smarts'] = info['mcs_smarts']
            if same:
                attempt['status'] = 'same_as_previous_mcs'
            elif not scaffold.HasSubstructMatch(Chem.MolFromSmarts(docking_smarts), useChirality=False):
                raise RuntimeError('Generation scaffold does not contain the fixed docking core.')
            else:
                attempt['status'] = 'different_mcs'
                attempts.append(attempt)
                result = dict(mcs_smarts=info['mcs_smarts'], scaffold_smiles=info['scaffold_smiles'],
                              source='previous_iteration_elites' if count == len(inputs)
                              else 'ranked_elite_subset_mcs', source_ids=attempt['source_ids'])
                break
        attempts.append(attempt)
        count = max(1, count // 2)
        print(f"[MCS] {attempt['elite_count']} elites: {attempt['status']}; trying highest-ranked {count}.", flush=True)
    else:
        result = alternate_attachments(inputs, previous, docking_smarts)
        attempts.append(dict(elite_count=1, source_ids=[inputs[0]['id']],
                             status='reused_attachments' if result['source'] == 'previous_mcs_reused_attachments'
                             else 'alternate_attachments'))
    result.update(elite_mcs_policy='ranked_halving_alternate_attachments_v2', elite_mcs_attempts=attempts,
                  ranked_elite_ids=[ref['id'] for ref in inputs])
    return result
