"""New implementation leaves must import without workflow initialization."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize('module', ['graft.chemistry.smiles', 'graft._job_record'])
def test_leaf_import_does_not_load_core_database_or_launch_work(module):
    code = """
import importlib
import sqlite3
import subprocess
import sys

def unexpected(*args, **kwargs):
    raise AssertionError('Implementation leaf initialized workflow I/O')

sqlite3.connect = unexpected
subprocess.run = unexpected
subprocess.Popen = unexpected
importlib.import_module(sys.argv[1])
assert not {'graft._core', 'graft.database', 'graft.config'} & sys.modules.keys()
"""
    subprocess.run([sys.executable, '-c', code, module], check=True,
                   capture_output=True, text=True)
