"""Prepare new Glide references in one installed Schrödinger Python process."""
import json
from pathlib import Path
import sys

if __package__:
    from .glide_pose_selection import CoreValidator
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from glide_pose_selection import CoreValidator


def prepare(request):
    from schrodinger import adapter, structure

    wanted = {entry['name'] for entry in request['groups']}
    references = {}
    with structure.StructureReader(request['references']) as reader:
        for st in reader:
            name = st.title.strip() if st.title else ''
            # Preserve the single-reference extractor's first matching pose.
            if name in wanted and name not in references:
                references[name] = st
            if wanted == references.keys():
                break
    missing = wanted - references.keys()
    if missing:
        raise RuntimeError(f'Reference poses not found: {sorted(missing)}')

    result = {}
    for entry in request['groups']:
        st = references[entry['name']]
        core = None
        smarts = entry.get('smarts', request['smarts'])
        if smarts:
            matches = adapter.evaluate_smarts(st, smarts, 'core_atoms' not in entry)
            if not matches:
                raise RuntimeError(f"SMARTS does not match reference ligand: {entry['name']}")
            chosen = entry.get('core_atoms', matches[0])
            if list(chosen) not in [list(m) for m in matches]:
                raise RuntimeError('Frozen elite MCS atom mapping does not match native reference')
            core = ','.join(str(i) for i in chosen)
        result[str(entry['id'])] = core

    # Inspect actual LigPrep states before submission. Keep their order and
    # original input indices: native CORE_FILTER skips nonmatches during docking.
    # Removing states here would invalidate durable timeout subjob boundaries.
    for entry in request['groups']:
        if not entry.get('ligands'):
            continue  # Compatibility callers that only export references.
        smarts = entry.get('smarts', request['smarts'])
        if not smarts:
            continue  # Reference-export compatibility; strict collection requires an explicit core.
        if not result[str(entry['id'])]:
            raise RuntimeError('Explicit SMARTS core required for prepared Glide states')
        validator = CoreValidator(references[entry['name']], smarts,
                                  [int(i) for i in result[str(entry['id'])].split(',')])
        accepted, rejected = 0, []
        for index, st in enumerate(structure.StructureReader(entry['ligands']), 1):
            if validator.matches(st):
                accepted += 1
            else:
                rejected.append(dict(index=index, title=st.title))
        report = dict(policy='prepared_core_match_v1', matched_states=accepted,
                      rejected_states=rejected, smarts=smarts,
                      reference_atoms=validator.atoms)
        output = Path(entry['output']).parent/'core_preflight.json'
        pending = output.with_suffix('.pending.json')
        pending.write_text(json.dumps(report, indent=2)+'\n')
        pending.replace(output)
        print(f"Glide reference {entry['id']}: core matching states={accepted}; "
              f"nonmatching states={len(rejected)} (native CORE_FILTER rejects these).")

    # Validate every requested reference before publishing any new input.
    for entry in request['groups']:
        output = Path(entry['output'])
        pending = output.with_name('_reference.pending.maegz')
        with structure.StructureWriter(str(pending)) as writer:
            writer.append(references[entry['name']])
        pending.replace(output)
    return result


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    result = prepare(request)
    output = Path(request['result'])
    pending = output.with_suffix('.tmp')
    pending.write_text(json.dumps(result))
    pending.replace(output)


if __name__ == '__main__':
    main()
