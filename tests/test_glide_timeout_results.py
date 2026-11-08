"""Native outputs are synthetic; exercise selection, publication and recovery."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, database, driver, glide_recovery as recovery
from graft import glide_timeout_results as native
from graft.stages import glide


class Pose:
    def __init__(self, cid, score, tag):
        self.title = f'CMPID_{cid}'
        self.property = dict(r_i_docking_score=score, tag=tag)

    def copy(self):
        return copy.deepcopy(self)


def structure_module(inputs):
    def reader(path):
        for value in inputs[str(path)]:
            if isinstance(value, Exception):
                raise value
            yield value

    class Writer:
        def __init__(self, path):
            self.path, self.rows = Path(path), []
        def __enter__(self):
            return self
        def append(self, pose):
            self.rows.append(dict(title=pose.title, properties=pose.property))
        def __exit__(self, *args):
            self.path.write_text(json.dumps(self.rows))
    return SimpleNamespace(StructureReader=reader, StructureWriter=Writer)


def test_lowest_finite_score_keeps_matching_pose_and_readable_prefix(tmp_path):
    inputs = {'one': [Pose(2, -5, 'old'), Pose(3, -7, 'prefix'), OSError('truncated tail')],
              'two': [Pose(2, -9, 'best'), Pose(2, -9, 'tie'), Pose(4, float('nan'), 'bad'),
                      Pose(4, float('inf'), 'bad'), Pose(999, -100, 'foreign')]}
    report = native.collect(dict(inputs=list(inputs), ids=[2, 3, 4], output=str(tmp_path)), structure_module(inputs))
    rows = json.loads((tmp_path/'best_poses.maegz').read_text())
    assert [r['properties']['tag'] for r in rows] == ['best', 'prefix']
    assert [r['properties']['i_user_compound_id'] for r in rows] == [2, 3]
    assert report['scored'] == 2 and report['skipped_invalid_scores'] == 2
    assert report['read_errors'] == [dict(path='one', error='truncated tail')]


def test_timeout_download_is_job_scoped_and_never_reads_stale_group_output(tmp_path, monkeypatch):
    (tmp_path/'glide_lib.maegz').write_bytes(b'stale')
    recovery.save_record(tmp_path, dict(job_id='parent', timeout_requested_at=1, timeout_seconds=1800))
    jobs = [dict(jobId='parent', status='STOPPED'), dict(jobId='child', status='DONE')]
    monkeypatch.setattr(recovery, 'job_details', lambda *a: jobs)
    calls = []
    def download(command, **kwargs):
        calls.append(command[-1])
        if command[-1] == 'child':
            (kwargs['cwd']/'glide-0001_lib.maegz').write_bytes(b'fresh')
    monkeypatch.setattr(recovery.subprocess, 'run', download)
    files = recovery.timeout_pose_files('/suite', tmp_path)
    assert len(files) == 1 and files[0].read_bytes() == b'fresh'
    assert files[0].is_relative_to(tmp_path/'timeout-results/child')
    assert recovery.timeout_pose_files('/suite', tmp_path) == files
    assert calls == ['parent', 'child']


def test_timeout_download_waits_for_every_child_and_unknown_lookup(tmp_path, monkeypatch):
    recovery.save_record(tmp_path, dict(job_id='parent', timeout_requested_at=1))
    jobs = [dict(jobId='parent', status='STOPPED'), dict(jobId='child', status='RUNNING')]
    monkeypatch.setattr(recovery, 'job_details', lambda *a: jobs)
    monkeypatch.setattr(recovery.subprocess, 'run', lambda *a, **kw: pytest.fail('downloaded live work'))
    with pytest.raises(RuntimeError, match='terminate'):
        recovery.timeout_pose_files('/suite', tmp_path)
    jobs[1]['status'] = 'UNKNOWN'
    with pytest.raises(RuntimeError, match='terminate'):
        recovery.timeout_pose_files('/suite', tmp_path)


@pytest.fixture
def project(tmp_path):
    db = tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) VALUES (1,'REF','C',0,'reference')")
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state,similar_to,failed_stage,failure_message) "
                         "VALUES (?,?,'C',1,'failed',1,'glide','Glide job exceeded 1800s; parent and children terminated')",
                         [(i, f'CMPID_{i}') for i in [2, 3, 4]])
    group = tmp_path/'iter01/06-glide/ref_1'
    group.mkdir(parents=True)
    recovery.save_record(group, dict(job_id='parent', timeout_requested_at=1, timeout_seconds=1800))
    args = SimpleNamespace(db_path=db, output=tmp_path, schrodinger=Path('/suite'))
    job = dict(group_dir=group, reference_id=1, compounds=database.glide_timeout_rows(db, 1, 1))
    return args, job


def test_timeout_collection_blocks_downstream_until_pose_publication_and_commit(project, monkeypatch):
    args, job = project
    group = job['group_dir']
    source = group/'timeout-results/child/partial_lib.maegz'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'native partial output')
    (group/'best_poses.maegz').write_bytes(b'stale cached poses must not be read')
    monkeypatch.setattr(glide, 'timeout_pose_files', lambda *a: [source])
    def extract(command, **kwargs):
        request = json.loads(Path(command[-1]).read_text())
        assert request['inputs'] == [str(source.resolve())]
        native.collect(request, structure_module({str(source.resolve()): [Pose(2, -9, 'pose')]}))
    monkeypatch.setattr(_core, 'run_command', extract)
    def publish(args, root, merger):
        assert (group/'best_poses.maegz').exists()
        assert database.docking_completion_reason(args.db_path, 1, 3) == 'Glide timeout result collection pending'
        with database.connect(args.db_path) as conn:
            assert conn.execute('SELECT docking_score FROM compound WHERE id=2').fetchone() == (None,)
    monkeypatch.setattr(glide, 'merge_current_best', publish)
    glide.process_timeout_job(1, args, job, 'merger', group.parent)
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT docking_score,state FROM compound WHERE id=2').fetchone() == (-9, 'docked')
        assert conn.execute("SELECT COUNT(*) FROM compound WHERE iteration=1 AND state='failed'").fetchone()[0] == 2
    assert database.docking_completion_reason(args.db_path, 1, 3) is None
    assert recovery.load_record(group)['timeout_results_imported']
    # Lost group marker after commit: repair from SQLite without rereading outputs.
    record = recovery.load_record(group);record.pop('timeout_results_imported');recovery.save_record(group, record)
    monkeypatch.setattr(glide, 'timeout_pose_files', lambda *a: pytest.fail('repeated collection'))
    glide.process_timeout_job(1, args, job, 'merger', group.parent)
    assert recovery.load_record(group)['timeout_results_imported']


def test_zero_native_outputs_preserve_failed_unscored_compounds(project, monkeypatch):
    args, job = project
    monkeypatch.setattr(glide, 'timeout_pose_files', lambda *a: [])
    monkeypatch.setattr(_core, 'run_command', lambda *a, **kw: pytest.fail('invented a score'))
    glide.process_timeout_job(1, args, job, 'merger', job['group_dir'].parent)
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT COUNT(docking_score) FROM compound WHERE iteration=1').fetchone()[0] == 0
    assert recovery.load_record(job['group_dir'])['timeout_result_count'] == 0


def test_partial_import_refuses_foreign_ids_conflicts_and_started_downstream(project):
    args, _ = project
    database.begin_glide_timeout_collection(args.db_path, 1, 1, 'parent')
    with pytest.raises(ValueError, match='foreign'):
        database.validate_glide_timeout_scores(args.db_path, 1, 1, {999: -9})
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET docking_score=-8,state='docked' WHERE id=2")
    with pytest.raises(RuntimeError, match='conflicts'):
        database.validate_glide_timeout_scores(args.db_path, 1, 1, {2: -9})
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE compound SET amber_gbsa_score=-30 WHERE id=2')
    with pytest.raises(RuntimeError, match='downstream'):
        database.validate_glide_timeout_scores(args.db_path, 1, 1, {2: -8})


def test_saved_timeout_blocks_supervisor_until_collection_committed(project):
    args, job = project
    assert 'timeout results require recovery' in driver.external_completion_reason(args, 1, 'glide')


def test_legacy_failed_rows_still_block_gbsa_while_timeout_child_is_live(project, monkeypatch):
    args, job = project
    root = job['group_dir'].parent
    prepared = args.output/'iter01/05-ligprep/ligprep.maegz'
    prepared.parent.mkdir(parents=True)
    prepared.write_bytes(b'frozen prepared ligands')
    (root/'ligprep_input.sha256').write_text(hashlib.sha256(prepared.read_bytes()).hexdigest())
    monkeypatch.setattr(glide, 'group_status', lambda *a, **kw: 'RUNNING')
    glide.recover_timeout_groups(args, 1, root)
    assert database.docking_completion_reason(args.db_path, 1, 3) == 'Glide timeout result collection pending'
    with database.connect(args.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM compound WHERE iteration=1 AND state='failed'").fetchone()[0] == 3


@pytest.mark.parametrize('kind', ['failed_amber', 'fep_claim', 'exported_candidates'])
def test_partial_recovery_cannot_rerank_a_started_downstream_workflow(project, kind):
    args, _ = project
    with database.connect(args.db_path) as conn:
        if kind == 'failed_amber':
            conn.execute("UPDATE compound SET failed_stage='amber_gbsa' WHERE id=2")
        elif kind == 'fep_claim':
            conn.execute("UPDATE compound SET state='fep_running' WHERE id=2")
        else:
            conn.execute('CREATE TABLE IF NOT EXISTS iteration_fep_selection(iteration INTEGER PRIMARY KEY)')
            conn.execute('INSERT INTO iteration_fep_selection(iteration) VALUES (1)')
    with pytest.raises(RuntimeError, match='downstream'):
        database.begin_glide_timeout_collection(args.db_path, 1, 1, 'parent')
