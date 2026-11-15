"""Charge deadlines stop SQM descendants and never score incomplete parameters."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from graft import amber_runner as worker


def test_charge_timeout_is_terminal_and_never_uses_partial_ligand(tmp_path, monkeypatch):
    folder = tmp_path / 'CMPID_1'
    folder.mkdir()
    (tmp_path / 'results').mkdir()
    calls = []
    def run(command, root, lock_fd, **options):
        calls.append(command[0])
        assert command[0] == 'antechamber'
        assert options == {'timeout': 600, 'kill_process_group': True}
        (root / 'ligand.mol2').write_text('incomplete charges')
        raise subprocess.TimeoutExpired(command, 600)
    monkeypatch.setattr(worker, 'run', run)
    monkeypatch.setattr(worker, 'restore_pose', lambda *a: pytest.fail('Incomplete charges used'))
    worker.calculate(tmp_path, {'id': 1, 'charge': 0}, 'bcc', 1, charge_timeout_s=600)
    result = json.loads((tmp_path / 'results/CMPID_1.json').read_text())
    assert 'Charge generation exceeded 600 seconds (TIMEOUT)' in result['error']
    assert 'score' not in result
    assert calls == ['antechamber']


def _process_exited(proc: Path) -> bool:
    try:
        state = proc.read_text().split()[2]
    except (FileNotFoundError, ProcessLookupError):
        return True  # Reaped between termination and the /proc read.
    return state in ('Z', 'X')


@pytest.mark.parametrize('state,expected', [('R', False), ('S', False), ('Z', True), ('X', True)])
def test_process_exit_probe_requires_a_terminal_state(tmp_path, monkeypatch, state, expected):
    monkeypatch.setattr(Path, 'read_text', lambda path: f'123 (test) {state}')
    assert _process_exited(tmp_path/'stat') is expected


@pytest.mark.parametrize('error', [FileNotFoundError(), ProcessLookupError()])
def test_process_exit_probe_accepts_a_reaped_process(tmp_path, monkeypatch, error):
    def read(path):
        raise error
    monkeypatch.setattr(Path, 'read_text', read)
    assert _process_exited(tmp_path/'stat')


def test_process_exit_probe_does_not_hide_other_io_errors(tmp_path, monkeypatch):
    failure = PermissionError('unreadable process state')
    def read(path):
        raise failure
    monkeypatch.setattr(Path, 'read_text', read)
    with pytest.raises(PermissionError) as caught:
        _process_exited(tmp_path/'stat')
    assert caught.value is failure


def test_deadline_kills_parent_and_sqm_like_child_with_inherited_lock(tmp_path):
    # Real Python processes verify process-tree termination; no Amber execution.
    child = "import os,time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); time.sleep(60)"
    parent = "import os,subprocess,sys,time; from pathlib import Path; Path('parent.pid').write_text(str(os.getpid())); subprocess.Popen([sys.executable,'-c',sys.argv[1]],pass_fds=(int(sys.argv[2]),)); time.sleep(60)"
    with (tmp_path / 'run.lock').open('a') as lock:
        command = [sys.executable, '-c', parent, child, str(lock.fileno())]
        with pytest.raises(subprocess.TimeoutExpired):
            worker.run(command, tmp_path, lock.fileno(), timeout=2, kill_process_group=True)
    for name in ('parent', 'child'):
        pid = int((tmp_path / f'{name}.pid').read_text())
        proc = Path(f'/proc/{pid}/stat')
        # A killed child may briefly remain as an init-owned zombie.
        assert _process_exited(proc)
    import fcntl
    with (tmp_path / 'run.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize('code', [0, 7])
def test_charge_process_completion_checks_exit_code(tmp_path, code):
    with (tmp_path / 'run.lock').open('a') as lock:
        command = [sys.executable, '-c', f'raise SystemExit({code})']
        if code:
            with pytest.raises(subprocess.CalledProcessError):
                worker.run(command, tmp_path, lock.fileno(), timeout=2, kill_process_group=True)
        else:
            worker.run(command, tmp_path, lock.fileno(), timeout=2, kill_process_group=True)


@pytest.mark.parametrize('value', [True, None, 0, -1, '600', float('nan'), float('inf')])
def test_invalid_charge_deadline_is_rejected_before_calculation(value):
    with pytest.raises(RuntimeError, match='charge_timeout_s must be positive finite seconds'):
        worker.validate_protocol({'charge_timeout_s': value})
