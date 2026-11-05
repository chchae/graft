"""Ranked MCS search with best-elite sidechain attachment construction."""
from itertools import product
import tempfile
from pathlib import Path

from rdkit import Chem

from graft import _core
from graft.chemistry.combichem import (
    _retained_core_query, _terminal_cuts, attachment_atoms, prepare_single_elite_scaffold,
)
from graft.chemistry.libinvent_scaffold import _round_trips, reduce_to_two_attachments


def _reuse_previous_scaffold(inputs, previous, reason, message):
    """Reuse an already validated scaffold only with exact training support."""
    scaffold = previous['scaffold_smiles']
    compatible = [ref['id'] for ref in inputs if _round_trips([ref], scaffold)]
    if inputs[0]['id'] not in compatible or len(compatible) < 2:
        raise RuntimeError('Previous scaffold reuse requires the best elite and at least two compatible training elites.')
    print(f'[MCS] {message}; reusing the previous MCS and attachment positions '
          'for new decoration generation.', flush=True)
    return dict(mcs_smarts=previous['mcs_smarts'], scaffold_smiles=scaffold,
                source='previous_mcs_reused_attachments', source_ids=[ref['id'] for ref in inputs],
                fallback_reason=reason, compatible_elite_ids=compatible,
                previous_scaffold_smiles=scaffold)


def best_elite_scaffold(inputs, previous, docking_smarts):
    """Build from the best molecule, moving one cut inside each old sidechain.

    No original core-sidechain boundary is eligible. Keep the established core,
    exact chemistry and labels, then select the largest verified retained core
    compatible with at least two original training elites. Canonical best-elite
    atom order and bond indices break ties deterministically. If internal cuts
    are unavailable or valid new cores lack training support, reuse the validated
    preceding scaffold.
    """
    if not inputs or previous is None:
        raise RuntimeError('Best-elite sidechain MCS requires ranked elites; no saved scaffold for the preceding iteration.')
    old = Chem.MolFromSmiles(previous['scaffold_smiles'])
    query = Chem.MolFromSmarts(previous['mcs_smarts'])
    if old is None or query is None or not query.GetNumHeavyAtoms():
        raise RuntimeError('Invalid saved MCS/scaffold.')
    dummies = attachment_atoms(old)
    if len(dummies) != 2 or sorted(old.GetAtomWithIdx(i).GetAtomMapNum() for i in dummies) != [0, 1]:
        raise RuntimeError('Saved scaffold requires exactly two terminal single-bond attachments.')
    docking = Chem.MolFromSmarts(docking_smarts)
    if docking is None or not old.HasSubstructMatch(docking, useChirality=False):
        raise RuntimeError('Preceding scaffold does not contain the fixed docking core.')
    if not old.HasSubstructMatch(query, useChirality=False):
        raise RuntimeError('Invalid saved MCS/scaffold: saved scaffold does not contain its MCS.')
    elite = inputs[0]
    smiles = Chem.MolToSmiles(Chem.RemoveHs(Chem.Mol(elite['mol'])), isomericSmiles=True)
    mol = Chem.MolFromSmiles(smiles)
    matched = _core._match_elite_to_scaffold(mol, previous['scaffold_smiles'])
    if matched is None:
        raise RuntimeError('Best elite is incompatible with the preceding scaffold.')
    match = matched[1]
    _, sites, _, dummy_indices, _ = _core._scaffold_attachment_layout(previous['scaffold_smiles'])
    core_atoms = {index for qidx, index in enumerate(match) if qidx not in dummy_indices}
    groups = []
    for label in range(2):
        sidechain = set()
        pending = [match[sites[label]['dummy_qidx']]]
        while pending:
            index = pending.pop()
            if index in core_atoms or index in sidechain:
                continue
            sidechain.add(index)
            pending.extend(atom.GetIdx() for atom in mol.GetAtomWithIdx(index).GetNeighbors())
        cuts = [cut for cut in _terminal_cuts(mol, sidechain, core_atoms)
                if cut['retained_atom_index'] in sidechain and cut['distal_atom_index'] in sidechain]
        if not cuts:
            result = _reuse_previous_scaffold(
                inputs, previous, 'missing_internal_sidechain_cut',
                f'Best elite has no internal single-bond attachment cut in sidechain {label}')
            result['fallback_sidechain_label'] = label
            return result
        groups.append(cuts)
    combinations = sorted(product(*groups), key=lambda cuts: (
        sum(mol.GetAtomWithIdx(i).GetAtomicNum() > 1 for cut in cuts for i in cut['removed_atom_indices']),
        tuple(cut['bond_index'] for cut in cuts)))
    seen = set()
    insufficient_training = False
    for cuts in combinations:
        try:
            result = prepare_single_elite_scaffold(elite, previous, docking_smarts, selected_cuts=list(cuts))
        except (RuntimeError, ValueError):
            continue  # A particular stereo/fragment combination must not weaken validation.
        scaffold = result['scaffold_smiles']
        if scaffold in seen:
            continue
        seen.add(scaffold)
        if not _round_trips(inputs[:1], scaffold):
            continue
        compatible = [ref['id'] for ref in inputs if _round_trips([ref], scaffold)]
        if len(compatible) < 2:
            insufficient_training = True
            continue
        result.update(source='single_elite_sidechain_mcs', compatible_elite_ids=compatible,
                      previous_scaffold_smiles=previous['scaffold_smiles'],
                      previous_core_atom_indices=sorted(core_atoms), replacement_candidates=groups)
        return result
    if insufficient_training:
        return _reuse_previous_scaffold(
            inputs, previous, 'insufficient_sidechain_training_elites',
            'Internal-sidechain scaffolds have fewer than two compatible training elites')
    raise RuntimeError('Best-elite sidechain MCS could not retain two internal attachment points '
                       'and at least two compatible training elites; no previous attachment positions were reused.')


def prepare_ranked_scaffold(inputs, previous, docking_smarts, *, mcs_timeout=60):
    """Halve the ranked prefix, then construct a new core from the best elite."""
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
            elif not _round_trips(selected, info['scaffold_smiles']):
                # A common substructure can still leave undeclared branches or
                # consume an entire decorator. Require usable TL pairs before
                # freezing this candidate, then continue the ranked search.
                attempt['status'] = 'elite_decomposition_failed'
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
        result = best_elite_scaffold(inputs, previous, docking_smarts)
        attempts.append(dict(elite_count=1, source_ids=[inputs[0]['id']],
                             status=('reused_attachments' if result['source'] == 'previous_mcs_reused_attachments'
                                     else 'single_elite_sidechain_mcs')))
    result.update(elite_mcs_policy='ranked_halving_single_elite_sidechains_v3', elite_mcs_attempts=attempts,
                  ranked_elite_ids=[ref['id'] for ref in inputs])
    return result
