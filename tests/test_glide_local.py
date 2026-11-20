"""Remote transport tests do not validate native docking energies."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import glide_local as local, schrodinger_guard as guard
from graft.config import glide_local_settings, load_project_toml
from graft.paths import stage_directory


@pytest.mark.parametrize('key,value', [('glide_local', 'true'), ('glide-local', 1),
    ('glide-local-host', '-prime'), ('glide-local-host', 'prime:4'),
    ('glide-local-work-dir', 'relative'), ('glide-local-schrodinger', 'relative')])
def test_invalid_config(key, value):
    with pytest.raises(ValueError, match='glide'):
        glide_local_settings({key: value})


def test_defaults_and_alias():
    assert glide_local_settings({})['glide_local'] is False
    config = glide_local_settings({'glide_local': True})
    assert config['glide_local_host'] == 'prime'
    assert config['glide_local_schrodinger'] == '/home/schrodinger/default'
    assert glide_local_settings({'glide-local': True}) == config
    with pytest.raises(ValueError, match='disagree'):
        glide_local_settings({'glide_local': True, 'glide-local': False})


@pytest.fixture
def project(tmp_path, monkeypatch):
    suite = tmp_path/'suite'
    suite.mkdir()
    (suite/'run').touch()
    grid = tmp_path/'input.zip'
    grid.write_bytes(b'grid unchanged')
    args = SimpleNamespace(output=tmp_path/'output', schrodinger=suite, grid=grid,
        glide_cpus=4, glide_host='slurm-cpu:128', use_slurm=True,
        **glide_local_settings({'glide_local': True, 'glide-local-work-dir': str(tmp_path/'share')}))
    from graft import md_gbsa
    def ssh(host, command):
        assert host == 'prime'
        import shlex
        tokens = shlex.split(command)
        if tokens[0] == 'cat':
            return Path(tokens[1]).read_text()
        return tokens[1]  # Remote canonical shared path.
    monkeypatch.setattr(md_gbsa, 'ssh', ssh)
    monkeypatch.setattr(guard, 'verify_submission_host', lambda suite, host: None)
    return args


def test_new_backend_has_shared_inputs_localhost_and_no_slurm(project):
    args = project
    grid = args.grid.read_bytes()
    local.configure_backend(args, 1)
    root = stage_directory(args.output, 1, 'glide')
    assert root.is_symlink()
    assert args.grid.read_bytes() == grid
    assert args.glide_host == 'localhost' and args.glide_cpus == 4
    assert args.glide_shared_queue and not args.use_slurm
    backend = local.backend_settings(args.schrodinger)
    assert backend['remote_suite'] == '/home/schrodinger/default'
    assert json.loads(guard.local_glide_registry().read_text()) == [str(args.schrodinger)]
    assert 'exec ' in (args.schrodinger/'run').read_text()


def test_restart_pins_backend_even_when_disabled_and_config_changes(project):
    args = project
    local.configure_backend(args, 1)
    suite, grid = args.schrodinger, args.grid
    args.glide_local = False
    args.glide_local_host = 'other'
    args.glide_cpus = 99
    local.configure_backend(args, 1)
    assert args.schrodinger == suite and args.grid == grid
    assert args.glide_cpus == 4
    assert local.backend_settings(suite)['host'] == 'prime'


def test_existing_uncertain_or_saved_slurm_work_is_never_moved(project):
    args = project
    directory = stage_directory(args.output, 1, 'glide')
    directory.mkdir(parents=True)
    record = directory/'glide_job.json'
    record.write_text('{"job_id": null}')
    suite = args.schrodinger
    local.configure_backend(args, 1)
    assert args.schrodinger == suite and args.use_slurm
    assert not directory.is_symlink() and record.read_text() == '{"job_id": null}'


def test_missing_saved_client_blocks_without_recreation(project):
    local.configure_backend(project, 1)
    (project.schrodinger/'graft-glide-backend.json').unlink()
    with pytest.raises(RuntimeError, match='missing or changed'):
        local.configure_backend(project, 1)


def test_shared_visibility_failure_precedes_link_claims_and_submission(project, monkeypatch):
    from graft import md_gbsa
    monkeypatch.setattr(md_gbsa, 'ssh', lambda *a: 'foreign bytes')
    with pytest.raises(RuntimeError, match='cannot see'):
        local.configure_backend(project, 1)
    assert not stage_directory(project.output, 1, 'glide').exists()
    assert not guard.local_glide_registry().exists()


def test_commands_quote_paths_keep_download_cwd_and_use_shared_cwd_for_queries(tmp_path):
    settings = dict(remote_suite='/home/schrodinger/default', host='prime', shared_root='/shared root')
    cmd = local.remote_command(settings, 'jsc', ['info', '--json', 'job;ignored'], tmp_path)
    assert cmd[-2] == 'prime'
    assert "cd /" in cmd[-1] and "'job;ignored'" in cmd[-1]
    download = local.remote_command(settings, 'jsc', ['download', '--cwd', 'job'], tmp_path)
    assert 'cd '+str(tmp_path) in download[-1]
    launch = local.remote_command(settings, 'glide', ['input.in', '-HOST', 'localhost:4'], tmp_path)
    assert '/glide ' in launch[-1] and 'localhost:4' in launch[-1]
    assert '-QARGS' not in launch[-1]


def test_fence_includes_remote_glide_when_prime_uses_regular_suite(project, monkeypatch):
    local.configure_backend(project, 1)
    wrapper = project.schrodinger
    calls = []
    def jobs(suite):
        calls.append(str(suite))
        return [dict(jobId='prime-glide', jobName='glide_01')] if Path(suite) == wrapper else []
    monkeypatch.setattr(guard, '_local_active_jobs', jobs)
    with pytest.raises(guard.SubmissionBlocked, match='prime-glide'):
        with guard.submission_guard(Path('/regular'), 'prime_gbsa'):
            pytest.fail('Prime submitted while remote Glide running')
    assert calls == ['/regular', str(wrapper)]


def test_fence_includes_regular_prime_when_glide_uses_remote_client(project, monkeypatch):
    local.configure_backend(project, 1)
    wrapper = project.schrodinger
    monkeypatch.setattr(guard, '_local_active_jobs', lambda suite:
        [] if Path(suite) == wrapper else [dict(jobId='slurm-prime', jobName='prime_mmgbsa')])
    with pytest.raises(guard.SubmissionBlocked, match='slurm-prime'):
        with guard.submission_guard(wrapper, 'glide'):
            pytest.fail('Glide submitted while regular Prime running')


def test_monitor_survives_output_move_and_keeps_cross_project_fence(project,monkeypatch):
    local.configure_backend(project,1)
    old = project.schrodinger
    settings = local.backend_settings(old)
    monitor = local.register_monitor(settings,replace=old)
    assert not monitor.is_relative_to(project.output)
    assert monitor.is_relative_to(guard.local_glide_registry().parent/'glide-monitors')
    project.output.rename(project.output.with_name('archived-output'))
    assert local.backend_settings(monitor) == settings
    assert json.loads(guard.local_glide_registry().read_text()) == [str(monitor)]
    assert local.register_monitor(settings) == monitor
    monkeypatch.setattr(guard,'_local_active_jobs',lambda suite:
        [dict(jobId='still-live-glide',jobName='glide_01')] if Path(suite)==monitor else [])
    with pytest.raises(guard.SubmissionBlocked,match='still-live-glide'):
        with guard.submission_guard(Path('/regular'),'prime_gbsa'):
            pytest.fail('Moving outputs must never bypass live-job exclusion')


def test_monitor_recreates_missing_wrapper_from_its_saved_transport(project):
    local.configure_backend(project,1)
    settings = local.backend_settings(project.schrodinger)
    monitor = local.register_monitor(settings,replace=project.schrodinger)
    (monitor/'jsc').unlink()
    assert local.register_monitor(settings)==monitor
    assert (monitor/'jsc').is_file()
    assert local.backend_settings(monitor)==settings


def test_unrecoverable_registered_client_still_blocks_and_identifies_path(tmp_path,monkeypatch):
    missing=tmp_path/'missing/client'
    guard.local_glide_registry().write_text(json.dumps([str(missing)]))
    monkeypatch.setattr(guard,'_local_active_jobs',lambda *a:[])
    with pytest.raises(guard.JobStatusUnavailable,match=str(missing)):
        guard.active_jobs(Path('/regular'))


def test_remote_shared_mount_alias_matches_native_launch_directory(project):
    local.configure_backend(project, 1)
    settings = local.backend_settings(project.schrodinger)
    settings['remote_root'] = '/remote/mount'
    (project.schrodinger/'graft-glide-backend.json').write_text(json.dumps(settings))
    job = dict(spec=dict(launchParams=dict(launchDirectory='/remote/mount/06-glide/ref_1')))
    assert guard.belongs_to_directory(job, Path(settings['shared_root'])/'06-glide/ref_1')
    assert not guard.belongs_to_directory(job, Path(settings['shared_root'])/'06-glide/ref_2')


def test_broken_shared_directory_is_not_recreated(project):
    local.configure_backend(project, 1)
    import shutil
    root = stage_directory(project.output, 1, 'glide')
    shutil.rmtree(root.resolve())
    project.glide_local = False
    with pytest.raises(RuntimeError, match='no recreation'):
        local.configure_backend(project, 1)
    assert root.is_symlink() and not root.exists()


def test_toml_load_exposes_requested_underscore_switch(tmp_path):
    toml = tmp_path/'project.toml'
    toml.write_text('''libinvent-prior = "prior.prior"
dock-grid = "grid.zip"
reference-pose = "references.maegz"
out-dir = "output"
sample-size = 10
target-count = 10
max-iteration = 1
host = "localhost"
synthetic-feasibility-enabled = false
use-slurm = true
glide_local = true
glide-cpus = 8
''')
    settings = load_project_toml(toml)
    assert settings['glide_local'] is True
    assert settings['glide_cpus'] == 8
    assert settings['ligprep_host'] == 'slurm-cpu'


def test_fence_queries_same_remote_server_once_across_iterations(project, monkeypatch):
    original_suite, original_grid = project.schrodinger, project.grid
    local.configure_backend(project, 1)
    first = project.schrodinger
    project.schrodinger, project.grid = original_suite, original_grid
    local.configure_backend(project, 2)
    # configure_backend is normally called on a freshly loaded worker namespace.
    # The server identity, rather than number of registered iterations, sets RPC count.
    calls = []
    monkeypatch.setattr(guard, '_local_active_jobs', lambda suite: calls.append(str(suite)) or [])
    assert guard.active_jobs(Path('/regular')) == []
    assert calls == ['/regular', str(first)]


@pytest.fixture
def completed(project, monkeypatch, tmp_path):
    from graft import _core, database, glide_local_cleanup as cleanup
    args = project
    args.db_path = tmp_path/'project.sqlite'
    args.target_count = 1
    with database.connect(args.db_path) as conn:
        _core.create_sqlite_schema(conn)
        conn.execute("INSERT INTO compound(name,smiles,iteration,state,docking_score) "
                     "VALUES ('CMPID_1','C',1,'docked',-10)")
    local.configure_backend(args, 1)
    directory = stage_directory(args.output, 1, 'glide')
    shared = directory.resolve().parent
    group = directory/'ref_1'
    group.mkdir()
    (group/'glide_job.json').write_text(json.dumps(dict(job_id='prime-1', downloaded=True)))
    (group/'docking_scores.tsv').write_text('id\tdocking_score\n1\t-10\n')
    (directory/'best_poses.maegz').write_bytes(b'verified matching pose')
    monkeypatch.setattr(guard, 'job_details', lambda *a: [dict(jobId='prime-1', status='DONE')])
    monkeypatch.setattr(guard, 'active_jobs', lambda *a: [])
    assert database.docking_completion_reason(args.db_path, 1, 1) is None
    return args, directory, shared


def test_cleanup_preserves_results_scores_logs_and_neighbors_before_removing_share(completed):
    from graft import database, glide_local_cleanup as cleanup
    args, directory, shared = completed
    neighbor = shared.parent/'unrelated-active-project'
    neighbor.mkdir()
    (neighbor/'job').write_bytes(b'keep')
    old_client = args.schrodinger
    cleanup.collect_and_cleanup(args)
    assert directory.is_dir() and not directory.is_symlink()
    assert (directory/'best_poses.maegz').read_bytes() == b'verified matching pose'
    assert (directory/'ref_1/glide_job.json').exists()
    assert not shared.exists() and neighbor.exists()
    assert cleanup.pending_iteration(args.output) is None
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT docking_score,state FROM compound').fetchone() == (-10, 'docked')
    receipt = json.loads(cleanup.receipt_path(directory).read_text())
    assert receipt['cleaned'] and receipt['native'][0]['status'] == 'DONE'
    clients = json.loads(guard.local_glide_registry().read_text())
    assert str(old_client) not in clients and len(clients) == 1
    assert local.backend_settings(clients[0])['host'] == 'prime'
    assert not Path(clients[0]).is_relative_to(args.output)
    assert cleanup.resume_cleanup(args, 1) is True
    args.output.rename(args.output.with_name('archived-output'))
    assert local.backend_settings(clients[0])['host'] == 'prime'


@pytest.mark.parametrize('status', ['RUNNING', 'WAITING', None])
def test_live_or_unknown_tree_retains_shared_results(completed, monkeypatch, status):
    from graft import glide_local_cleanup as cleanup
    args, directory, shared = completed
    monkeypatch.setattr(guard, 'job_details', lambda *a: [dict(jobId='prime-1', status=status)])
    cleanup.collect_and_cleanup(args)
    assert directory.is_symlink() and shared.exists()
    assert not cleanup.receipt_path(directory).exists()


def test_uncommitted_compounds_and_publication_barriers_keep_shared_data(completed):
    from graft import database, glide_local_cleanup as cleanup
    args, directory, shared = completed
    database.glide_shared_publication(args.db_path, 1, True)
    cleanup.collect_and_cleanup(args)
    assert shared.exists() and directory.is_symlink()
    database.glide_shared_publication(args.db_path, 1, False)
    with database.connect(args.db_path) as conn:
        conn.execute("UPDATE compound SET state='glide_running',docking_score=NULL")
    cleanup.collect_and_cleanup(args)
    assert shared.exists() and not cleanup.receipt_path(directory).exists()


def test_unknown_launch_intent_and_orphaned_native_job_prevent_cleanup(completed, monkeypatch):
    from graft import glide_local_cleanup as cleanup
    args, directory, shared = completed
    record = directory/'ref_1/glide_job.json'
    record.write_text('{"job_id": null}')
    cleanup.collect_and_cleanup(args)
    assert shared.exists()
    record.write_text('{"job_id": "prime-1"}')
    settings = local.backend_settings(args.schrodinger)
    launch = settings['remote_root']+'/06-glide/ref_2'
    monkeypatch.setattr(guard, 'active_jobs', lambda *a: [dict(spec=dict(launchParams=dict(launchDirectory=launch)))])
    cleanup.collect_and_cleanup(args)
    assert shared.exists() and not cleanup.receipt_path(directory).exists()


def test_corrupt_local_copy_never_deletes_share(completed, monkeypatch):
    from graft import glide_local_cleanup as cleanup
    args, directory, shared = completed
    copy = cleanup.shutil.copytree
    def corrupt(source, destination, *args, **kwargs):
        copy(source, destination, *args, **kwargs)
        if Path(source).name == '06-glide':
            (Path(destination)/'best_poses.maegz').write_bytes(b'corrupted')
    monkeypatch.setattr(cleanup.shutil, 'copytree', corrupt)
    with pytest.raises(RuntimeError, match='checksums differ'):
        cleanup.collect_and_cleanup(args)
    assert shared.exists() and directory.is_symlink()
    assert not cleanup.receipt_path(directory).exists()


def test_cleanup_replays_after_shared_retirement_without_native_relaunch(completed, monkeypatch):
    from graft import glide_local_cleanup as cleanup
    args, directory, shared = completed
    remove = cleanup.shutil.rmtree
    monkeypatch.setattr(cleanup.shutil, 'rmtree', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('interrupted delete')))
    with pytest.raises(RuntimeError, match='interrupted delete'):
        cleanup.collect_and_cleanup(args)
    assert not directory.is_symlink() and (directory/'best_poses.maegz').exists()
    assert not shared.exists() and shared.with_name(shared.name+'.collected').exists()
    monkeypatch.setattr(cleanup.shutil, 'rmtree', remove)
    local.register(args.schrodinger)  # A stale re-registration must be removed on replay.
    monkeypatch.setattr(guard, 'job_details', lambda *a: pytest.fail('queried or relaunched after retirement'))
    assert cleanup.resume_cleanup(args, 1)
    assert not shared.with_name(shared.name+'.collected').exists()
    assert str(args.schrodinger) not in json.loads(guard.local_glide_registry().read_text())


def test_publication_gap_recovers_from_durable_receipt(completed, monkeypatch):
    from graft import glide_local_cleanup as cleanup
    args, directory, shared = completed
    rename = Path.rename
    def interrupted(path, destination):
        if path.name.startswith('.06-glide.collect-'):
            raise RuntimeError('interrupted publish')
        return rename(path, destination)
    monkeypatch.setattr(Path, 'rename', interrupted)
    with pytest.raises(RuntimeError, match='interrupted publish'):
        cleanup.collect_and_cleanup(args)
    assert not directory.exists() and shared.exists()
    assert cleanup.pending_iteration(args.output) == 1
    monkeypatch.setattr(Path, 'rename', rename)
    assert cleanup.resume_cleanup(args, 1)
    assert (directory/'best_poses.maegz').exists() and not shared.exists()


def test_driver_keeps_cleanup_pending_even_after_compound_scores_commit(completed, monkeypatch):
    from graft import driver, glide_local_cleanup as cleanup
    args, directory, shared = completed
    assert cleanup.pending_cleanup(directory)
    assert 'cleanup' in driver.external_completion_reason(args, 1, 'glide')
    cleanup.collect_and_cleanup(args)
    monkeypatch.setattr(guard, 'job_details', lambda *a: pytest.fail('queried removed native client'))
    assert not cleanup.pending_cleanup(directory)
    assert driver.external_completion_reason(args, 1, 'glide') is None


def test_supervisor_reenters_glide_for_pending_cleanup_before_later_stage(completed, monkeypatch):
    from graft import database, driver
    args, directory, shared = completed
    monkeypatch.setattr(database, 'restart_calculation_stage', lambda *a: 'prime_gbsa')
    monkeypatch.setattr(database, 'running_calculation_stages', lambda *a: set())
    # Downloaded native identities alone must not hide pending shared cleanup.
    stages = [('generate', 'generation'), ('glide', 'glide-worker'), ('prime_gbsa', 'prime-worker')]
    assert driver.resume_iteration_stages(args, 1, stages) == stages[1:]
