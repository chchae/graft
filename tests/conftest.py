"""Keep mocked submission locks and remote registries separate from live workers."""
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Keep collection/teardown and child processes away from live registries."""
    # Allocate multiprocessing's separately finalized socket directory first.
    from multiprocessing.util import get_temp_dir
    get_temp_dir()
    temporary = tempfile.TemporaryDirectory(prefix='graft-pytest-')
    patch = pytest.MonkeyPatch()
    def cleanup():
        patch.undo()
        temporary.cleanup()
    config.add_cleanup(cleanup)
    patch.setenv('TMPDIR', temporary.name)
    patch.setattr(tempfile, 'tempdir', temporary.name)
    from graft import schrodinger_guard
    root = Path(temporary.name)
    patch.setattr(schrodinger_guard, 'tempfile', SimpleNamespace(gettempdir=lambda: str(root)))
    patch.setattr(schrodinger_guard, '_md_registry', lambda: root/'session-md-remotes.json')
    patch.setattr(schrodinger_guard, 'local_glide_registry', lambda: root/'session-glide-clients.json')


@pytest.fixture(autouse=True)
def isolated_submission_mutex(tmp_path, monkeypatch):
    from graft import schrodinger_guard
    monkeypatch.setattr(schrodinger_guard, 'tempfile',
                        SimpleNamespace(gettempdir=lambda: str(tmp_path)))
    monkeypatch.setattr(schrodinger_guard, '_md_registry',
                        lambda: tmp_path / 'md-remotes.json')

    monkeypatch.setattr(schrodinger_guard, 'local_glide_registry',
                        lambda: tmp_path / 'glide-local-clients.json')
