"""Read-only REINVENT retirement check; executed through system Python over SSH."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def active_processes(root):
    # Include descendants that retain their launch cwd after a lost SSH session.
    # A process-table access failure is uncertainty, not evidence of termination.
    ancestors = set()
    pid = os.getpid()
    while pid > 1:
        ancestors.add(pid)
        pid = int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[1])
    active = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) in ancestors:
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            command = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            try:
                cwd = Path(os.readlink(proc / 'cwd'))
            except PermissionError:
                # Known credential/session daemons are non-dumpable. They do
                # not run the calculation; their children are scanned separately.
                comm = (proc / 'comm').read_text().strip()
                argv0 = command.split()[0] if command.split() else ''
                services = {'(sd-pam)': '(sd-pam)', 'ssh-agent': 'ssh-agent',
                            'gpg-agent': 'gpg-agent', 'gnome-keyring-d': 'gnome-keyring-daemon',
                            'sshd-session': 'sshd-session:'}
                if str(root) not in command and services.get(comm) == Path(argv0).name:
                    continue
                raise
            if cwd.is_relative_to(root) or str(root) in command:
                active.append(int(proc.name))
        except (FileNotFoundError, ProcessLookupError):
            continue  # Process exited while /proc was being read.
    return active


def snapshot(request):
    root = Path(request['root']).resolve(strict=True)
    config = root / request['config_name']
    if hashlib.sha256(config.read_bytes()).hexdigest() != request['config_sha256']:
        raise RuntimeError('Remote REINVENT configuration changed')
    active = active_processes(root)
    if active:
        return dict(terminal=False, reason='Remote REINVENT processes remain active', pids=active)
    receipt = request.get('receipt')
    if receipt is not None:
        return dict(terminal=True, evidence='ssh_exit_and_process_check', exit_code=receipt['exit_code'])
    # Legacy runs have no SSH-exit receipt. The installed REINVENT writes this
    # native terminal line after runner()/controlled shutdown, not in finally.
    # Require its exact config identity, final log line, output and no processes.
    log = (root / request['log_name']).read_text().splitlines()
    tail = next((line.strip() for line in reversed(log) if line.strip()), '')
    if not re.search(r'Finished REINVENT on \d{4}-\d{2}-\d{2}$', tail):
        return dict(terminal=False, reason='No native REINVENT terminal evidence')
    output = Path(request['output'])
    if not output.resolve().is_relative_to(root) or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError('Missing or foreign legacy REINVENT output')
    return dict(terminal=True, evidence='legacy_native_log_config_output_and_process_check',
                output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    print(json.dumps(snapshot(json.loads(sys.argv[1]))))
