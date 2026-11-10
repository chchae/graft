"""Read completed poses from terminal Glide outputs; never perform docking."""
import csv
import json
import hashlib
import math
import os
from pathlib import Path
import re
import sys
import tarfile


def excluded_inputs(request, structure):
    """Read native archived subjob boundaries, never guess the partitioning."""
    names = set(request.get('exclude_subjobs', []))
    if not names:
        return {}, set(), []
    if any(not isinstance(name, str) or Path(name).name != name for name in names):
        raise ValueError('Invalid excluded Glide subjob name')
    ranges = {}
    with tarfile.open(request['subjob_archive'], 'r:gz') as archive:
        for member in archive.getmembers():
            name = Path(member.name).stem
            if not member.name.endswith('.inp') or name not in names:
                continue
            if not member.isfile() or member.size > 1024*1024 or name in ranges:
                raise ValueError('Ambiguous Glide subjob input archive')
            text = archive.extractfile(member).read().decode()
            values = {}
            for key in ('LIGAND_START', 'LIGAND_END', 'LIGAND_OFFSET'):
                matches = re.findall(r'^\s*'+key+r'\s+([0-9]+)\s*$', text, re.M)
                if len(matches) != 1:
                    raise ValueError(f'Missing/ambiguous native {key} for {name}')
                values[key] = int(matches[0])
            first = values['LIGAND_START'] + values['LIGAND_OFFSET']
            last = values['LIGAND_END'] + values['LIGAND_OFFSET']
            if first < 1 or last < first:
                raise ValueError('Invalid native Glide subjob boundaries')
            ranges[name] = [first, last]
    if set(ranges) != names:
        raise RuntimeError('Missing native input boundaries for timed-out Glide subjobs')
    excluded = set()
    for first, last in ranges.values():
        indices = set(range(first, last+1))
        if excluded & indices:
            raise ValueError('Overlapping timed-out Glide subjob boundaries')
        excluded.update(indices)
    mapping = {}
    allowed = set(request['ids'])
    for index, st in enumerate(structure.StructureReader(request['ligprep_input']), 1):
        match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
        if not match or int(match[1]) not in allowed:
            raise ValueError('Foreign compound in native Glide group input')
        mapping[index] = int(match[1])
    if not excluded <= mapping.keys():
        raise ValueError('Glide timeout boundaries exceed the saved prepared input')
    return mapping, excluded, [dict(job_name=name, first=first, last=last)
                               for name, (first, last) in sorted(ranges.items())]


def collect(request, structure):
    """Select the lowest finite score and its exact pose across input states.

    A stopped compressed stream may have an unreadable tail. Fully decoded
    structures preceding it remain usable; report every input read error.
    """
    allowed = set(request['ids'])
    mapping, excluded, boundaries = excluded_inputs(request, structure)
    scores, poses, errors = {}, {}, []
    skipped = 0
    excluded_poses = 0
    for source in request['inputs']:
        try:
            for st in structure.StructureReader(source):
                match = re.search(r'CMPID_(\d+)', st.title or '')
                if not match or int(match[1]) not in allowed:
                    if mapping:
                        raise ValueError('Foreign compound in merged Glide output')
                    continue
                if mapping:
                    index = st.property.get('i_i_glide_lignum')
                    if isinstance(index, bool) or not isinstance(index, int) or mapping.get(index) != int(match[1]):
                        raise ValueError('Merged Glide pose does not match its native input index/CMPID')
                    if index in excluded:
                        excluded_poses += 1
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
            if mapping:
                raise  # A verified parent output must not silently lose states.
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
    if 'exclude_subjobs' in request:
        report.update(timeout_ids=sorted({mapping[index] for index in excluded}),
                      excluded_subjobs=boundaries, excluded_poses=excluded_poses)
        paths = [*request['inputs']]
        if boundaries:
            paths += [request['subjob_archive'], request['ligprep_input']]
        report['source_checksums'] = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                      for path in paths}
    (folder / 'report.pending.json').write_text(json.dumps(report, indent=2)+'\n')
    os.replace(folder / 'report.pending.json', folder / 'report.json')
    print(f'Glide timeout recovery: finite scores={len(scores)}, unreadable inputs={len(errors)}')
    return report


if __name__ == '__main__':
    from schrodinger import structure
    collect(json.loads(Path(sys.argv[1]).read_text()), structure)
