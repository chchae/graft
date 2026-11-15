"""Export the frozen elites used by the following CReM iteration."""
import io

from rdkit import Chem

from graft.paths import stage_directory
from graft.chemistry.scaffold import _publish


def export_elites(output, generation_iteration, plan):
    """Use saved order/poses, never rerank or change scientific records."""
    if generation_iteration < 2:
        return 0  # The initial frame is not a selected elite.
    cycle = generation_iteration - 1
    smiles = []
    sdf = io.StringIO()
    writer = Chem.SDWriter(sdf)
    try:
        for rank, parent in enumerate(plan['parents'], 1):
            name = f"CMPID_{parent['compound_id']}"
            mol = Chem.MolFromMolBlock(parent['molblock'], removeHs=False)
            if mol is None or mol.GetNumConformers() != 1:
                raise ValueError(f'Invalid saved elite structure: {name}')
            mol.SetProp('_Name', name)
            for key, value in dict(compound_id=parent['compound_id'],
                                   selected_cycle=cycle,
                                   generation_iteration=generation_iteration,
                                   elite_rank=rank, score_source=plan['score_source'],
                                   selection_score=parent['score'],
                                   smiles=parent['smiles'],
                                   coordinate_source='saved_generation_parent').items():
                mol.SetProp(key, str(value))
            writer.write(mol)
            smiles.append(f"{parent['smiles']}\t{name}\t{rank}\t"
                          f"{plan['score_source']}\t{parent['score']}\n")
        writer.flush()
        structures = sdf.getvalue()
    finally:
        writer.close()
    folder = stage_directory(output, cycle, 'elite')
    # Build both complete files before publishing either; each replacement is atomic.
    exports = (('elites.smi', ''.join(smiles)), ('elites.sdf', structures))
    for filename, contents in exports:
        path = folder / filename
        if not path.exists() or path.read_text() != contents:
            _publish(path, contents)
    # Remove only identical old exports, after both new files are published.
    previous = stage_directory(output, cycle, 'reinvent') / 'crem'
    for filename, contents in exports:
        path = previous / filename
        if path.is_file() and path.read_text() == contents:
            path.unlink()
    return len(smiles)
