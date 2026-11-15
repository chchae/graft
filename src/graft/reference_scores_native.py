"""Property-only MAEGZ score cache I/O in installed Schrödinger Python."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

CACHE = 's_graft_reference_scores'
SCORES = {'amber_gbsa': 'r_graft_amber_gbsa_score',
          'prime_gbsa': 'r_graft_prime_gbsa_score', 'md_gbsa': 'r_graft_md_gbsa_score'}
RESERVED = {CACHE, *SCORES.values(), 'r_graft_md_gbsa_std', 'i_graft_md_gbsa_snapshots'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def describe(st, index):
    from schrodinger import adapter
    from rdkit import Chem
    props = {k: v for k, v in st.property.items() if k not in RESERVED}
    identity = dict(title=st.title, properties=props,
                    atoms=[dict(a.property) for a in st.atom],
                    bonds=[(b.atom1.index, b.atom2.index, b.order) for b in st.bond])
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    ligand = 0 < sum(a.atomic_number > 1 for a in st.atom) <= 200
    smiles = (Chem.MolToSmiles(Chem.RemoveHs(Chem.MolFromSmiles(adapter.to_smiles(st))))
              if ligand else None)
    return dict(index=index, name=(st.title or '').strip() or f'REF_{index:04d}',
                smiles=smiles, signature=signature, cache=st.property.get(CACHE),
                scores={stage:st.property.get(key) for stage,key in SCORES.items()},
                md_std=st.property.get('r_graft_md_gbsa_std'),
                md_snapshots=st.property.get('i_graft_md_gbsa_snapshots'))


def inspect(path):
    from schrodinger import structure
    before = digest(path)
    records = [describe(st, i) for i, st in enumerate(structure.StructureReader(str(path)), 1)]
    if digest(path) != before:
        raise RuntimeError('Reference file changed during inspection')
    signature = hashlib.sha256(json.dumps([r['signature'] for r in records]).encode()).hexdigest()
    return dict(sha256=before, signature=signature, records=records)


def backup_path(path, checksum):
    return path.with_name(path.name + '.graft-backup-' + checksum + path.suffix)


def update(path, request):
    from schrodinger import structure
    with Path(str(path) + '.graft-scores.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        original = inspect(path)
        if original['sha256'] != request['sha256']:
            raise RuntimeError('Reference file changed during score export; original retained')
        wanted = {int(k): v for k, v in request['updates'].items()}
        if not set(wanted) <= {r['index'] for r in original['records']}:
            raise ValueError('Unknown reference structure index')
        by_index = {r['index']:r for r in original['records']}
        if any(value.get('pose_signature') != by_index[i]['signature']
               or value.get('source_signature') != original['signature'] for i,value in wanted.items()):
            raise ValueError('Reference score payload does not match the original structures')
        encoded = {i: json.dumps(v, sort_keys=True, allow_nan=False) for i, v in wanted.items()}
        if all(r['cache'] == encoded[r['index']] and r['scores'] == wanted[r['index']]['scores']
               and r['md_std'] == (wanted[r['index']]['md_result']['std'] if wanted[r['index']].get('md_result') else None)
               and r['md_snapshots'] == (len(wanted[r['index']]['md_result']['snapshots']) if wanted[r['index']].get('md_result') else None)
               for r in original['records'] if r['index'] in wanted):
            return original
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.graft-reference-', suffix=path.suffix)
        os.close(fd)
        pending = Path(name)
        try:
            with structure.StructureWriter(str(pending)) as writer:
                for i, st in enumerate(structure.StructureReader(str(path)), 1):
                    if i in wanted:
                        st.property[CACHE] = encoded[i]
                        for stage, key in SCORES.items():
                            value = wanted[i]['scores'].get(stage)
                            if value is not None:
                                st.property[key] = float(value)
                            elif key in st.property:
                                del st.property[key]
                        result = wanted[i].get('md_result')
                        if result:
                            st.property['r_graft_md_gbsa_std'] = float(result['std'])
                            st.property['i_graft_md_gbsa_snapshots'] = len(result['snapshots'])
                        else:
                            for key in ('r_graft_md_gbsa_std','i_graft_md_gbsa_snapshots'):
                                if key in st.property:
                                    del st.property[key]
                    writer.append(st)
            checked = inspect(pending)
            if checked['signature'] != original['signature']:
                raise RuntimeError('Reference structures/properties changed; refusing score publication')
            backup = backup_path(path, original['sha256'])
            if backup.exists():
                if digest(backup) != original['sha256']:
                    raise RuntimeError('Reference backup checksum mismatch')
            else:
                shutil.copy2(path, backup)
                with backup.open('rb') as stream:
                    os.fsync(stream.fileno())
            os.chmod(pending, stat.S_IMODE(path.stat().st_mode))
            with pending.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(pending, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return inspect(path)
        finally:
            pending.unlink(missing_ok=True)


def main():
    action, source, request_file, output_file = sys.argv[1:]
    path = Path(source)
    request = json.loads(Path(request_file).read_text())
    result = update(path, request) if action == 'update' else inspect(path)
    Path(output_file).write_text(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
