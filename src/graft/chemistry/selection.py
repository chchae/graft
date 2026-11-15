"""Score-gated chemical diversity for already ranked original molecules."""
from rdkit import Chem
from rdkit.Chem import rdFingerprintGenerator
from rdkit.SimDivFilters import rdSimDivPickers


POLICY = 'score_half_morgan2_2048_maxmin_v1'


def score_diverse_ids(ranked, count):
    """Keep the best half at >=2N, then MaxMin; return in original score order.

    ranked contains (compound id, SMILES) in scientific score/tie-break order.
    Always seed with its best compound; fixed seed makes distance ties repeatable.
    """
    if type(count) is not int or count < 1:
        raise ValueError('Selection count must be a positive integer')
    if len(ranked) < 2 * count:
        return [cid for cid, _ in ranked[:count]]
    pool = ranked[:(len(ranked) + 1) // 2]
    if len(pool) == count:
        return [cid for cid, _ in pool]
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    fingerprints = []
    for cid, smiles in pool:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f'Invalid SMILES for diversity selection: CMPID_{cid}')
        fingerprints.append(generator.GetFingerprint(mol))
    picks = rdSimDivPickers.MaxMinPicker().LazyBitVectorPick(
        fingerprints, len(pool), count, firstPicks=[0], seed=0)
    return [pool[index][0] for index in sorted(picks)]
