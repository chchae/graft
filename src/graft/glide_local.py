"""Prime Glide transport; SQLite and scientific result handling stay in the worker.

A shared stage directory preserves native launch paths across SSH and restarts.
The pinned client routes only Glide and JobServer commands to Prime; preparation
and pose validation continue through the existing local installation.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import uuid

from graft.paths import stage_directory
from graft.md_gbsa_native import save


def backend_settings(suite):
    marker = Path(suite) / 'graft-glide-backend.json'
    return json.loads(marker.read_text()) if marker.is_file() else None


def remote_command(settings, program, arguments, cwd):
    from graft.config import SCHRODINGER_LICENSE_SERVERS
    arguments = [str(x) for x in arguments]
    if program == 'glide':
        arguments[0] = str(Path(arguments[0]).resolve())
    if program == 'jsc' and (not arguments or arguments[0] != 'download'):
        cwd = '/'
    command = ('export SCHRODINGER='+shlex.quote(settings['remote_suite'])+
               ' SCHRODINGER_LICENSE_SERVERS='+shlex.quote(SCHRODINGER_LICENSE_SERVERS)+
               '; cd '+shlex.quote(str(Path(cwd).resolve()))+' && exec '+
               shlex.join([settings['remote_suite']+'/'+program, *arguments]))
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', settings['host'], command]


def register(suite, *, replace=None):
    import fcntl
    import tempfile
    from graft.schrodinger_guard import local_glide_registry
    lockfile = Path(tempfile.gettempdir()) / f'graft-schrodinger-submit-{os.getuid()}.lock'
    with lockfile.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        path = local_glide_registry()
        original = json.loads(path.read_text()) if path.exists() else []
        saved = list(original)
        if replace is not None:
            saved = [item for item in saved if item != str(Path(replace).resolve())]
        value = str(Path(suite).resolve())
        if value not in saved:
            saved.append(value)
        if saved != original:
            path.parent.mkdir(parents=True, exist_ok=True)
            save(path, saved)


def _client(root, settings):
    suite = root / 'client'
    suite.mkdir(exist_ok=True)
    save(suite / 'graft-glide-backend.json', settings)
    local = Path(settings['local_suite'])
    for item in local.iterdir():
        target = suite / item.name
        if item.name not in {'glide', 'jsc', 'run'} and not target.exists() and not target.is_symlink():
            target.symlink_to(item)
    # Standalone scripts retain the backend even when glide_local is disabled.
    source = Path(__file__).resolve()
    for program in ('glide', 'jsc'):
        path = suite / program
        path.write_text('#!'+sys.executable+'\nimport os, runpy, sys\n'+
            'scope = runpy.run_path('+repr(str(source))+')\n'+
            'settings = scope["backend_settings"]('+repr(str(suite))+')\n'+
            'cmd = scope["remote_command"](settings, '+repr(program)+', sys.argv[1:], os.getcwd())\n'+
            'os.execvp(cmd[0], cmd)\n')
        path.chmod(0o700)
    run = suite / 'run'
    run.write_text('#!/bin/sh\nexec '+shlex.quote(str(local/'run'))+' "$@"\n')
    run.chmod(0o700)
    return suite


def configure_backend(args, iteration):
    """Select once per iteration; never migrate a saved native submission."""
    directory = stage_directory(args.output, iteration, 'glide')
    marker = directory / 'prime-backend.json'
    if directory.is_symlink() and 'graft-glide-' in str(directory.readlink()) and not marker.is_file():
        raise RuntimeError('Saved shared Prime Glide directory missing; no recreation/resubmission')
    if marker.is_file():
        settings = json.loads(marker.read_text())
        suite = Path(settings['client'])
        if backend_settings(suite) != settings['transport']:
            raise RuntimeError('Saved Prime Glide client missing or changed; retain native jobs')
    elif not getattr(args, 'glide_local', False):
        return
    elif directory.exists() and any(directory.iterdir()):
        # Even without a parent record, a frozen queue or uncertain launch must
        # keep its original execution location. Finish this iteration there.
        print('Glide: retaining existing iteration backend; glide_local applies to a fresh iteration.', flush=True)
        return
    else:
        root = Path(args.glide_local_work_dir) / ('graft-glide-'+uuid.uuid4().hex)
        root.mkdir(parents=True)
        token = uuid.uuid4().hex
        check = root/'shared-check'
        check.write_text(token)
        from graft.md_gbsa import ssh
        if ssh(args.glide_local_host, 'cat '+shlex.quote(str(check))).strip() != token:
            raise RuntimeError('Prime cannot see the same shared Glide directory')
        transport = dict(host=args.glide_local_host, remote_suite=args.glide_local_schrodinger,
                         local_suite=str(args.schrodinger), shared_root=str(root.resolve()),
                         remote_root=ssh(args.glide_local_host, 'cd '+shlex.quote(str(root))+' && pwd -P').strip())
        suite = _client(root, transport)
        # Query only; incompatible or absent native localhost cannot create an intent.
        from graft.schrodinger_guard import verify_submission_host
        verify_submission_host(suite, 'localhost')
        grid = root/'grid.zip'
        shutil.copy2(args.grid, grid)
        shared = root/'06-glide'
        shared.mkdir()
        settings = dict(client=str(suite), transport=transport, grid=str(grid),
                        host='localhost', cpus=getattr(args, 'glide_cpus', None) or 1)
        save(shared/'prime-backend.json', settings)
        directory.parent.mkdir(parents=True, exist_ok=True)
        if directory.exists():
            directory.rmdir()  # Only an empty directory can be replaced.
        directory.symlink_to(shared, target_is_directory=True)
    register(suite)
    args.glide_local_iteration = iteration
    args.schrodinger = suite
    args.grid = Path(settings['grid'])
    args.glide_host = settings['host']
    args.glide_cpus = settings['cpus']
    args.glide_shared_queue = True
    # This is the Glide worker's own namespace, not the supervisor's settings.
    args.use_slurm = False
    print(f'Glide: Prime {settings["transport"]["host"]}, '
          f'HOST=localhost:{args.glide_cpus}, suite={settings["transport"]["remote_suite"]}.', flush=True)
