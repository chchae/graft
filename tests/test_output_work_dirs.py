"""Slurm work lives under each TOML's output; explicit remote paths stay remote."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, amber_gbsa, amber_slurm, md_gbsa
from graft.config import amber_settings, md_gbsa_settings
from graft.paths import stage_directory


PROJECT = '''
sidechain-library = "fragments.smi"
dock-grid = "grid.zip"
reference-pose = "reference.maegz"
out-dir = "results/project output"
target-count = 10
sample-size = 10
max-iteration = 1
host = "localhost"
synthetic-feasibility-enabled = false
use-slurm = true
amber_gbsa = true
md-gbsa = true
'''


def test_defaults_follow_toml_output_and_new_run_records(tmp_path, monkeypatch):
    projects = []
    for name in ('target-a', 'target-b'):
        base = tmp_path / name
        base.mkdir()
        toml = base / 'project.toml'
        toml.write_text(PROJECT)
        cfg = _core.load_project_toml(toml)
        output = base / 'results/project output'
        assert cfg['amber_remote']['remote_work_dir'] == str(output / 'graft-amber-slurm-runs')
        assert cfg['md_gbsa_remote_work_dir'] == str(output / 'graft-md-gbsa-runs')
        args = SimpleNamespace(**cfg)
        directory = stage_directory(output, 1, 'amber_gbsa')
        directory.mkdir(parents=True)
        monkeypatch.setattr(amber_gbsa, 'source_fingerprints', lambda *a: {})
        record = amber_slurm.new_record(args, 1, directory, [1])
        assert Path(record['remote_dir']).parent == output / 'graft-amber-slurm-runs'
        prime = stage_directory(output, 1, 'prime_gbsa')
        prime.mkdir(parents=True)
        (prime / 'prime_gbsa_final.maegz').write_bytes(b'final optimized complex')
        saved = md_gbsa.settings_for(args, 1)
        assert Path(saved['root']).parent == output / 'graft-md-gbsa-runs'
        assert saved['helper'] == saved['root'] + '/native.py'
        projects.append(cfg)
    assert projects[0]['md_gbsa_remote_work_dir'] != projects[1]['md_gbsa_remote_work_dir']
    # Invocation directory must never override the TOML's directory.
    monkeypatch.chdir(tmp_path / 'target-b')
    cfg = _core.load_project_toml(tmp_path / 'target-a/project.toml')
    assert cfg['md_gbsa_remote_work_dir'] == projects[0]['md_gbsa_remote_work_dir']


def test_explicit_remote_overrides_are_not_resolved_on_client(tmp_path):
    remote = '/remote-share/not-local/../work'
    cfg = {'out-dir': 'output', 'use-slurm': True,
           'md-gbsa-remote-work-dir': remote, 'amber-slurm-work-dir': remote}
    assert md_gbsa_settings(cfg, tmp_path)['md_gbsa_remote_work_dir'] == remote
    assert amber_settings(cfg, tmp_path)['amber_remote']['remote_work_dir'] == remote


@pytest.mark.parametrize('key', ['md-gbsa-remote-work-dir', 'amber-slurm-work-dir'])
def test_explicit_remote_work_paths_still_require_absolute_paths(tmp_path, key):
    cfg = {'out-dir': 'output', 'use-slurm': True, key: 'relative/remote'}
    function = md_gbsa_settings if key.startswith('md-') else amber_settings
    with pytest.raises(ValueError, match='absolute remote path'):
        function(cfg, tmp_path)


def test_absolute_output_is_not_rebased_against_toml(tmp_path):
    output = tmp_path / 'shared-output'
    cfg = {'out-dir': str(output), 'use-slurm': True}
    base = tmp_path / 'elsewhere'
    assert md_gbsa_settings(cfg, base)['md_gbsa_remote_work_dir'] == str(output / 'graft-md-gbsa-runs')
    assert amber_settings(cfg, base)['amber_remote']['remote_work_dir'] == str(output / 'graft-amber-slurm-runs')
