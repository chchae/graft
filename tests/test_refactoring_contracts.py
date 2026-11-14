"""Observable configuration/status contracts before moving their implementation."""
from contextlib import closing
from contextvars import ContextVar
from pathlib import Path
import sqlite3

from graft import _core, cli
from graft.config import combichem_3d_settings
from graft.stages import _logging


PROJECT_TOML = '''libinvent-prior = "prior.prior"
dock-grid = "grid.zip"
reference-pose = "references.maegz"
out-dir = "output"
sample-size = 3
target-count = 3
max-iteration = 2
host = "localhost"
synthetic-feasibility-enabled = false
'''


def test_crem_table_and_dotted_keys_load_identically_without_initializing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    project = tmp_path/'project.toml'
    shared = PROJECT_TOML.replace('libinvent-prior = "prior.prior"\n', '') + '''\
generation-method = "crem"
reference-mcs = "c1ccc([*:1])cc1"
elite-count = 7
schrodinger = "suite"
'''
    section = '''generation-backend = "crem"
crem-db = "fragments.sqlite"
processes = 2
sidechain-min-heavy-atoms = 2
sidechain-max-heavy-atoms = 8
conformers = 4
'''
    project.write_text(shared + '\n[combichem-3d]\n' + section)
    table_project = _core.load_project_toml(project)
    table_growth = combichem_3d_settings(project)

    # Dotted keys precede the root settings, as in the reorganized EGFR input.
    dotted = '\n'.join('combichem-3d.' + line for line in section.splitlines())
    contents = dotted + '\n' + shared
    project.write_text(contents)
    assert _core.load_project_toml(project) == table_project
    assert combichem_3d_settings(project) == table_growth
    assert table_project['db_path'] == tmp_path/'output/project.sqlite'
    assert table_project['elite_count'] == 7
    assert table_project['target_count'] == 3
    assert table_growth['crem-db'] == str(tmp_path/'fragments.sqlite')
    assert table_growth['sidechain-max-heavy-atoms'] == 8
    assert table_growth['conformers'] == 4
    assert project.read_text() == contents
    assert list(tmp_path.iterdir()) == [project]


def test_status_backfills_legacy_reference_metadata_and_preserves_scientific_rows(
        tmp_path, monkeypatch, capsys):
    project = tmp_path/'project.toml'
    project.write_text(PROJECT_TOML)
    db = tmp_path/'output/project.sqlite'
    db.parent.mkdir()
    scientific_rows = [
        (1, 'REF', 'CCO', 0, 'reference', None, None, None, None, None, None, None),
        (2, 'CMPID_2', 'CCN', 1, 'prime_gbsa_done', -8., -40., -35., None, 1, None, None),
        (3, 'CMPID_3', 'CCC', 1, 'failed', -7., None, None, None, 0,
         'prime_gbsa', 'saved native failure'),
    ]
    fields = ('id,name,smiles,iteration,state,docking_score,prime_gbsa_score,'
              'amber_gbsa_score,fep_score,synthetic_feasibility,failed_stage,failure_message')
    with closing(sqlite3.connect(db)) as conn, conn:
        # Build a pre-descriptor schema without using today's schema helper.
        conn.execute('''CREATE TABLE compound (
            id INTEGER PRIMARY KEY, name TEXT, smiles TEXT, iteration INTEGER,
            state TEXT, docking_score REAL, prime_gbsa_score REAL,
            amber_gbsa_score REAL, fep_score REAL, synthetic_feasibility INTEGER,
            failed_stage TEXT, failure_message TEXT,
            modified_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.executemany(f'INSERT INTO compound ({fields}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         scientific_rows)
        assert 'rdk_qed' not in {row[1] for row in conn.execute('PRAGMA table_info(compound)')}

    def unexpected_native_command(*args, **kwargs):
        raise AssertionError('Status of an existing DB must not require a native command')

    monkeypatch.setattr(_core, 'run_command', unexpected_native_command)
    monkeypatch.setattr(_logging, '_log_level', ContextVar('status_contract_level', default=1))
    assert cli.main(['status', str(project)]) is None
    captured = capsys.readouterr()
    expected = (Path(__file__).parent/'data/refactoring/status-existing-db.txt').read_text()
    assert captured.out.replace(str(db), '<DB>') == expected
    assert captured.err == ''

    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(f'SELECT {fields} FROM compound ORDER BY id').fetchall() == scientific_rows
        descriptors = conn.execute(
            'SELECT id,rdk_qed,rdk_sa_score FROM compound ORDER BY id').fetchall()
    assert descriptors[0][1] is not None and descriptors[0][2] is not None
    assert descriptors[1:] == [(2, None, None), (3, None, None)]
    assert not (tmp_path/'references.maegz').exists()

    # Repeated status must keep the schema/backfill but never rescore these rows.
    assert cli.main(['status', str(project)]) is None
    repeated = capsys.readouterr()
    assert 'calculating local RDKit properties' not in repeated.out
    assert repeated.out.split('Project: project\n', 1)[1] == captured.out.split('Project: project\n', 1)[1]
    assert repeated.err == ''
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(f'SELECT {fields} FROM compound ORDER BY id').fetchall() == scientific_rows
        assert conn.execute(
            'SELECT id,rdk_qed,rdk_sa_score FROM compound ORDER BY id').fetchall() == descriptors
