"""Single-expansion integration; synthetic receptors/scores do not validate affinity."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from rdkit import Chem

from graft import _core, database, driver
from graft.states import CompoundState as State, GrowthTaskState as Growth
from graft.stages import generate, druglike, crem_generate
from graft.chemistry.combichem3d import history as provenance
from test_combichem_crem import project as growth_project


@pytest.fixture
def project(growth_project, monkeypatch):
    path=Path(growth_project['config-path'])
    s=path.read_text().replace('elite-count = 2\n','').replace('max-iteration = 2\n','')
    s='''generation-method = "crem"
host = "local"
dock-grid = "receptor.sdf"
target-count = 100
elite-count = 2
amber_gbsa = true
md-gbsa = true
prime-gbsa-input-count = 2
synthetic-feasibility-enabled = false
sa-accepted-ratio = 1.0
'''+s
    path.write_text(s)
    cfg=_core.load_project_toml(path)
    args=SimpleNamespace(**cfg,schrodinger=Path('/unused'))
    args.output.mkdir(exist_ok=True)
    with database.connect(args.db_path) as conn:
        _core.create_sqlite_schema(conn)
        ref=next(iter(Chem.SDMolSupplier(str(path.parent/'references.sdf'),removeHs=False)))
        conn.execute('INSERT INTO compound(name,smiles,iteration,state,docking_score,amber_gbsa_score,prime_gbsa_score) '
                     'VALUES (?,?,0,?,-10,-40,-50)',('reference',provenance.canonical_smiles(ref),State.PRIME_GBSA_DONE))
    monkeypatch.setattr(driver,'reference_work_pending',lambda args:False)
    monkeypatch.setattr(driver,'external_completion_reason',lambda *args:None)
    return args


def test_single_expansion_exhaustion_before_target_and_idempotent_restart(project,monkeypatch):
    args=project
    for name in ('run_libinvent_sampling','run_libinvent_elite_transfer_learning'):
        monkeypatch.setattr(_core,name,lambda **kw:pytest.fail('REINVENT must not run'))
    generate._run_stage(args,1)
    saved=database.crem_generation(args.db_path,1)
    assert saved['state']==Growth.DONE and 0<saved['population_count']<args.target_count
    assert saved['stop_reason']=='exhausted'
    assert database.stage_completion_reason(args.db_path,1,'generate',args.target_count,2) is None
    assert database.uses_raw_generation(args.db_path,1)
    folder=Path(args.output)/'iter01/02-generate/crem'
    assert not list(folder.glob('cycle02.*'))
    poses=database.growth_rows(folder/'combichem3d.sqlite','combichem3d_pose')
    assert {p['iteration'] for p in poses}=={0,1}
    with database.connect(args.db_path) as conn:
        rows=conn.execute('SELECT id,name,state FROM compound WHERE iteration=1').fetchall()
        assert all(name==f'CMPID_{cid}' and state==State.GENERATED for cid,name,state in rows)
        assert conn.execute('SELECT count(*) FROM crem_compound_pose').fetchone()[0]==len(rows)
        before=list(conn.execute('SELECT * FROM compound ORDER BY id'))
    generate._run_stage(args,1)
    with database.connect(args.db_path) as conn:
        assert list(conn.execute('SELECT * FROM compound ORDER BY id'))==before
    assert 'generation' not in (database.upstream_completion_reason(args.db_path,1,args.target_count,'ligprep') or '')
    druglike._run_stage(args,1)
    assert database.druglike_completion_reason(args.db_path,1) is None
    assert database.upstream_completion_reason(args.db_path,1,args.target_count,'ligprep') is None
    database.validate_generation_population(args.db_path,1,args.target_count)
    with pytest.raises(RuntimeError,match='target-count'):
        database.validate_generation_population(args.db_path,1,args.target_count+1)


def _score_generation(args, iteration):
    # Artificial values exercise real database ranking/claim/completion paths.
    with database.connect(args.db_path) as conn:
        conn.execute('UPDATE compound SET state=?,docking_score=-10,amber_gbsa_score=-40,prime_gbsa_score=-50 '
                     'WHERE iteration=? AND state=?',(State.PRIME_GBSA_DONE,iteration,State.GENERATED))
    selected=database.freeze_md_gbsa_selection(args.db_path,iteration,dict(protocol=dict(snapshots=1),replicates=1),
                                            count=2,target_count=args.target_count,prime_count=2,amber_keep_count=500)
    for index,cid in enumerate(selected['ids']):
        score=-70-index
        database.store_md_gbsa_result(args.db_path,iteration,cid,
                                     dict(id=cid,mean=score,std=0.,snapshots=[dict(frame=0,score=score)]))
    return selected['ids']


def test_next_iteration_uses_md_ranked_elites_and_only_one_addition(project):
    args=project
    generate._run_stage(args,1)
    druglike._run_stage(args,1)
    assert crem_generate._ready(args,2) is not None
    ids=_score_generation(args,1)
    expected=list(reversed(ids))
    assert [r['compound_id'] for r in database.crem_parent_poses(args.db_path,1,args.elite_count)]==expected
    generate._run_stage(args,2)
    saved=database.crem_generation(args.db_path,2)
    assert saved['state']==Growth.DONE
    assert [r['compound_id'] for r in saved['plan']['parents']]==expected
    scratch=Path(args.output)/'iter02/02-generate/crem/combichem3d.sqlite'
    poses=database.growth_rows(scratch,'combichem3d_pose')
    assert {p['iteration'] for p in poses}<={1,2}
    histories={p['id']:json.loads(p['history']) for p in poses}
    for row in poses:
        if row['generated']:
            h=histories[row['id']];before=histories[row['parent_pose']]
            assert h['core']==before['core']
            assert sum(h['growth_counts'].values())==sum(before['growth_counts'].values())+1
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT count(*)-count(DISTINCT smiles) FROM compound').fetchone()[0]==0


def test_target_caps_raw_candidates_and_keeps_downstream_gates(project):
    args=project;args.target_count=2
    generate._run_stage(args,1)
    saved=database.crem_generation(args.db_path,1)
    assert saved['population_count']==2 and saved['stop_reason']=='target_count'
    assert database.stage_completion_reason(args.db_path,1,'ligprep',2,2) is not None
    druglike._run_stage(args,1)
    assert len(database.druglike_report(args.db_path,1))==2
    assert database.docking_completion_reason(args.db_path,1,2) is not None
    _score_generation(args,1)
    assert database.docking_completion_reason(args.db_path,1,2) is None
    assert database.amber_completion_reason(args.db_path,1,2) is None
    assert database.stage_completion_reason(args.db_path,1,'prime_gbsa',2,2) is None
    assert database.md_gbsa_completion_reason(args.db_path,1) is None


def test_iteration_zero_stages_unchanged_and_legacy_defaults_remain(project):
    stages=[name for name,_ in driver.iteration_stages(project,0)]
    assert stages==['amber-gbsa','prime_gbsa','md-gbsa']
    names=[name for name,_ in driver.iteration_stages(project,1)]
    assert names==['generate','druglike','ligprep','glide','amber-gbsa','prime_gbsa','md-gbsa']


def test_deleted_frozen_crem_population_is_not_silently_regenerated(project):
    args=project
    generate._run_stage(args,1)
    with database.connect(args.db_path) as conn:
        conn.execute('DELETE FROM crem_compound_pose')
        conn.execute('DELETE FROM compound WHERE iteration=1')
    with pytest.raises(RuntimeError,match='population changed'):
        generate._run_stage(args,1)


def test_explicit_deleted_crem_reconciliation_backs_up_and_retains_previous_results(project, tmp_path):
    generate._run_stage(project, 1)
    druglike._run_stage(project, 1)
    before = database.crem_generation(project.db_path, 1)
    with sqlite3.connect(project.db_path) as conn:
        earlier = conn.execute('SELECT * FROM compound WHERE iteration=0').fetchall()
        conn.execute('DELETE FROM compound WHERE iteration=1')  # simulate manual deletion with FK checks off
    assert not database.iteration_was_deleted(project.db_path, 1)  # never an automatic reset
    backup = tmp_path/'before-explicit-reset.sqlite'
    database.reset_deleted_iteration(project.db_path, 1, backup, allow_deleted_crem=True)
    assert database.crem_generation(project.db_path, 1) is None
    with database.connect(project.db_path) as conn:
        assert conn.execute('SELECT * FROM compound WHERE iteration=0').fetchall() == earlier
        assert conn.execute('SELECT COUNT(*) FROM crem_compound_pose WHERE iteration=1').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM iteration_druglike_priority WHERE iteration=1').fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert database.crem_generation(backup, 1) == before
    with sqlite3.connect(backup) as conn:
        assert conn.execute('SELECT COUNT(*) FROM crem_compound_pose').fetchone()[0] == before['population_count']
        assert conn.execute('SELECT COUNT(*) FROM iteration_druglike_priority WHERE iteration=1').fetchone()[0] == 1


def test_explicit_existing_crem_reset_backs_up_rows_scores_and_keeps_earlier_work(project, tmp_path):
    generate._run_stage(project, 1)
    druglike._run_stage(project, 1)
    with database.connect(project.db_path) as conn:
        conn.execute("UPDATE compound SET docking_score=-10,state='amber_running' WHERE iteration=1")
        old = conn.execute('SELECT * FROM compound WHERE iteration=1 ORDER BY id').fetchall()
        earlier = conn.execute('SELECT * FROM compound WHERE iteration=0 ORDER BY id').fetchall()
        sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='compound'").fetchone()
    backup = tmp_path/'before-existing-reset.sqlite'
    # Caller has archived files, holds all stage locks and verified retirement.
    database.reset_deleted_iteration(project.db_path, 1, backup,
                                     allow_deleted_crem=True, delete_existing_crem=True)
    with database.connect(project.db_path) as conn:
        assert conn.execute('SELECT * FROM compound WHERE iteration=0 ORDER BY id').fetchall() == earlier
        assert conn.execute('SELECT COUNT(*) FROM compound WHERE iteration=1').fetchone()[0] == 0
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name='compound'").fetchone() == sequence
        assert conn.execute('SELECT COUNT(*) FROM crem_compound_pose WHERE iteration=1').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM iteration_druglike_priority WHERE iteration=1').fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert database.crem_generation(project.db_path, 1) is None
    assert driver.choose_iteration(project) == 1
    with sqlite3.connect(backup) as conn:
        assert conn.execute('SELECT * FROM compound WHERE iteration=1 ORDER BY id').fetchall() == old


@pytest.mark.parametrize('problem', ['no_authorization', 'later_compound', 'later_workspace',
                                    'md_selection', 'bad_provenance', 'earlier_reference'])
def test_existing_crem_reset_refuses_unsafe_or_implicit_deletion(project, tmp_path, problem):
    generate._run_stage(project, 1)
    druglike._run_stage(project, 1)
    with database.connect(project.db_path) as conn:
        cid = conn.execute('SELECT MIN(id) FROM compound WHERE iteration=1').fetchone()[0]
        if problem == 'later_compound':
            conn.execute("INSERT INTO compound(name,smiles,iteration,state) VALUES ('later','CC',2,'generated')")
        elif problem == 'later_workspace':
            database._mark_iteration_workspace_prepared(conn, 2)
        elif problem == 'md_selection':
            conn.execute("INSERT INTO iteration_md_gbsa_selection VALUES (1,'[]','{}')")
        elif problem == 'bad_provenance':
            conn.execute('DELETE FROM crem_compound_pose WHERE compound_id=?', (cid,))
        elif problem == 'earlier_reference':
            conn.execute('UPDATE compound SET similar_to=? WHERE iteration=0', (cid,))
        old = conn.execute('SELECT * FROM compound ORDER BY id').fetchall()
    backup = tmp_path/'must-not-reset-existing.sqlite'
    with pytest.raises((RuntimeError, ValueError)):
        database.reset_deleted_iteration(project.db_path, 1, backup,
            allow_deleted_crem=problem != 'no_authorization', delete_existing_crem=True)
    with database.connect(project.db_path) as conn:
        assert conn.execute('SELECT * FROM compound ORDER BY id').fetchall() == old
    assert not backup.exists()


def test_existing_crem_reset_rolls_back_all_metadata_on_delete_failure(project, tmp_path):
    generate._run_stage(project, 1)
    druglike._run_stage(project, 1)
    with database.connect(project.db_path) as conn:
        old = conn.execute('SELECT * FROM compound ORDER BY id').fetchall()
        count = conn.execute('SELECT COUNT(*) FROM crem_compound_pose').fetchone()[0]
        conn.execute("CREATE TRIGGER fail_reset BEFORE DELETE ON compound WHEN OLD.iteration=1 "
                     "BEGIN SELECT RAISE(ABORT, 'injected deletion failure'); END")
    backup = tmp_path/'before-rolled-back-reset.sqlite'
    with pytest.raises(sqlite3.IntegrityError, match='injected deletion failure'):
        database.reset_deleted_iteration(project.db_path, 1, backup,
                                         allow_deleted_crem=True, delete_existing_crem=True)
    with database.connect(project.db_path) as conn:
        assert conn.execute('SELECT * FROM compound ORDER BY id').fetchall() == old
        assert conn.execute('SELECT COUNT(*) FROM crem_compound_pose').fetchone()[0] == count
    assert database.crem_generation(project.db_path, 1) is not None
    assert backup.exists()


@pytest.mark.parametrize('problem', ['nonempty', 'partial_provenance', 'md_work', 'later_plan'])
def test_explicit_deleted_crem_reconciliation_refuses_uncertain_state(project, tmp_path, problem):
    generate._run_stage(project, 1)
    before = database.crem_generation(project.db_path, 1)
    with sqlite3.connect(project.db_path) as conn:
        if problem != 'nonempty':
            conn.execute('DELETE FROM compound WHERE iteration=1')
        if problem == 'partial_provenance':
            conn.execute('DELETE FROM crem_compound_pose WHERE compound_id=(SELECT MIN(compound_id) FROM crem_compound_pose)')
        if problem == 'md_work':
            conn.execute("INSERT INTO iteration_md_gbsa_selection VALUES (1,'[]','{}')")
        if problem == 'later_plan':
            conn.execute("INSERT INTO iteration_crem VALUES (2,'{}','running',NULL,NULL)")
    backup = tmp_path/'must-not-reset.sqlite'
    with pytest.raises(RuntimeError):
        database.reset_deleted_iteration(project.db_path, 1, backup, allow_deleted_crem=True)
    assert database.crem_generation(project.db_path, 1) == before
    assert not backup.exists()


def test_explicit_all_deleted_crem_recovery_preserves_reference_and_archives_md(project, tmp_path):
    generate._run_stage(project, 1)
    druglike._run_stage(project, 1)
    with sqlite3.connect(project.db_path) as conn:
        earlier = conn.execute('SELECT * FROM compound WHERE iteration=0').fetchall()
        cid = conn.execute('SELECT MIN(id) FROM compound WHERE iteration=1').fetchone()[0]
        conn.execute('INSERT INTO compound_md_gbsa_result VALUES (?,?)', (cid, json.dumps({'id':cid, 'mean':-42.})))
        conn.execute('INSERT INTO iteration_md_gbsa_selection VALUES (1,?,?)', (json.dumps([cid]), '{}'))
        conn.execute("INSERT INTO iteration_crem VALUES (2,'{}','done',1,'exhausted')")
        conn.execute('INSERT INTO crem_compound_pose SELECT 100000,2,molblock,history,properties '
                     'FROM crem_compound_pose LIMIT 1')
        conn.execute('DELETE FROM compound WHERE iteration>0')
    backup = tmp_path/'all-deleted-backup.sqlite'
    assert database.reset_deleted_crem_iterations(project.db_path, [1,2], backup) == [1,2]
    with database.connect(project.db_path) as conn:
        assert conn.execute('SELECT * FROM compound WHERE iteration=0').fetchall() == earlier
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('SELECT * FROM iteration_crem').fetchall() == []
        assert conn.execute('SELECT * FROM iteration_md_gbsa_selection WHERE iteration>0').fetchall() == []
        assert conn.execute('SELECT * FROM compound_md_gbsa_result WHERE compound_id=?', (cid,)).fetchall() == []
    with sqlite3.connect(backup) as conn:
        assert conn.execute('SELECT COUNT(*) FROM iteration_crem').fetchone()[0] == 2
        assert conn.execute('SELECT result FROM compound_md_gbsa_result WHERE compound_id=?', (cid,)).fetchone()


@pytest.mark.parametrize('problem', ['nonempty','missing_pose','unresolved_md','wrong_members','wrong_requested','later_metadata','unknown_orphan'])
def test_explicit_all_deleted_crem_recovery_refuses_inconsistent_or_uncertain_records(project, tmp_path, problem):
    generate._run_stage(project, 1)
    druglike._run_stage(project, 1)
    before = database.crem_generation(project.db_path, 1)
    with sqlite3.connect(project.db_path) as conn:
        cid = conn.execute('SELECT MIN(id) FROM compound WHERE iteration=1').fetchone()[0]
        if problem != 'nonempty':
            conn.execute('DELETE FROM compound WHERE iteration>0')
        if problem == 'missing_pose':
            conn.execute('DELETE FROM crem_compound_pose WHERE compound_id=?', (cid,))
        if problem == 'unresolved_md':
            conn.execute('INSERT INTO iteration_md_gbsa_selection VALUES (1,?,?)', (json.dumps([cid]), '{}'))
        if problem == 'wrong_members':
            conn.execute('DELETE FROM compound_druglike_selection WHERE compound_id=?', (cid,))
        if problem == 'later_metadata':
            conn.execute("INSERT INTO iteration_scaffold VALUES (2,'{}')")
        if problem == 'unknown_orphan':
            conn.execute("INSERT INTO compound_md_gbsa_result VALUES (100000,'{}')")
    backup = tmp_path/'blocked-backup.sqlite'
    with pytest.raises(RuntimeError):
        database.reset_deleted_crem_iterations(project.db_path, [1,2] if problem=='wrong_requested' else [1], backup)
    assert database.crem_generation(project.db_path, 1) == before
    assert backup.exists() == (problem == 'unknown_orphan')


def test_active_reference_blocks_generation(project,monkeypatch):
    monkeypatch.setattr(driver,'reference_work_pending',lambda args:True)
    generate._run_stage(project,1)
    assert database.crem_generation(project.db_path,1) is None


@pytest.mark.parametrize('setting', ['max-iteration = 2','elite-count = 10','elite-rank-by = "qed"','out-dir = "old-output"'])
def test_internal_cycles_are_rejected(project,setting):
    project.toml_file.write_text(project.toml_file.read_text()+'\n'+setting+'\n')
    with pytest.raises(ValueError,match='internal cycle'):
        _core.load_project_toml(project.toml_file)


def test_import_failure_rolls_back_and_resumes_without_recalculating(project,monkeypatch):
    args=project
    original=database.import_crem_results
    def interrupted(path, iteration, records, reason):
        def partial():
            yield next(records)
            raise RuntimeError('interrupted import')
        return original(path,iteration,partial(),reason)
    with monkeypatch.context() as patch:
        patch.setattr(database,'import_crem_results',interrupted)
        with pytest.raises(RuntimeError,match='interrupted import'):
            generate._run_stage(args,1)
    assert database.iteration_compound_count(args.db_path,1)==0
    assert database.crem_pending_iteration(args.db_path)==1
    assert driver.choose_iteration(args)==1
    assert database.stage_completion_reason(args.db_path,1,'generate',args.target_count,2) is not None
    monkeypatch.setattr(crem_generate.growth,'search',lambda *a,**kw:pytest.fail('completed geometry recalculated'))
    generate._run_stage(args,1)
    assert database.crem_generation(args.db_path,1)['state']==Growth.DONE
    assert database.iteration_compound_count(args.db_path,1)>0


def test_failed_worker_does_not_publish_completion(project,monkeypatch):
    args=project
    with monkeypatch.context() as patch:
        def failed(cfg, db, first):
            database.growth_start_session(db,cfg)
            database.growth_request_stop(db,'worker_error')
            database.growth_end_session(db,'worker_error')
        patch.setattr(crem_generate.growth,'supervise',failed)
        with pytest.raises(RuntimeError,match='worker_error'):
            generate._run_stage(args,1)
    assert database.crem_generation(args.db_path,1)['state']==Growth.RUNNING
    assert database.iteration_compound_count(args.db_path,1)==0
    generate._run_stage(args,1)
    assert database.crem_generation(args.db_path,1)['state']==Growth.DONE


def test_empty_search_finishes_without_spawning_more_pipeline_iterations(project,monkeypatch):
    from graft.chemistry.combichem3d import crem_growth
    monkeypatch.setattr(crem_growth,'candidate_sites',lambda *a:[])
    generate._run_stage(project,1)
    saved=database.crem_generation(project.db_path,1)
    assert saved['population_count']==0 and saved['state']==Growth.DONE
    assert driver.choose_iteration(project) is None
    assert driver.supervise_iteration(project,project.toml_file,1,driver.iteration_stages(project,1),1,driver.StopFlag())
    assert not database.iteration_was_deleted(project.db_path,1)


def test_parent_count_is_upper_bound_but_missing_selected_pose_stops(project):
    args=project
    generate._run_stage(args,1)
    druglike._run_stage(args,1)
    ids=_score_generation(args,1)
    assert len(database.crem_parent_poses(args.db_path,1,1000))==len(ids)
    with database.connect(args.db_path) as conn:
        conn.execute('DELETE FROM crem_compound_pose WHERE compound_id=?',(ids[-1],))
    with pytest.raises(RuntimeError,match='missing CReM pose'):
        database.crem_parent_poses(args.db_path,1,1)


def test_growth_rejects_parents_from_other_iterations(project):
    from graft.config import combichem_3d_settings
    from graft.chemistry.combichem3d import frames
    args=project
    cfg=combichem_3d_settings(args.toml_file)
    mol,h=frames.initial_frame(cfg['parents'],cfg['reference-mcs'],growth_mode='fixed-frame')
    parents=[dict(smiles=provenance.canonical_smiles(mol),molblock=Chem.MolToMolBlock(mol),history=h)]
    with pytest.raises(ValueError,match='preceding-iteration'):
        database.growth_initialize(args.output/'invalid.sqlite',cfg,'test',parents,external_iteration=2)
    assert not (args.output/'invalid.sqlite').exists()


def test_existing_population_is_rejected_before_generation_or_native_queries(project,monkeypatch):
    with database.connect(project.db_path) as conn:
        conn.execute('INSERT INTO compound(name,smiles,iteration,state) VALUES (?,?,1,?)',('old','C',State.GENERATED))
    monkeypatch.setattr(driver,'reference_work_pending',lambda *a:pytest.fail('existing population was not rejected early'))
    with pytest.raises(RuntimeError,match='Cannot convert'):
        generate._run_stage(project,1)
    assert database.crem_generation(project.db_path,1) is None


@pytest.mark.parametrize('change', ['conformers = 3', 'receptor-repulsion-strength = 100.0'])
def test_changed_scientific_settings_cannot_resume_frozen_generation(project,monkeypatch,change):
    with monkeypatch.context() as patch:
        def interrupted(*args):
            raise RuntimeError('before import')
        patch.setattr(database,'import_crem_results',interrupted)
        with pytest.raises(RuntimeError,match='before import'):
            generate._run_stage(project,1)
    before=database.crem_generation(project.db_path,1)
    project.toml_file.write_text(project.toml_file.read_text()+'\n'+change+'\n')
    with pytest.raises(RuntimeError,match='settings/parents changed'):
        generate._run_stage(project,1)
    assert database.crem_generation(project.db_path,1)==before


def test_glide_receives_fixed_dummy_free_reference_constraint(project):
    from graft.chemistry.scaffold import prepare_iteration_scaffold
    generate._run_stage(project,1)
    prepare_iteration_scaffold(project,1,include_generation=False)
    query=Chem.MolFromSmarts(project.mcs_smarts)
    assert query.GetNumAtoms()==6 and all(atom.GetAtomicNum()>0 for atom in query.GetAtoms())
    initial=database.iteration_scaffold(project.db_path,0)
    assert initial['mcs_smarts']==project.mcs_smarts


@pytest.mark.parametrize('entry', ['stage', 'cli'])
@pytest.mark.parametrize('flags', [[], ['--debug'], ['--trace']])
def test_standalone_command_rejects_pipeline_before_reset_or_dispatch(project,monkeypatch,capsys,entry,flags):
    from graft.stages import combichem_3d as standalone
    from graft import cli
    sentinel=Path(project.output)/'combichem-3d/preserved-result.txt'
    sentinel.parent.mkdir(exist_ok=True)
    sentinel.write_text('saved scientific results')
    def unexpected(*args,**kwargs):
        pytest.fail('Standalone preflight/reset/dispatch must not run for pipeline TOML')
    monkeypatch.setattr(standalone,'combichem_3d_settings',unexpected)
    monkeypatch.setattr(standalone,'reset_crem_output',unexpected)
    monkeypatch.setattr(standalone,'configure',unexpected)
    from graft.stages import combichem_remote
    monkeypatch.setattr(combichem_remote,'dispatch',unexpected)
    with pytest.raises(SystemExit) as exc:
        if entry=='stage':
            standalone._main([str(project.toml_file),*flags])
        else:
            cli.main(['combichem-3d',str(project.toml_file),*flags])
    assert exc.value.code==2
    assert 'graft run' in capsys.readouterr().err
    assert sentinel.read_text()=='saved scientific results'
    assert database.iteration_compound_count(project.db_path,0)==1
    assert database.crem_generation(project.db_path,1) is None


def test_standalone_export_only_keeps_pipeline_and_old_growth_results(project):
    from graft.stages import combichem_3d as standalone
    from graft.config import combichem_3d_settings
    cfg=combichem_3d_settings(project.toml_file)
    db,_=standalone.prepare_project(cfg)
    before=database.growth_rows(db,'combichem3d_pose')
    standalone._main([str(project.toml_file),'--export-only'])
    assert database.growth_rows(db,'combichem3d_pose')==before
    assert (Path(cfg['out-dir'])/'summary.json').exists()
    assert database.iteration_compound_count(project.db_path,0)==1
    assert database.crem_generation(project.db_path,1) is None
