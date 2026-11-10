import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, database
from graft.config import glide_group_resources, glide_job_options, schrodinger_job_options
from graft.glide_recovery import save_record, load_record
from graft.stages import glide


def requested_slots(host):
    return sum(int(entry.rsplit(':', 1)[1]) for entry in host.split())


def test_weighted_allocation_caps_small_groups_and_uses_budget():
    counts = {1: 3, 2: 100, 3: 900}
    result = glide_group_resources('compute:124', None, counts)
    slots = {ref: requested_slots(host) for ref, host in result.items()}
    assert sum(slots.values()) == 124
    assert 1 <= slots[1] <= 3
    assert slots[1] < slots[2] < slots[3]
    assert glide_group_resources('compute:128', 124, counts) == result


@pytest.mark.parametrize('budget', [1, 3, 5, 12, 124, 2000])
def test_allocation_never_exceeds_budget_or_structure_counts(budget):
    counts = {1: 1, 2: 3, 3: 17, 4: 350, 5: 200}
    result = glide_group_resources(f'compute:{budget}', None, counts)
    assert sum(requested_slots(host) for host in result.values()) == min(budget, sum(counts.values()))
    assert all(1 <= requested_slots(host) <= counts[ref] for ref, host in result.items())


def test_equal_groups_split_cores_evenly_with_deterministic_remainders():
    result = glide_group_resources('compute:124', None, {i: 100 for i in range(23)})
    sizes = [requested_slots(host) for host in result.values()]
    assert sizes.count(6) == 9
    assert sizes.count(5) == 14
    assert result == glide_group_resources('compute:124', None, {i: 100 for i in reversed(range(23))})


def test_multiple_hosts_respect_each_hosts_remaining_slots():
    result = glide_group_resources('a:4 b:8', None, {1: 20, 2: 60}, ['a:2 b:3'])
    totals = {'a': 0, 'b': 0}
    for host in result.values():
        for entry in host.split():
            name, value = entry.split(':')
            totals[name] += int(value)
    assert totals == {'a': 2, 'b': 5}


@pytest.mark.parametrize('reserved', [[None], ['compute'], ['other:2'], ['compute:128']])
def test_uncertain_or_oversized_legacy_requests_block_only_new_submissions(reserved):
    assert glide_group_resources('compute:124', None, {1: 100}, reserved) == {}


def test_bare_host_preserves_vendor_defaults_and_empty_groups_do_not_launch():
    assert glide_group_resources('compute', None, {1: 100, 2: 20}) == {1: 'compute', 2: 'compute'}
    assert glide_group_resources('compute:124', None, {}) == {}


@pytest.mark.parametrize('bad', [0, -1, True, 2.5, '10'])
def test_invalid_counts_are_rejected(bad):
    with pytest.raises(ValueError, match='ligand counts'):
        glide_group_resources('compute:124', None, {1: bad})


@pytest.mark.parametrize('legacy', [None, 'compute:5', 'compute:124', 'unknown'])
@pytest.mark.parametrize('stage_host', [None, 'slurm-cpu'])
def test_stage_uses_prepared_counts_reserves_live_jobs_and_keeps_native_commands(
        tmp_path, monkeypatch, capsys, legacy, stage_host):
    db = tmp_path / 'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        for ref in (1, 2, 3):
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES (?,?,'C',0,'reference')",
                         (ref, f'REF{ref}'))
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) "
                         "VALUES (?,?,'C',1,'ligprepped',?)", (ref + 3, f'GEN{ref}', ref))
    args = SimpleNamespace(db_path=db, output=tmp_path, schrodinger=Path('/suite'),
                           host='compute:128', glide_cpus=12, mcs_smarts=None,
                           reference_poses=Path('refs.maegz'), grid=Path('grid.zip'))
    if stage_host is not None:
        args.glide_host = stage_host
    prepared = tmp_path / 'iter1/ligprep/ligprep.maegz'
    prepared.parent.mkdir(parents=True)
    prepared.write_text('input')
    root = tmp_path / 'iter1/glide'
    root.mkdir()
    glide.prepare_glide_files(root, prepared)
    if legacy:
        directory = root / 'ref_1'
        directory.mkdir()
        command = ['/suite/glide', 'input.in', '-HOST', legacy, '-NJOBS', '5']
        if legacy == 'unknown':
            command = []
        save_record(directory, dict(job_id='existing', command=command))
        original_record = load_record(directory)
    submitted = {}
    def split(*a):
        # One original compound each, but very different LigPrep state counts.
        (root / 'ligprep_counts.json').write_text(json.dumps({1: 2, 2: 10, 3: 1001}))
        files = {}
        for ref in (1, 2, 3):
            directory = root / f'ref_{ref}'
            directory.mkdir(exist_ok=True)
            files[ref] = directory / 'ligprep_group.maegz'
            files[ref].write_text('ligands')
        return files
    def status(suite, directory, *a, **k):
        ref = int(directory.name.split('_')[1])
        return 'RUNNING' if (ref == 1 and legacy) or ref in submitted else None
    def submit(suite, inp, directory, host, **kwargs):
        ref = int(directory.name.split('_')[1])
        assert not (legacy and ref == 1)
        assert kwargs.get('cpus') is None
        assert host.startswith((stage_host or 'compute')+':')
        submitted[ref] = host
        count = {1: 2, 2: 10, 3: 1001}[ref]
        assert kwargs['ligand_count'] == count
        options = glide_job_options(host, count)
        njobs = int(options[options.index('-NJOBS') + 1])
        assert njobs == (count + 49) // 50
        return {}
    monkeypatch.setattr(_core, 'assign_reference_compounds', lambda *a: None)
    monkeypatch.setattr(_core, 'split_ligprep_by_reference', split)
    monkeypatch.setattr(_core, 'extract_reference_pose', lambda *a: None)
    monkeypatch.setattr(_core, 'submit_glide', submit)
    monkeypatch.setattr(glide, 'active_jobs', lambda *a: [])
    monkeypatch.setattr(glide, 'group_status', status)
    monkeypatch.setattr(glide.time, 'monotonic', itertools.count().__next__)
    monkeypatch.setattr(glide.time, 'sleep', lambda *a: None)
    glide._run_stage(args, SimpleNamespace(iteration=1, poll_interval=1,
                                           completion_fraction=0, tail_timeout=0))
    if legacy in ('unknown', 'compute:124') or (stage_host and legacy):
        assert submitted == {}
    else:
        assert requested_slots(submitted[3]) > requested_slots(submitted[2])
        assert sum(requested_slots(host) for host in submitted.values()) == (7 if legacy else 12)
        output = capsys.readouterr().out
        assert '1001 prepared ligands; NJOBS=' in output
        assert 'target ~50 prepared structures/subjob.' in output
    if legacy:
        assert load_record(root / 'ref_1') == original_record
    with database.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM compound WHERE iteration=1 AND state=\'glide_running\'').fetchone()[0] == 3


def test_splitter_records_actual_prepared_structure_counts(tmp_path, monkeypatch):
    import runpy
    import sys
    import types
    class Reader:
        def __init__(self, path):
            pass
        def __iter__(self):
            return iter(SimpleNamespace(title=title) for title in
                        ['CMPID_10', 'CMPID_10', 'CMPID_20', 'CMPID_999', 'unmapped'])
    class Writer:
        def __init__(self, path):
            self.path = Path(path)
        def append(self, st):
            with self.path.open('a') as f:
                f.write(st.title + '\n')
        def close(self):
            pass
    fake = types.ModuleType('schrodinger')
    fake.structure = SimpleNamespace(StructureReader=Reader, StructureWriter=Writer)
    monkeypatch.setitem(sys.modules, 'schrodinger', fake)
    map_file = _core.write_reference_map({1: [(10, 'A', 'C', 1)], 2: [(20, 'B', 'CC', 1)]}, tmp_path)
    script = _core.create_ligprep_splitter(tmp_path)
    monkeypatch.setattr(sys, 'argv', [str(script), 'input.maegz', str(map_file), str(tmp_path)])
    runpy.run_path(str(script), run_name='__main__')
    assert json.loads((tmp_path / 'ligprep_counts.json').read_text()) == {'1': 2, '2': 1}
    assert (tmp_path / 'ref_1/ligprep_group.maegz').read_text().splitlines() == ['CMPID_10', 'CMPID_10']


def test_completed_group_commits_before_next_submission_and_survives_restart(tmp_path, monkeypatch, capsys):
    db = tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        for ref in (1,2,3):
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES (?,?,'C',0,'reference')",
                         (ref, f'REF{ref}'))
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) "
                         "VALUES (?,?,'C',1,'ligprepped',?)", (ref+3, f'GEN{ref}', ref))
    args = SimpleNamespace(db_path=db, output=tmp_path, schrodinger=Path('/suite'), host='compute:3',
                           mcs_smarts=None, reference_poses=Path('refs.maegz'), grid=Path('grid.zip'),
                           target_count=3)
    prepared = tmp_path/'iter1/ligprep/ligprep.maegz'
    prepared.parent.mkdir(parents=True)
    prepared.write_text('prepared')
    root = tmp_path/'iter1/glide'
    launched, events = set(), []
    def split(*a):
        files = {}
        for ref in (1,2,3):
            folder = root/f'ref_{ref}'
            folder.mkdir(exist_ok=True)
            files[ref] = folder/'ligprep_group.maegz'
            files[ref].write_text('ligand')
        return files
    def status(suite, folder, *a, **kw):
        ref = int(folder.name.removeprefix('ref_'))
        return ('DONE' if ref == 1 else 'RUNNING') if ref in launched else None
    def submit(suite, inp, folder, host, **kw):
        ref = int(folder.name.removeprefix('ref_'))
        if ref != 1:
            # Separate DB connection observes committed results while peers are
            # still being submitted, before the final polling loop even starts.
            with database.connect(db) as conn:
                assert conn.execute('SELECT docking_score,state FROM compound WHERE id=4').fetchone() == (-9., 'docked')
                assert conn.execute('SELECT docking_score FROM compound WHERE id=5').fetchone() == (None,)
            assert (root/'best_poses.maegz').read_text() == 'published matching pose'
        events.append(('submit',ref))
        launched.add(ref)
        save_record(folder, dict(job_id=f'job-{ref}', command=['/suite/glide','-HOST',host]))
        if ref == 1:
            (folder/'glide_lib.maegz').write_text('verified native poses')
        return dict(job_id=f'job-{ref}')
    def extract(suite, extractor, output, folder):
        assert folder.name == 'ref_1'
        scores, best = folder/'docking_scores.tsv', folder/'best_poses.maegz'
        scores.write_text('id\tdocking_score\n4\t-9\n')
        best.write_text('matching pose')
        return scores,best
    def merge(suite, merger, inputs, output, cwd):
        assert root/'ref_1/best_poses.maegz' in inputs
        output.write_text('published matching pose')
    update = _core.update_scores
    def commit(scores, path):
        assert (root/'best_poses.maegz').exists()
        changed = update(scores, path)
        events.append(('commit',1))
        return changed
    monkeypatch.setattr(_core,'assign_reference_compounds',lambda *a:None)
    monkeypatch.setattr(_core,'split_ligprep_by_reference',split)
    monkeypatch.setattr(_core,'extract_reference_pose',lambda *a:None)
    monkeypatch.setattr(_core,'submit_glide',submit)
    monkeypatch.setattr(_core,'extract_scores_and_best_poses',extract)
    monkeypatch.setattr(_core,'merge_best_pose_files',merge)
    monkeypatch.setattr(_core,'update_scores',commit)
    monkeypatch.setattr(glide,'group_status',status)
    monkeypatch.setattr(glide,'active_jobs',lambda *a:[])
    monkeypatch.setattr(glide.time,'sleep',lambda *a:None)
    ns = SimpleNamespace(iteration=1,poll_interval=1,completion_fraction=0,tail_timeout=0)
    glide._run_stage(args,ns)
    assert events.index(('commit',1)) < events.index(('submit',2))
    assert events.index(('commit',1)) < events.index(('submit',3))
    assert 'Reference 1 completed: DB updated=1, no-pose=0' in capsys.readouterr().out
    assert database.docking_completion_reason(db,1,3) is not None
    with database.connect(db) as conn:
        assert conn.execute('SELECT state,docking_score FROM compound WHERE id IN (5,6)').fetchall() == [
            ('glide_running',None),('glide_running',None)]
    monkeypatch.setattr(_core,'submit_glide',lambda *a,**k:pytest.fail('Resubmitted saved job'))
    glide._run_stage(args,ns)
    assert events.count(('commit',1)) == 1
    assert (root/'best_poses.maegz').read_text() == 'published matching pose'


def test_interleaved_collection_skips_live_tree_without_blocking_finished_peer(tmp_path, monkeypatch):
    jobs = [dict(reference_id=ref, group_dir=tmp_path/f'ref_{ref}',log_file=tmp_path/f'{ref}.log',
                 output_file=tmp_path/f'{ref}.maegz') for ref in (1,2)]
    jobs[1]['output_file'].write_text('finished native poses')
    native = dict(spec=dict(launchParams=dict(launchDirectory=str(jobs[0]['group_dir']))))
    monkeypatch.setattr(glide,'active_jobs',lambda *a:[native])
    checked, committed = [],[]
    def status(suite,folder,*a,**kw):
        checked.append(folder)
        assert folder == jobs[1]['group_dir']
        return 'DONE'
    monkeypatch.setattr(glide,'group_status',status)
    monkeypatch.setattr(glide,'process_completed_job',lambda iteration,args,job,*a:committed.append(job['reference_id']))
    args = SimpleNamespace(schrodinger=Path('/suite'))
    pending,terminal = glide.collect_finished_groups(1,args,jobs,'extractor','merger',tmp_path,skip_active=True)
    assert pending == [jobs[0]] and terminal == 1 and committed == [2]
    assert checked == [jobs[1]['group_dir']]
    # Regular polling still inspects active trees and enforces saved timeouts.
    def poll(suite,folder,*a,**kw):
        assert kw['timeout_seconds'] == 1800
        checked.append(folder)
        return 'RUNNING'
    monkeypatch.setattr(glide,'group_status',poll)
    assert glide.collect_finished_groups(1,args,pending,'extractor','merger',tmp_path) == (pending,0)
    assert checked[-1] == jobs[0]['group_dir']
