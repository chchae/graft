"""Project startup order and pure-loader compatibility before ownership changes."""
from pathlib import Path

import pytest

from graft import _core, config, database, reference_scores
from graft.stages import _logging
from test_refactoring_contracts import PROJECT_TOML


def test_loader_preserves_both_keyword_names_and_no_initialization(tmp_path):
    path=tmp_path/'project.toml';path.write_text(PROJECT_TOML)
    assert _core.load_project_toml(filename=path)==config.load_project_toml(path=path)
    assert list(tmp_path.iterdir())==[path]


def test_loader_retains_multi_import_callback_capture_order(tmp_path,monkeypatch):
    path=tmp_path/'project.toml';path.write_text(PROJECT_TOML);events=[]
    def original(raw):events.append('original-aizynth');return None
    def changed(raw):events.append('changed-aizynth');return None
    def reinvent(raw):
        events.append('reinvent')
        monkeypatch.setattr(config,'remote_aizynth_settings',changed)
        return None
    monkeypatch.setattr(config,'remote_aizynth_settings',original)
    monkeypatch.setattr(config,'remote_reinvent_settings',reinvent)
    _core.load_project_toml(path)
    assert events==['reinvent','original-aizynth']


@pytest.mark.parametrize('phase',[None,'migrate','initialize','restore','open','enter','ensure','exit','mkdir'])
def test_project_bootstrap_preserves_dynamic_core_hooks_order_and_errors(tmp_path,monkeypatch,phase):
    path=tmp_path/'project.toml';path.write_text(PROJECT_TOML)
    cfg=_core.load_project_toml(path);events=[];failure=RuntimeError('startup boundary failure')
    def point(name):
        events.append(name)
        if phase==name:raise failure
    monkeypatch.setattr(_core,'load_project_toml',lambda value:events.append('load') or dict(cfg))
    monkeypatch.setattr(_logging,'set_log_level',lambda value:events.append('level'))
    connection=object()
    def ensure(conn):assert conn is connection;point('ensure')
    class Context:
        def __enter__(self):point('enter');return connection
        def __exit__(self,*args):point('exit');return False
    def opening(db):assert db==cfg['db_path'];point('open');return Context()
    def restore(args):
        point('restore')
        monkeypatch.setattr(_core,'open_sqlite',opening)
        monkeypatch.setattr(_core,'ensure_schema_columns',ensure)
    def initialize(**kwargs):
        point('initialize')
        assert kwargs==dict(db_path=cfg['db_path'],reference_poses=cfg['reference_poses'],schrodinger=tmp_path/'suite',sa_score_cpus=cfg['sa_score_cpus'])
        return True
    def migrate(old,new):
        point('migrate')
        assert old==tmp_path/'project.sqlite' and new==cfg['db_path']
        monkeypatch.setattr(_core,'initialize_project_database',initialize)
    def unexpected(*args,**kwargs):pytest.fail('An outdated startup hook was captured')
    monkeypatch.setattr(database,'migrate_project_database',migrate)
    monkeypatch.setattr(_core,'initialize_project_database',unexpected)
    monkeypatch.setattr(_core,'open_sqlite',unexpected)
    monkeypatch.setattr(_core,'ensure_schema_columns',unexpected)
    monkeypatch.setattr(reference_scores,'restore',restore)
    mkdir=Path.mkdir
    def creating(directory,*args,**kwargs):
        if directory==cfg['output']:point('mkdir')
        return mkdir(directory,*args,**kwargs)
    monkeypatch.setattr(Path,'mkdir',creating)
    sequence=['load','level','migrate','initialize','restore','open','enter','ensure','exit','mkdir']
    if phase:
        with pytest.raises(RuntimeError) as caught:_core.configure_project(path,tmp_path/'suite')
        assert caught.value is failure
        expected=sequence[:sequence.index(phase)+1]
        if phase=='ensure':expected+=['exit']
        assert events==expected
    else:
        args=_core.configure_project(path,tmp_path/'suite')
        assert args.db_path==cfg['db_path'] and args.output.is_dir()
        assert events==sequence
    assert not cfg['db_path'].exists()


def test_config_import_is_independent_of_core_and_workflow_io():
    import subprocess,sys
    code="""
import sqlite3,subprocess,sys

def forbidden(*args,**kwargs):
    raise AssertionError('Config import initialized workflow I/O')

sqlite3.connect=forbidden
subprocess.run=forbidden
subprocess.Popen=forbidden
import graft.config
assert 'graft._core' not in sys.modules
assert 'graft.database' not in sys.modules
"""
    subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,text=True)
