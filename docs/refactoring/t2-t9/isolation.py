"""External test harness: isolate fallback registry even between fixture lifetimes."""
import os
from pathlib import Path

def pytest_configure(config):
    from graft import schrodinger_guard
    root = Path(os.environ['GRAFT_REFACTOR_TEST_TMP'])
    schrodinger_guard._md_registry = lambda: root/'session-md-remotes.json'
