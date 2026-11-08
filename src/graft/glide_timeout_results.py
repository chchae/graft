"""Read completed poses from terminal Glide outputs; never perform docking."""
import csv
import json
import math
import os
from pathlib import Path
import re
import sys


def collect(request, structure):
    """Select the lowest finite score and its exact pose across input states.

    A stopped compressed stream may have an unreadable tail. Fully decoded
    structures preceding it remain usable; report every input read error.
    """
    allowed = set(request['ids'])
    scores, poses, errors = {}, {}, []
    skipped = 0
    for source in request['inputs']:
        try:
            for st in structure.StructureReader(source):
                match = re.search(r'CMPID_(\d+)', st.title or '')
                if not match or int(match[1]) not in allowed:
                    continue
                try:
                    score = float(st.property['r_i_docking_score'])
                except (KeyError, TypeError, ValueError):
                    skipped += 1
                    continue
                if not math.isfinite(score):
                    skipped += 1
                    continue
                cid = int(match[1])
                if cid not in scores or score < scores[cid]:
                    scores[cid], poses[cid] = score, st.copy()
        except Exception as exc:
            errors.append(dict(path=source, error=str(exc)))
    folder = Path(request['output'])
    folder.mkdir(parents=True, exist_ok=True)
    pending = folder / 'best_poses.pending.maegz'
    with structure.StructureWriter(str(pending)) as writer:
        for cid in sorted(poses):
            poses[cid].property['i_user_compound_id'] = cid
            writer.append(poses[cid])
    pending.replace(folder / 'best_poses.maegz')
    with (folder / 'docking_scores.pending.tsv').open('w', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(['id', 'docking_score'])
        writer.writerows(sorted(scores.items()))
    os.replace(folder / 'docking_scores.pending.tsv', folder / 'docking_scores.tsv')
    report = dict(scored=len(scores), skipped_invalid_scores=skipped, read_errors=errors,
                  inputs=request['inputs'], ids=request['ids'])
    (folder / 'report.pending.json').write_text(json.dumps(report, indent=2)+'\n')
    os.replace(folder / 'report.pending.json', folder / 'report.json')
    print(f'Glide timeout recovery: finite scores={len(scores)}, unreadable inputs={len(errors)}')
    return report


if __name__ == '__main__':
    from schrodinger import structure
    collect(json.loads(Path(sys.argv[1]).read_text()), structure)
