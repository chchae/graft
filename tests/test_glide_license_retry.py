"""License-only tail admission, exact state slicing, barriers and restart recovery."""
import copy
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sqlite3
import tarfile
from types import SimpleNamespace

import pytest

from graft import _core, database, glide_license_inputs as native, glide_timeout_results
from graft.glide_recovery import load_record, save_record
from graft.stages import glide_license_retry as retry
from graft.states import CompoundState as State


LICENSE = ('WARNING Checkout attempts from all servers failed; giving up.\n'
           'atlas:53000: RESOURCE_EXHAUSTED: insufficient licenses for feature GLIDE_SP_DOCKING v73\n'
           'WARNING Unable to check out 4 GLIDE_SP_DOCKING v73 license(s)\n')


class Pose:
    def __init__(self, cid, score=None, tag=None, index=None):
        self.title = f'CMPID_{cid}'
        self.property = {} if score is None else dict(r_i_docking_score=score, tag=tag)
        if index is not None:
            self.property['i_i_glide_lignum'] = index
    def copy(self):
        return copy.deepcopy(self)


def write_poses(path, poses):
    Path(path).write_text(json.dumps([dict(title=p.title, properties=p.property) for p in poses]))


def structures():
    def reader(path):
        for row in json.loads(Path(path).read_text()):
            p = Pose(int(row['title'].removeprefix('CMPID_')))
            p.property = row['properties']
            yield p
    class Writer:
        def __init__(self, path): self.path, self.rows = path, []
        def __enter__(self): return self
        def append(self, p): self.rows.append(p)
        def __exit__(self, *a):
            if self.rows: write_poses(self.path, self.rows)
    return SimpleNamespace(StructureReader=reader, StructureWriter=Writer)


def archive(path, entries):
    with tarfile.open(path, 'w:gz') as tar:
        for name, text in entries.items():
            data = text.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))


@pytest.fixture
def project(tmp_path, monkeypatch):
    db = tmp_path/'project.sqlite'
    with sqlite3.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(10,'REF','C',0,'reference')")
        for cid in (1, 2, 3):
            conn.execute('INSERT INTO compound(id,name,smiles,iteration,state,similar_to,docking_score,failed_stage,failure_message) '
                         'VALUES(?,?,?,1,?,10,?,?,?)',
                         (cid, f'CMPID_{cid}', 'CC', State.DOCKED if cid == 1 else State.FAILED,
                          -5 if cid == 1 else None, None if cid == 1 else 'glide', None if cid == 1 else 'No Glide pose'))
    root = tmp_path/'iter01/06-glide'
    group = root/'ref_10'
    group.mkdir(parents=True)
    grid, reference = group/'grid.zip', group/'_reference.maegz'
    grid.write_bytes(b'frozen grid');reference.write_bytes(b'frozen reference')
    inp = group/'glide_10.in'
    inp.write_text(f'GRIDFILE {grid}\nLIGANDFILE {group / "ligprep_group.maegz"}\n'
                   f'REF_LIGAND_FILE {reference}\nPRECISION SP\nCORE_DEFINITION smarts\n'
                   'CORE_SMARTS "[#6]-[#6]"\nCORE_ATOMS 1,2\nCORE_RESTRAIN True\n'
                   'CORE_FILTER True\nCORECONS_FALLBACK False\nCORE_POS_MAX_RMSD 1.0\nPOSES_PER_LIG 10\n')
    write_poses(group/'ligprep_group.maegz', [Pose(1, tag='success'), Pose(2, tag='state-A'),
                                              Pose(2, tag='state-B'), Pose(3, tag='unrelated')])
    write_poses(group/'best_poses.maegz', [Pose(1, -5, 'original')])
    log = group/'glide_10.log'
    log.write_text("ERROR: Subjob 'glide_10-0001' has failed after 2 retries.\n"+LICENSE+
                   "ERROR: Subjob 'glide_10-0002' has failed after 2 retries.\nsegfault\n")
    archive(group/'glide_10_subjobs.tar.gz', {
        'glide_10-0001.log': LICENSE,
        'glide_10-0001.inp': 'LIGAND_START 2\nLIGAND_END 3\nLIGAND_OFFSET 0\n',
        'glide_10-0002.log': 'segmentation fault',
        'glide_10-0002.inp': 'LIGAND_START 4\nLIGAND_END 4\nLIGAND_OFFSET 0\n'})
    save_record(group, dict(job_id='original', command=['/suite/glide', str(inp), '-HOST', 'slurm-cpu:16'], downloaded=True))
    args = SimpleNamespace(db_path=db, schrodinger=Path('/suite'), host='slurm-cpu:348',
                           glide_host='slurm-cpu', glide_cpus=348, use_slurm=True, glide_pose_point=None, glide_job_timeout=1800)
    monkeypatch.setattr(retry, 'job_details', lambda *a: [dict(jobId='original', status='DONE'),
                                                       dict(jobId='failed', jobName='glide_10-0001', status='FAILED')])
    monkeypatch.setattr(retry, 'active_jobs', lambda *a: [])
    monkeypatch.setattr(glide_timeout_results.CoreValidator, 'from_input',
                        lambda *a: SimpleNamespace(rmsd=lambda p: 0., tolerance=1., smarts='CC', atoms=[1, 2]))
    def run(command, cwd):
        request = json.loads(Path(command[2]).read_text())
        if Path(command[1]).name == 'glide_license_inputs.py':
            result = {'plan': native.plan, 'prepare': native.prepare, 'validate': native.validate,
                      'ordinary_plan': native.ordinary_plan}[request['action']](request, structures())
            Path(request['result']).write_text(json.dumps(result))
        else:
            glide_timeout_results.collect(request, structures())
    monkeypatch.setattr(_core, 'run_command', run)
    return args, root, group


def enroll(project):
    args, root, group = project
    retry.inspect_downloaded(args, 1, root)
    tasks = database.glide_license_retries(args.db_path, 1)
    assert len(tasks) == 1
    return tasks[0]


def test_archive_evidence_enrolls_only_license_failure_and_blocks_downstream(project):
    args, root, group = project
    task = enroll(project)
    assert task['payload']['indices'] == [2, 3]
    assert task['payload']['ids'] == [2]
    assert database.docking_completion_reason(args.db_path, 1, 3) == 'Glide license retry queue pending'
    assert database.claim_prime_gbsa_candidates(args.db_path, 1, 2, 3) == []
    retry.inspect_downloaded(args, 1, root)
    assert len(database.glide_license_retries(args.db_path, 1)) == 1
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT docking_score,state FROM compound WHERE id=1').fetchone() == (-5, 'docked')


def test_prepare_copies_only_exact_failed_states_preserving_science(project, tmp_path):
    args, root, group = project
    task = enroll(project)
    out = tmp_path/'retry'
    result = native.prepare(dict(payload=task['payload'], output=str(out)), structures())
    rows = list(structures().StructureReader(out/'ligands.maegz'))
    assert [p.title for p in rows] == ['CMPID_2', 'CMPID_2']
    original = (group/'glide_10.in').read_text().splitlines()
    changed = Path(result['input']).read_text().splitlines()
    assert [line for line in changed if not line.startswith('LIGANDFILE ')] == [line for line in original if not line.startswith('LIGANDFILE ')]


def test_queue_claims_are_atomic_and_license_shortage_survives_restart(project):
    args, _, _ = project
    task = enroll(project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: database.claim_glide_license_retry(args.db_path, 1, 100), range(2)))
    assert sorted(str(x) for x in ids) == sorted([str(task['id']), 'None'])
    database.finish_glide_license_retry(args.db_path, task['id'], license_error=True, now=100)
    assert database.claim_glide_license_retry(args.db_path, 1, 159) is None
    assert database.claim_glide_license_retry(args.db_path, 1, 160) == task['id']
    assert database.glide_license_retries(args.db_path, 1)[0]['attempts'] == 2
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT state,failure_message FROM compound WHERE id=2').fetchone() == ('failed', 'No Glide pose')


def test_backfill_overlaps_live_original_without_resubmitting_uncertain_attempt(project, monkeypatch):
    args, root, group = project
    task = enroll(project)
    calls = []
    def submit(suite, inp, folder, host, **kw):
        calls.append((host, kw))
        save_record(folder, dict(job_id=None, command=['/suite/glide', str(inp)], downloaded=False))
    monkeypatch.setattr(_core, 'submit_glide', submit)
    record = load_record(group);record['downloaded'] = False;save_record(group, record)
    monkeypatch.setattr(retry, 'job_details', lambda *a: [dict(jobId='original', status='RUNNING')])
    retry.run_tail(args, 1, root, None, None)
    assert calls == [('slurm-cpu:1', {'memory_mb': 2048})]
    monkeypatch.setattr(retry, 'group_status', lambda *a, **kw: 'UNKNOWN')
    retry.run_tail(args, 1, root, None, None)
    assert len(calls) == 1
    assert database.glide_license_retries(args.db_path, 1)[0]['state'] == 'running'


@pytest.mark.parametrize('improved', [-8, -3])
def test_collection_merges_best_states_without_losing_old_poses(project, tmp_path, improved):
    args, root, group = project
    # CMPID_1's successful state is index 1; its failed state is index 2.
    write_poses(group/'ligprep_group.maegz', [Pose(1), Pose(1), Pose(2), Pose(3)])
    retry.inspect_downloaded(args, 1, root)
    database.claim_glide_license_retry(args.db_path, 1, 0)
    task = database.glide_license_retries(args.db_path, 1)[0]
    folder = tmp_path/'attempt'
    folder.mkdir()
    native.prepare(dict(payload=task['payload'], output=str(folder)), structures())
    output = folder/'retry_lib.maegz'
    write_poses(output, [Pose(1, improved, 'new-state', index=1), Pose(2, -7, 'recovered', index=2)])
    calls = []
    retry._collect(args, 1, root, task, folder, output, lambda *a: calls.append(True), 'merge')
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT docking_score FROM compound WHERE id=1').fetchone()[0] == min(-5, improved)
        assert conn.execute('SELECT docking_score,state,failed_stage FROM compound WHERE id=2').fetchone() == (-7, 'docked', None)
        assert conn.execute('SELECT docking_score,state FROM compound WHERE id=3').fetchone() == (None, 'failed')
    best = list(structures().StructureReader(group/'best_poses.maegz'))
    assert best[0].property['tag'] == ('new-state' if improved < -5 else 'original')
    assert calls == [True]
    assert database.glide_license_retries(args.db_path, 1) == []
    assert database.docking_completion_reason(args.db_path, 1, 3) is None


def test_collect_missing_existing_pose_cannot_overwrite_valid_results(project, tmp_path):
    args, root, group = project
    task = enroll(project)
    database.claim_glide_license_retry(args.db_path, 1, 0)
    task = database.glide_license_retries(args.db_path, 1)[0]
    (group/'best_poses.maegz').unlink()
    folder = tmp_path/'attempt';folder.mkdir()
    native.prepare(dict(payload=task['payload'], output=str(folder)), structures())
    output = folder/'retry_lib.maegz';write_poses(output, [Pose(2, -7, index=1)])
    with pytest.raises(RuntimeError, match='lose or mix'):
        retry._collect(args, 1, root, task, folder, output, lambda *a: pytest.fail('premature publication'), None)
    assert not (group/'best_poses.maegz').exists()
    assert database.glide_license_retries(args.db_path, 1)[0]['state'] == 'running'


def test_changed_frozen_inputs_and_downstream_work_block_retry(project, tmp_path):
    args, _, group = project
    task = enroll(project)
    (group/'grid.zip').write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='source changed'):
        native.prepare(dict(payload=task['payload'], output=str(tmp_path/'new')), structures())
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE compound SET prime_gbsa_score=-10 WHERE id=1')
    with pytest.raises(RuntimeError, match='downstream'):
        database.claim_glide_license_retry(args.db_path, 1, 0)


def test_license_error_requeues_but_successful_fallback_does_not(project, monkeypatch, tmp_path):
    args, root, _ = project
    task = enroll(project)
    database.claim_glide_license_retry(args.db_path, 1, 0)
    folder = root/'license-retries'/f"task_{task['id']}"/'attempt_1';folder.mkdir(parents=True)
    inp = folder/'glide_license_retry.in';inp.write_text('saved retry')
    save_record(folder, dict(job_id='retry-native', downloaded=True, command=['/suite/glide', str(inp)]))
    (folder/'glide_license_retry.log').write_text(LICENSE)
    monkeypatch.setattr(retry, 'group_status', lambda *a, **kw: 'FAILED')
    retry.run_tail(args, 1, root, None, None)
    task = database.glide_license_retries(args.db_path, 1)[0]
    assert task['state'] == 'pending' and task['not_before'] > 0
    assert load_record(folder)['job_id'] == 'retry-native'


def test_temporary_server_failure_is_not_terminal_license_evidence():
    assert not native.license_exhausted('atlas: insufficient licenses for feature GLIDE_SP_DOCKING\nvirusgpu1 checkout succeeded')
    assert native.license_exhausted(LICENSE)


def test_failed_inspection_blocks_scoring_until_verified_recovery(project, monkeypatch):
    args, root, group = project
    (group/'glide_10_subjobs.tar.gz').unlink()
    with pytest.raises(FileNotFoundError):
        retry.inspect_downloaded(args, 1, root)
    assert database.glide_license_inspection_pending(args.db_path, 1)
    assert database.docking_completion_reason(args.db_path, 1, 3) == 'Glide license failure inspection pending'
    assert database.claim_prime_gbsa_candidates(args.db_path, 1, 2, 3) == []
    assert not load_record(group).get('license_failures_inspected')


def test_serial_parent_license_failure_retries_its_complete_input(project, monkeypatch):
    args, root, group = project
    (group/'glide_10.log').write_text(LICENSE)
    (group/'glide_10_subjobs.tar.gz').unlink()
    monkeypatch.setattr(retry, 'job_details', lambda *a: [dict(jobId='original', status='FAILED')])
    task = enroll(project)
    assert task['payload']['indices'] == [1, 2, 3, 4]
    assert task['payload']['ids'] == [1, 2, 3]


def test_final_scientific_failure_does_not_inherit_old_license_warning(project):
    args, root, group = project
    archive(group/'glide_10_subjobs.tar.gz', {
        'glide_10-0001.log': 'scientific failure, no license failure',
        'glide_10-0002.log': 'segmentation fault'})
    retry.inspect_downloaded(args, 1, root)
    assert database.glide_license_retries(args.db_path, 1) == []
    assert not database.glide_license_inspection_pending(args.db_path, 1)


@pytest.mark.parametrize('bad', ['overlap', 'out_of_range', 'duplicate'])
def test_ambiguous_native_boundaries_are_never_guessed(project, bad):
    args, root, group = project
    text = 'LIGAND_START 2\nLIGAND_END 999\nLIGAND_OFFSET 0\n' if bad == 'out_of_range' else (
        'LIGAND_START 2\nLIGAND_START 3\nLIGAND_END 3\nLIGAND_OFFSET 0\n' if bad == 'duplicate' else
        'LIGAND_START 2\nLIGAND_END 3\nLIGAND_OFFSET 0\n')
    archive(group/'glide_10_subjobs.tar.gz', {
        'glide_10-0001.log': LICENSE, 'glide_10-0001.inp': text,
        'glide_10-0002.log': LICENSE, 'glide_10-0002.inp': 'LIGAND_START 3\nLIGAND_END 4\nLIGAND_OFFSET 0\n'})
    with pytest.raises(ValueError):
        retry.inspect_downloaded(args, 1, root)
    assert database.glide_license_inspection_pending(args.db_path, 1)
    assert database.glide_license_retries(args.db_path, 1) == []


def test_foreign_retry_pose_is_rejected_before_publication(project, tmp_path):
    args, root, group = project
    task = enroll(project)
    database.claim_glide_license_retry(args.db_path, 1, 0)
    task = database.glide_license_retries(args.db_path, 1)[0]
    folder = tmp_path/'attempt'
    native.prepare(dict(payload=task['payload'], output=str(folder)), structures())
    output = folder/'retry_lib.maegz'
    write_poses(output, [Pose(3, -100, index=1)])  # Native input index 1 belongs to CMPID_2.
    original = (group/'best_poses.maegz').read_bytes()
    with pytest.raises(ValueError, match='submitted state'):
        retry._collect(args, 1, root, task, folder, output, lambda *a: pytest.fail('foreign pose'), None)
    assert (group/'best_poses.maegz').read_bytes() == original


def test_crash_after_pose_publication_repeats_collection_without_losing_results(project, tmp_path, monkeypatch):
    args, root, group = project
    enroll(project)
    database.claim_glide_license_retry(args.db_path, 1, 0)
    task = database.glide_license_retries(args.db_path, 1)[0]
    folder = tmp_path/'attempt'
    native.prepare(dict(payload=task['payload'], output=str(folder)), structures())
    output = folder/'retry_lib.maegz';write_poses(output, [Pose(2, -7, index=1)])
    store = database.store_glide_license_retry_scores
    monkeypatch.setattr(database, 'store_glide_license_retry_scores', lambda *a: (_ for _ in ()).throw(OSError('DB unavailable')))
    with pytest.raises(OSError):
        retry._collect(args, 1, root, task, folder, output, lambda *a: None, None)
    assert database.glide_license_retries(args.db_path, 1)[0]['state'] == 'running'
    monkeypatch.setattr(database, 'store_glide_license_retry_scores', store)
    retry._collect(args, 1, root, task, folder, output, lambda *a: None, None)
    assert database.glide_license_retries(args.db_path, 1) == []
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT id,docking_score FROM compound WHERE id IN(1,2) ORDER BY id').fetchall() == [(1, -5), (2, -7)]


def test_repeated_license_failure_moves_behind_untried_work(project):
    args, root, group = project
    archive(group/'glide_10_subjobs.tar.gz', {
        'glide_10-0001.log': LICENSE,
        'glide_10-0001.inp': 'LIGAND_START 2\nLIGAND_END 3\nLIGAND_OFFSET 0\n',
        'glide_10-0002.log': LICENSE,
        'glide_10-0002.inp': 'LIGAND_START 4\nLIGAND_END 4\nLIGAND_OFFSET 0\n'})
    retry.inspect_downloaded(args, 1, root)
    first = database.claim_glide_license_retry(args.db_path, 1, 0)
    database.finish_glide_license_retry(args.db_path, first, license_error=True, now=0)
    second = database.claim_glide_license_retry(args.db_path, 1, 100)
    assert second != first


def test_success_after_temporary_license_warning_does_not_redock(project, monkeypatch):
    args, root, _ = project
    task = enroll(project)
    database.claim_glide_license_retry(args.db_path, 1, 0)
    folder = root/'license-retries'/f"task_{task['id']}"/'attempt_1';folder.mkdir(parents=True)
    inp = folder/'glide_license_retry.in';inp.write_text('saved retry')
    save_record(folder, dict(job_id='retry-native', downloaded=True, command=['/suite/glide', str(inp)]))
    (folder/'glide_license_retry.log').write_text(LICENSE+'Later native retry succeeded.\n')
    write_poses(folder/'glide_license_retry_lib.maegz', [Pose(2, -7, index=1)])
    monkeypatch.setattr(retry, 'group_status', lambda *a, **kw: 'DONE')
    calls = []
    monkeypatch.setattr(retry, '_collect', lambda *a: calls.append(True))
    retry.run_tail(args, 1, root, None, None)
    assert calls == [True]
    assert database.glide_license_retries(args.db_path, 1)[0]['state'] == 'running'


def test_backfill_reserves_live_parent_capacity_and_waits_when_full(project, monkeypatch):
    args, root, group = project
    enroll(project)
    record = load_record(group);record['downloaded'] = False;save_record(group, record)
    monkeypatch.setattr(retry, 'job_details', lambda *a: [dict(jobId='original', status='RUNNING')])
    args.glide_cpus = 16
    assert not retry._available_retry_host(args, 1, root)
    assert database.glide_license_retries(args.db_path, 1)[0]['state'] == 'pending'
    args.glide_cpus = 17
    assert retry._available_retry_host(args, 1, root) == 'slurm-cpu:1'
    # Empty native child lists cannot release a parent's saved 16-slot request.
    monkeypatch.setattr(retry, 'job_details', lambda *a: [dict(jobId='original', status='WAITING')])
    args.glide_cpus = 16
    assert not retry._available_retry_host(args, 1, root)


def test_backfill_preserves_original_priority_and_uncertainty(project, monkeypatch):
    args, root, group = project
    enroll(project)
    record = load_record(group);record.update(downloaded=False, job_id=None);save_record(group, record)
    assert not retry._available_retry_host(args, 1, root)
    record['job_id'] = 'original';save_record(group, record)
    from graft.schrodinger_guard import JobStatusUnavailable
    monkeypatch.setattr(retry, 'job_details', lambda *a: (_ for _ in ()).throw(JobStatusUnavailable('unavailable')))
    with pytest.raises(JobStatusUnavailable):
        retry._available_retry_host(args, 1, root)
    with database.connect(args.db_path) as conn:
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES(11,'REF2','C',0,'reference')")
        conn.execute("INSERT INTO compound(name,smiles,iteration,state,similar_to) VALUES('new','C',1,'glide_running',11)")
    monkeypatch.setattr(retry, 'job_details', lambda *a: pytest.fail('New original group must be submitted first'))
    assert not retry._available_retry_host(args, 1, root)


def test_backfill_selects_host_with_actual_vacancy(project, monkeypatch):
    args, root, group = project
    enroll(project)
    record = load_record(group);record['downloaded'] = False
    record['command'][-1] = 'first:2';save_record(group, record)
    args.glide_host = 'first:2 second:2';args.glide_cpus = None
    monkeypatch.setattr(retry, 'job_details', lambda *a: [dict(jobId='original', status='RUNNING')])
    assert retry._available_retry_host(args, 1, root) == 'second:1'


def test_bare_host_budget_keeps_conservative_terminal_fallback(project, monkeypatch):
    args, root, group = project
    enroll(project)
    args.glide_cpus = None
    record = load_record(group);record['downloaded'] = False;save_record(group, record)
    assert not retry._available_retry_host(args, 1, root)
    record['downloaded'] = True;save_record(group, record)
    assert retry._available_retry_host(args, 1, root) == 'slurm-cpu:1'


def test_verified_terminal_tree_releases_slots_without_importing_its_results(project, monkeypatch):
    args, root, group = project
    enroll(project)
    args.glide_cpus = 16
    record = load_record(group);record['downloaded'] = False;save_record(group, record)
    monkeypatch.setattr(retry, 'job_details', lambda *a: [dict(jobId='original', status='STOPPED')])
    assert retry._available_retry_host(args, 1, root) == 'slurm-cpu:1'
    assert not load_record(group)['downloaded']


def test_unmerged_timeout_is_pending_and_does_not_abort_other_queue_work(project, monkeypatch):
    from graft.stages import glide
    from graft.glide_recovery import GlideResultsPending
    args, root, group = project
    monkeypatch.setattr(glide, 'process_timeout_job', lambda *a: (_ for _ in ()).throw(GlideResultsPending('merge not finished')))
    assert glide.try_timeout_collection(1, args, dict(group_dir=group), None, root) is False
    # Actual scientific validation errors must still fail closed.
    monkeypatch.setattr(glide, 'process_timeout_job', lambda *a: (_ for _ in ()).throw(ValueError('invalid core mapping')))
    with pytest.raises(ValueError, match='core mapping'):
        glide.try_timeout_collection(1, args, dict(group_dir=group), None, root)
