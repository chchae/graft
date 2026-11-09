import sys
import threading

import pytest

from graft.driver import stream_process
from graft.stages._logging import WORK_STARTED, work_started


@pytest.mark.parametrize("active,failed", [(False, False), (True, False), (False, True), (True, True)])
def test_supervisor_summarizes_idle_work_and_preserves_errors(tmp_path, capsys, active, failed):
    script = (
        "import os, sys\n"
        "assert os.environ['GRAFT_SUPERVISED'] == '1'\n"
        "assert os.environ['PYTHONUNBUFFERED'] == '1'\n"
        "print('Using existing SQLite project database')\n"
        "print('MCS matched references: 23/24')\n"
    )
    if active:
        script += f"print({WORK_STARTED!r})\nprint('Starting LigPrep for 1000 compounds')\n"
    else:
        script += "print('No iteration requires LigPrep.')\n"
    if failed:
        script += "raise RuntimeError('calculation or initialization failed')\n"
    rc = stream_process("ligprep", [sys.executable, "-c", script], tmp_path, threading.Event())
    output = capsys.readouterr().out
    assert (rc != 0) == failed
    assert WORK_STARTED not in output
    if active:
        assert "Starting LigPrep for 1000 compounds" in output
        assert "MCS matched references" not in output
    elif not failed:
        assert output == "[ligprep ] skip: No iteration requires LigPrep.\n"
    else:
        assert "Using existing SQLite project database" in output
    if failed:
        assert "Traceback" in output
        assert "calculation or initialization failed" in output


def test_standalone_work_message_has_no_protocol_marker(monkeypatch, capsys):
    monkeypatch.delenv("GRAFT_SUPERVISED", raising=False)
    work_started("Starting prime_gbsa")
    assert capsys.readouterr().out == "Starting prime_gbsa\n"


@pytest.mark.parametrize('active', [False, True])
def test_mcs_summaries_survive_supervisor_setup_filter(tmp_path, capsys, active):
    script = "print('setup message')\n"
    script += "print('[MCS] Initial docking constraint: c1ccccc1')\n"
    script += "print('[MCS] Iteration 2 generation core (iteration 1 elites): Cc1ccccc1')\n"
    script += "print('[MCS] Iteration 2 generation scaffold SMILES: [*:0]c1ccc([*:1])cc1')\n"
    if active:
        script += f"print({WORK_STARTED!r})\nprint('Sampling')\n"
    else:
        script += "print('Generation already complete.')\n"
    assert stream_process('generate', [sys.executable, '-c', script], tmp_path,
                          threading.Event()) == 0
    output = capsys.readouterr().out
    assert output.count('[MCS] Initial docking constraint: c1ccccc1') == 1
    assert output.count('[MCS] Iteration 2 generation core (iteration 1 elites): Cc1ccccc1') == 1
    assert output.count('[MCS] Iteration 2 generation scaffold SMILES: [*:0]c1ccc([*:1])cc1') == 1
    assert 'setup message' not in output
    assert WORK_STARTED not in output
    assert ('skip: Generation already complete.' in output) == (not active)


@pytest.mark.parametrize("stage", ["generate", "synthetic", "ligprep", "glide", "prime_gbsa", "fep"])
@pytest.mark.parametrize("silent", [False, True])
def test_each_idle_stage_emits_one_skip_line(tmp_path, capsys, stage, silent):
    script = "pass" if silent else "print('setup message'); print('No eligible work.'); print()"
    assert stream_process(stage, [sys.executable, "-c", script], tmp_path, threading.Event()) == 0
    assert capsys.readouterr().out == f"[{stage:<8}] skip: No eligible work.\n"


def test_shutdown_does_not_report_skip(tmp_path, capsys):
    stop = threading.Event()
    stop.set()
    stream_process("glide", [sys.executable, "-c", "pass"], tmp_path, stop)
    assert "skip:" not in capsys.readouterr().out


def test_prime_gbsa_idle_poll_has_explicit_reason(tmp_path, monkeypatch, capsys):
    import sqlite3
    from types import SimpleNamespace
    from graft import _core
    from graft.stages import prime_gbsa
    db = tmp_path / "project.sqlite"
    with sqlite3.connect(db) as conn:
        _core.create_sqlite_schema(conn)
    args = SimpleNamespace(db_path=db, output=tmp_path, prime_gbsa_input_count=100)
    prime_gbsa._run_stage(args, None)
    assert capsys.readouterr().out == "No completed docking iteration requires prime_gbsa.\n"


def test_pending_stage_elapsed_preserves_original_clock_across_restart(tmp_path,monkeypatch):
    from graft import database
    path=tmp_path/'clock.sqlite'
    monkeypatch.setattr(database.time,'time',lambda:100.)
    assert database.stage_elapsed(path,60,'amber-gbsa') is None
    database.start_stage_timer(path,60,'amber-gbsa')
    monkeypatch.setattr(database.time,'time',lambda:700.)
    database.start_stage_timer(path,60,'amber-gbsa')  # Later driver/process invocation.
    assert database.stage_elapsed(path,60,'amber-gbsa')==600.
    assert database.finish_stage_timer(path,60,'amber-gbsa')==600.
    monkeypatch.setattr(database.time,'time',lambda:1000.)
    assert database.stage_elapsed(path,60,'amber-gbsa')==600.


@pytest.fixture
def verbosity():
    from graft.stages import _logging
    token = _logging.set_log_level(1)
    yield _logging.set_log_level
    _logging._log_level.reset(token)


@pytest.mark.parametrize('level',[1,2,3])
@pytest.mark.parametrize('supervised',[False,True])
def test_md_recovery_banner_is_verbose_but_keeps_work_protocol(monkeypatch,capsys,verbosity,level,supervised):
    from graft.stages import md_gbsa as stage
    verbosity(level)
    monkeypatch.setenv('GRAFT_SUPERVISED','1' if supervised else '0')
    monkeypatch.setattr(stage.md_gbsa,'run',lambda *a:None)
    stage._run_stage(object(),6)
    output=capsys.readouterr().out
    assert ('recovering or starting Desmond/Prime MD-GBSA' in output)==(level>=2)
    assert (WORK_STARTED in output)==supervised


@pytest.mark.parametrize('level',[1,2,3])
def test_md_compound_pending_is_verbose_but_blockers_remain_visible(capsys,verbosity,level):
    from graft.md_gbsa import report_pending
    verbosity(level)
    report_pending('MD-GBSA still pending for compound 114732')
    report_pending('Cannot verify external jobs; submissions withheld: network failure')
    output=capsys.readouterr().out
    assert ('still pending for compound' in output)==(level>=2)
    assert 'Cannot verify external jobs' in output


@pytest.mark.parametrize('level',[1,2])
@pytest.mark.parametrize('rc',[0,1])
def test_md_supervisor_pending_is_verbose_but_worker_failures_stay_visible(tmp_path,monkeypatch,capsys,verbosity,level,rc):
    from graft import driver,database
    from graft.stages import _summary
    from test_stage_timing import project
    args=project(tmp_path)
    verbosity(level)
    monkeypatch.setattr(driver,'md_stage_required',lambda *a:True)
    monkeypatch.setattr(driver,'stream_process',lambda *a:rc)
    monkeypatch.setattr(database,'stage_completion_reason',lambda *a:'MD-GBSA still pending for compound 114732')
    monkeypatch.setattr(_summary,'md_pending_summary',lambda *a:None)
    assert not driver.supervise_iteration(args,tmp_path/'p.toml',1,[('md-gbsa','module')],30,driver.StopFlag(),once=True)
    output=capsys.readouterr().out
    assert ('md-gbsa pending:' in output)==(level>=2 or rc!=0)
    if rc:assert 'worker exited with status 1' in output


@pytest.mark.parametrize('level', [1, 2, 3, 4])
def test_console_verbosity_thresholds(verbosity, capsys, level):
    from graft.stages._logging import detail, progress
    verbosity(level)
    work_started('stage started')
    progress('stage progress')
    detail('compound details')
    output = capsys.readouterr().out
    assert 'stage started' in output
    assert ('stage progress' in output) == (level >= 2)
    assert ('compound details' in output) == (level >= 3)


@pytest.mark.parametrize('level', [1, 2, 3, 4])
def test_prime_launch_checkpoints_and_keeps_logs_at_every_level(tmp_path, verbosity, capsys, level):
    from graft.schrodinger_retry import _run_attempt
    verbosity(level)
    seen = []
    code, output = _run_attempt(
        [sys.executable, '-c', "print('JobId: durable-id'); print('native detail')"],
        tmp_path, seen.append,
    )
    assert code == 0
    assert seen == ['JobId: durable-id\n', 'native detail\n']
    assert 'JobId: durable-id' in output
    assert 'native detail' in (tmp_path / 'prime_launch.log').read_text()
    assert ('native detail' in capsys.readouterr().out) == (level >= 3)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_helper_command_output_and_failures(tmp_path, verbosity, capfd, level):
    import subprocess
    from graft import _core
    verbosity(level)
    _core.run_command([sys.executable, '-c', "print('group calculation')"], cwd=tmp_path)
    output = capfd.readouterr().out
    assert ('group calculation' in output) == (level >= 3)
    if level < 3:
        assert 'group calculation' in (tmp_path / 'graft_commands.log').read_text()
    with pytest.raises(subprocess.CalledProcessError):
        _core.run_command([sys.executable, '-c', "import sys; print('failed calculation'); sys.exit(1)"], cwd=tmp_path)
    assert 'failed calculation' in capfd.readouterr().out


def test_helper_log_directory_does_not_change_command_working_directory(tmp_path, monkeypatch, verbosity):
    from graft import _core
    verbosity(1)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / 'output'
    _core.run_command([sys.executable, '-c', 'import os; print(os.getcwd())'], log_dir=output)
    assert not (tmp_path / 'graft_commands.log').exists()
    assert str(tmp_path) in (output / 'graft_commands.log').read_text()


@pytest.mark.parametrize('level', [1, 2, 3])
def test_amber_download_preserves_artifacts_and_filters_console(tmp_path, monkeypatch, verbosity, capsys, level):
    import io
    import tarfile
    from graft import amber_gbsa
    verbosity(level)
    folder = tmp_path / 'run-test'
    folder.mkdir()
    payload = b'ligand minimization detail\n'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        member = tarfile.TarInfo('CMPID_1/calculation.log')
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    monkeypatch.setattr(amber_gbsa, 'ssh', lambda *a, **kw: kw['stdout'].write(buffer.getvalue()))
    monkeypatch.setattr(amber_gbsa, 'environment_command', lambda settings, command: command)
    record = {'remote_dir': '/remote/run-test', 'run_id': 'run-test',
              'settings': {}, 'compound_ids': [1]}
    amber_gbsa.download_logs(tmp_path, record)
    assert (folder / 'CMPID_1/calculation.log').read_bytes() == payload
    assert ('ligand minimization detail' in capsys.readouterr().out) == (level >= 3)


def test_quiet_work_start_keeps_supervisor_protocol_and_queue_summary(tmp_path, capsys):
    script = "from graft.stages._logging import work_started\n"
    script += "work_started('recovering saved prime_gbsa job', level=2)\n"
    script += "print('Iteration 1: prime_gbsa running=186, saved=14, queue active=7, waiting=179, limit=7, scores updated=0.')\n"
    from pathlib import Path
    # The subprocess must import the current source checkout.
    root = Path(__file__).resolve().parents[1]
    script = f'import sys; sys.path.insert(0, {str(root / "src")!r})\n' + script
    assert stream_process('prime_gbsa', [sys.executable, '-c', script], root, threading.Event()) == 0
    output = capsys.readouterr().out
    assert output == ('[prime_gbsa] Iteration 1: prime_gbsa running=186, saved=14, queue active=7, '
                      'waiting=179, limit=7, scores updated=0.\n')
