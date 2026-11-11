"""Prepare new Glide references in one installed Schrödinger Python process."""
import json
from pathlib import Path
import sys


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
