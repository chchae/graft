"""Queue memory is per Glide job, without changing saved native jobs."""
from pathlib import Path

import pytest

from graft import _core, glide_recovery
from graft.config import GLIDE_SLURM_MEMORY_MB


@pytest.mark.parametrize('memory', [None, GLIDE_SLURM_MEMORY_MB])
def test_submission_preserves_split_and_uses_per_job_memory(tmp_path, monkeypatch, memory):
    calls = []
    monkeypatch.setattr(glide_recovery, 'submit',
                        lambda suite, command, folder: calls.append(command) or 'native')
    _core.submit_glide(Path('/suite'), tmp_path/'glide.in', tmp_path,
                       'slurm-cpu:8', ligand_count=100, molecules_per_job=25,
                       memory_mb=memory)
    cmd = calls[0]
    assert cmd[cmd.index('-HOST') + 1] == 'slurm-cpu:8'
    assert cmd[cmd.index('-NJOBS') + 1] == '4'
    if memory is None:
        assert '-QARGS' not in cmd
    else:
        assert cmd[cmd.index('-QARGS') + 1] == '--mem=2048M'
    assert '-WAIT' not in cmd


@pytest.mark.parametrize('memory', [0, -1, True, 2.5, '2048'])
def test_invalid_memory_cannot_launch(tmp_path, monkeypatch, memory):
    monkeypatch.setattr(glide_recovery, 'submit',
                        lambda *a: pytest.fail('Invalid resource request launched'))
    with pytest.raises(ValueError, match='memory_mb'):
        _core.submit_glide(Path('/suite'), tmp_path/'glide.in', tmp_path,
                           'slurm-cpu:8', memory_mb=memory)
