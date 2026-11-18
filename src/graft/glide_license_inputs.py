"""Read native failure evidence and copy exact prepared states for tail retries.

Run with the installed Schrodinger Python. This helper never submits jobs.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile


def license_exhausted(text):
    return bool(re.search(r'Checkout attempts from all servers failed; giving up', text, re.I)
                and re.search(r'Unable to check out\s+\d+\s+GLIDE_\S+.*license', text, re.I))


def fields(text):
    result = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if not parts or parts[0].startswith('#'):
            continue
        if len(parts) != 2 or parts[0] in result:
            raise ValueError('Ambiguous saved Glide input')
        result[parts[0]] = parts[1]
    for key, value in [('CORE_RESTRAIN', 'true'), ('CORE_FILTER', 'true'),
                       ('CORECONS_FALLBACK', 'false'), ('CORE_DEFINITION', 'smarts')]:
        if result.get(key, '').lower() != value:
            raise ValueError('License retry needs explicit strict saved Glide core constraints')
    if not all(result.get(key) for key in ('CORE_SMARTS', 'CORE_ATOMS', 'GRIDFILE', 'REF_LIGAND_FILE', 'LIGANDFILE')):
        raise ValueError('License retry has incomplete saved Glide input')
    return result


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def license_failure_inputs(log, archive_path):
    """Final native failure logs, not temporary checkout warnings, authorize retry."""
    names = set(re.findall(r"ERROR: Subjob '([^']+)' has failed after \d+ retries\.", log))
    if not names:
        return {}
    if any(Path(name).name != name for name in names):
        raise ValueError('Invalid failed native Glide subjob name')
    archived = {}
    with tarfile.open(archive_path, 'r:gz') as archive:
        for entry in archive.getmembers():
            name = Path(entry.name).name
            if Path(name).stem not in names or Path(name).suffix not in ('.log', '.inp'):
                continue
            if not entry.isfile() or entry.size > 8*1024*1024 or name in archived:
                raise ValueError('Ambiguous archived Glide license failure evidence')
            archived[name] = archive.extractfile(entry).read().decode()
    tasks = {}
    for name in sorted(names):
        if name+'.log' not in archived:
            raise RuntimeError('Missing final native Glide subjob log')
        if not license_exhausted(archived[name+'.log']):
            continue
        if name+'.inp' not in archived:
            raise RuntimeError('Missing failed native Glide subjob input')
        text = archived[name+'.inp']
        bounds = {}
        for key in ('LIGAND_START', 'LIGAND_END', 'LIGAND_OFFSET'):
            hits = re.findall(r'^\s*'+key+r'\s+(\d+)\s*$', text, re.M)
            if len(hits) != 1:
                raise ValueError('Missing/ambiguous native license retry boundaries')
            bounds[key] = int(hits[0])
        first = bounds['LIGAND_START']+bounds['LIGAND_OFFSET']
        last = bounds['LIGAND_END']+bounds['LIGAND_OFFSET']
        if first < 1 or last < first:
            raise ValueError('Invalid native license retry boundaries')
        tasks[name] = dict(first=first, last=last)
    return tasks


def plan(request, structure):
    log = Path(request['log']).read_text(errors='replace')
    tasks = license_failure_inputs(log, request['archive'])
    serial = request.get('serial_license_failure') and license_exhausted(log)
    if not tasks and not serial:
        return {}
    allowed = set(request['ids'])
    mapping = {}
    for index, st in enumerate(structure.StructureReader(request['ligands']), 1):
        match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
        if not match or int(match[1]) not in allowed:
            raise ValueError('Foreign compound in saved license retry input')
        mapping[index] = int(match[1])
    if serial:
        # Caller verified a FAILED native calculation with no child jobs: its
        # saved parent input is the complete native scientific input.
        tasks[Path(request['input']).stem] = dict(first=1, last=len(mapping))
    seen = set()
    for task in tasks.values():
        if task['last'] > len(mapping) or task['first'] < 1 or task['first'] > task['last']:
            raise ValueError('Out-of-range license retry input slice')
        task['indices'] = list(range(task['first'], task['last']+1))
        indices = set(task['indices'])
        if not indices <= mapping.keys() or seen & indices:
            raise ValueError('Overlapping/out-of-range license retry input slices')
        seen.update(indices)
        task['ids'] = sorted({mapping[i] for i in indices})
    original = Path(request['input'])
    options = fields(original.read_text())
    files = [original, Path(request['ligands']), Path(request['log'])]
    if not serial:
        files.append(Path(request['archive']))
    for key in ('GRIDFILE', 'REF_LIGAND_FILE'):
        path = Path(options[key].strip('"'))
        files.append(path if path.is_absolute() else original.parent/path)
    frozen = {str(p.resolve()): checksum(p) for p in files}
    for task in tasks.values():
        task.update(input=str(original.resolve()), ligands=str(Path(request['ligands']).resolve()), files=frozen)
    return tasks


def ordinary_plan(request, structure):
    """Pack complete compound state sets; no reference owns a CPU partition."""
    original = Path(request['input'])
    options = fields(original.read_text())
    if options.get('PRECISION', '').upper() != 'SP':
        raise ValueError('Shared Glide license accounting requires the SP protocol')
    allowed = set(request['ids'])
    by_id = {}
    for index, st in enumerate(structure.StructureReader(request['ligands']), 1):
        match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
        if not match or int(match[1]) not in allowed:
            raise ValueError('Foreign compound in shared Glide group')
        by_id.setdefault(int(match[1]), []).append(index)
    files = [original, Path(request['ligands'])]
    for key in ('GRIDFILE', 'REF_LIGAND_FILE'):
        path = Path(options[key].strip('"'))
        files.append(path if path.is_absolute() else original.parent/path)
    frozen = {str(p.resolve()): checksum(p) for p in files}
    tasks, indices, ids = {}, [], []
    def finish():
        if indices:
            tasks[f'glide_batch_{len(tasks)+1:06d}'] = dict(kind='ordinary', indices=list(indices), ids=list(ids),
                input=str(original.resolve()), ligands=str(Path(request['ligands']).resolve()), files=frozen)
    for cid, states in by_id.items():
        if indices and len(indices)+len(states) > request['batch_size']:
            finish(); indices, ids = [], []
        indices.extend(states); ids.append(cid)
    finish()
    return dict(tasks=tasks, missing=sorted(allowed-set(by_id)),
                source='shared:'+hashlib.sha256(json.dumps(dict(files=frozen,batch_size=request['batch_size']),sort_keys=True).encode()).hexdigest())


def prepare(request, structure):
    payload = request['payload']
    for path, expected in payload['files'].items():
        if checksum(path) != expected:
            raise RuntimeError('Frozen Glide license retry source changed')
    original = Path(payload['input'])
    text = original.read_text()
    options = fields(text)
    source = Path(options['LIGANDFILE'].strip('"'))
    source = source if source.is_absolute() else original.parent/source
    if source.resolve() != Path(payload['ligands']).resolve():
        raise ValueError('Saved Glide input and frozen prepared states differ')
    folder = Path(request['output'])
    folder.mkdir(parents=True, exist_ok=True)
    wanted = set(payload['indices'])
    found, ids = set(), set()
    pending = folder/'ligands.pending.maegz'
    with structure.StructureWriter(str(pending)) as writer:
        for index, st in enumerate(structure.StructureReader(str(source)), 1):
            if index in wanted:
                match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
                if not match or int(match[1]) not in payload['ids']:
                    raise ValueError('License retry input identity changed')
                writer.append(st)
                found.add(index)
                ids.add(int(match[1]))
    if found != wanted or ids != set(payload['ids']):
        raise ValueError('Incomplete license retry prepared-state selection')
    ligands = folder/'ligands.maegz'
    pending.replace(ligands)
    replacements = dict(LIGANDFILE=str(ligands.resolve()))
    for key in ('GRIDFILE', 'REF_LIGAND_FILE'):
        path = Path(options[key].strip('"'))
        replacements[key] = str((path if path.is_absolute() else original.parent/path).resolve())
    for key, value in replacements.items():
        text, count = re.subn(r'^\s*'+key+r'\s+[^\n]+$', lambda m: key+' '+value, text, flags=re.M)
        if count != 1:
            raise ValueError('Ambiguous license retry input path')
    target = folder/'glide_license_retry.in'
    pending_input = target.with_suffix('.pending')
    pending_input.write_text(text)
    pending_input.replace(target)
    return dict(input=str(target), structures=len(found), ids=sorted(ids))


def validate(request, structure):
    """Tie each retry pose to the exact prepared-state index submitted here."""
    mapping = {}
    for index, st in enumerate(structure.StructureReader(request['ligands']), 1):
        match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
        if not match or int(match[1]) not in request['ids']:
            raise ValueError('Foreign compound in retry prepared-state input')
        mapping[index] = int(match[1])
    poses = 0
    for st in structure.StructureReader(request['poses']):
        match = re.fullmatch(r'CMPID_(\d+)', st.title or '')
        index = st.property.get('i_i_glide_lignum')
        if (not match or isinstance(index, bool) or not isinstance(index, int)
                or mapping.get(index) != int(match[1])):
            raise ValueError('Glide license retry pose does not match its submitted state/CMPID')
        poses += 1
    return dict(poses=poses)


def main():
    from schrodinger import structure
    request = json.loads(Path(sys.argv[1]).read_text())
    result = {'plan': plan, 'prepare': prepare, 'validate': validate, 'ordinary_plan': ordinary_plan}[request['action']](request, structure)
    Path(request['result']).write_text(json.dumps(result))


if __name__ == '__main__':
    main()
