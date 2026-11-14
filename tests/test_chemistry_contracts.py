"""Characterize existing chemistry boundaries; no new normalization policy."""
from contextlib import closing
import json
import sqlite3

import pytest
from rdkit import Chem

from graft import _core, database
from graft.chemistry.similarity import tanimoto


@pytest.mark.parametrize('smiles,expected', [
    ('OCC', 'CCO'),
    (' CCO ', 'CCO'),
    ('CCO label', 'CCO'),
    ('C1=CC=CC=C1', 'c1ccccc1'),
    ('C[C@H](O)F', 'C[C@H](O)F'),
    ('C[C@@H](O)F', 'C[C@@H](O)F'),
    ('F/C=C/F', 'F/C=C/F'),
    ('F/C=C\\F', 'F/C=C\\F'),
    ('[NH4+]', '[NH4+]'),
    ('[13CH3]CO', '[13CH3]CO'),
    ('[Na+].CC', 'CC.[Na+]'),
    ('[H]OC', 'CO'),
    ('[2H]OC', '[2H]OC'),
    ('[CH3:7][OH:9]', '[CH3:7][OH:9]'),
])
def test_canonical_smiles_observed_chemical_identity(smiles, expected):
    assert _core.canonicalize_smiles(smiles) == expected


@pytest.mark.parametrize('value', [None, '', ' \t\n', b'CCO', 12, []])
def test_canonical_smiles_rejects_blank_and_nonstring_before_rdkit(value, monkeypatch):
    def unexpected_parse(*args, **kwargs):
        pytest.fail('Blank/non-string input reached RDKit')

    monkeypatch.setattr(_core.Chem, 'MolFromSmiles', unexpected_parse)
    assert _core.canonicalize_smiles(value) is None


@pytest.mark.parametrize('smiles', ['not_smiles', 'c1cccc1', 'C(C)(C)(C)(C)C', '.'])
def test_canonical_smiles_parse_and_sanitization_failures_return_none(smiles):
    assert _core.canonicalize_smiles(smiles) is None


def test_canonical_smiles_empty_rdkit_molecule_returns_none(monkeypatch):
    monkeypatch.setattr(_core.Chem, 'MolFromSmiles', lambda value: Chem.Mol())
    assert _core.canonicalize_smiles('CCO') is None


@pytest.mark.parametrize('operation', ['MolFromSmiles', 'MolToSmiles'])
def test_canonical_smiles_propagates_rdkit_call_exceptions(operation, monkeypatch):
    failure = RuntimeError('injected RDKit call failure')

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(_core.Chem, operation, fail)
    with pytest.raises(RuntimeError) as caught:
        _core.canonicalize_smiles('CCO')
    assert caught.value is failure


def test_core_morgan_options_and_observed_ethanol_bit_vector():
    options = _core.FP_GENERATOR.GetOptions()
    assert (options.radius, options.fpSize, options.includeChirality) == (2, 2048, False)
    fingerprint = _core.FP_GENERATOR.GetFingerprint(Chem.MolFromSmiles('CCO'))
    assert fingerprint.GetNumBits() == 2048
    assert tuple(fingerprint.GetOnBits()) == (80, 222, 294, 807, 1057, 1410)


@pytest.mark.parametrize('a,b,expected', [
    ('CCO', 'OCC', 1.0),
    ('CCO', 'CCN', 1/3),
    ('CCN', 'CCO', 1/3),
    ('C[C@H](O)F', 'C[C@@H](O)F', 1.0),
    ('F/C=C/F', 'F/C=C\\F', 1.0),
    ('[13CH3]CO', 'CCO', 1.0),
    ('[NH4+]', 'N', 0.0),
    ('C1=CC=CC=C1', 'c1ccccc1', 1.0),
    ('', '', 0.0),
    ('', 'CCO', 0.0),
    (b'CCO', 'CCO', 1.0),
])
def test_similarity_observed_values_including_stereo_and_empty_inputs(a, b, expected):
    assert tanimoto(a, b) == expected


@pytest.mark.parametrize('a,b,error', [
    ('not_smiles', 'CCO', ValueError),
    ('CCO', 'not_smiles', ValueError),
    (' ', 'CCO', ValueError),
    (None, 'CCO', TypeError),
    ('CCO', None, TypeError),
    ('not_smiles', None, TypeError),
    (12, 'CCO', TypeError),
])
def test_similarity_keeps_current_exception_types_and_parse_order(a, b, error):
    with pytest.raises(error) as caught:
        tanimoto(a, b)
    if error is ValueError:
        assert str(caught.value) == 'Invalid SMILES'


def test_similarity_reads_the_current_core_generator_binding(monkeypatch):
    original = _core.FP_GENERATOR
    calls = []

    class Generator:
        def GetFingerprint(self, mol):
            calls.append(Chem.MolToSmiles(mol))
            return original.GetFingerprint(mol)

    monkeypatch.setattr(_core, 'FP_GENERATOR', Generator())
    assert tanimoto('CCO', 'CCN') == 1/3
    assert calls == ['CCO', 'CCN']


@pytest.fixture
def chemistry_db(tmp_path):
    path = tmp_path/'chemistry.sqlite'
    with closing(sqlite3.connect(path)) as conn, conn:
        _core.create_sqlite_schema(conn)
        conn.executemany(
            'INSERT INTO compound(id,name,smiles,iteration,state,prime_gbsa_score,'
            'failed_stage,failure_message) VALUES (?,?,?,0,?,?,?,?)', [
                (10, 'REF_ETHANOL', 'OCC', 'prime_gbsa_done', -40., None, None),
                (20, 'REF_STEREO', 'C[C@H](O)F', 'failed', None,
                 'prime_gbsa', 'saved native failure'),
            ])
    database.freeze_crem_generation(path, 1, {
        'protocol': 'crem_pipeline_single_expansion_v1', 'target_count': 4,
    })
    return path


def _compound_rows(path):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute('SELECT * FROM compound ORDER BY id').fetchall()


def _record(smiles, label):
    mol = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
    return dict(smiles=smiles,
                molblock=Chem.MolToMolBlock(mol) if mol is not None else 'unused invalid pose',
                history={'iteration': 1, 'label': label}, properties={'source': label})


def test_project_smiles_preserves_ids_duplicates_none_and_stored_results(chemistry_db):
    with closing(sqlite3.connect(chemistry_db)) as conn, conn:
        conn.executemany('INSERT INTO compound(id,name,smiles,iteration,state) '
                         "VALUES (?,?,?,0,'reference')", [
                             (30, 'OLD_BAD', 'not_smiles'), (40, 'SECOND_ETHANOL', 'CCO'),
                         ])
    before = _compound_rows(chemistry_db)
    assert database.project_smiles(chemistry_db) == [
        (10, 'CCO'), (20, 'C[C@H](O)F'), (30, None), (40, 'CCO'),
    ]
    assert _compound_rows(chemistry_db) == before


def test_crem_import_deduplicates_canonical_identity_but_keeps_stereo_and_provenance(chemistry_db):
    before = _compound_rows(chemistry_db)
    records = [_record(smiles, str(index)) for index, smiles in enumerate([
        'CCO', 'OCC', 'C[C@H](O)F', 'C[C@@H](O)F',
        '[13CH3]CO', '[NH4+]', 'C1=CC=CC=C1', 'c1ccccc1',
    ])]
    assert database.import_crem_results(chemistry_db, 1, records, 'exhausted') == 4
    assert _compound_rows(chemistry_db)[:2] == before
    with closing(sqlite3.connect(chemistry_db)) as conn:
        assert conn.execute('SELECT id,name,smiles,state FROM compound WHERE iteration=1 '
                            'ORDER BY id').fetchall() == [
            (21, 'CMPID_21', 'C[C@@H](O)F', 'generated'),
            (22, 'CMPID_22', '[13CH3]CO', 'generated'),
            (23, 'CMPID_23', '[NH4+]', 'generated'),
            (24, 'CMPID_24', 'c1ccccc1', 'generated'),
        ]
        poses = conn.execute('SELECT compound_id,molblock,history,properties '
                             'FROM crem_compound_pose ORDER BY compound_id').fetchall()
    for saved, source in zip(poses, records[3:7], strict=True):
        assert saved[1] == source['molblock']
        assert json.loads(saved[2]) == source['history']
        assert json.loads(saved[3]) == source['properties']
    completed = database.crem_generation(chemistry_db, 1)
    after = _compound_rows(chemistry_db)

    def unread_records():
        pytest.fail('Completed CReM import consumed new records')
        yield

    assert database.import_crem_results(chemistry_db, 1, unread_records(), 'target_count') == 4
    assert database.crem_generation(chemistry_db, 1) == completed
    assert _compound_rows(chemistry_db) == after


def test_database_read_and_import_use_the_current_core_canonicalizer_binding(chemistry_db, monkeypatch):
    original = _core.canonicalize_smiles
    calls = []

    def observe(smiles):
        calls.append(smiles)
        return original(smiles)

    monkeypatch.setattr(_core, 'canonicalize_smiles', observe)
    assert database.project_smiles(chemistry_db) == [(10, 'CCO'), (20, 'C[C@H](O)F')]
    assert calls == ['OCC', 'C[C@H](O)F']
    calls.clear()
    assert database.import_crem_results(chemistry_db, 1, [_record('CCN', 'new')], 'exhausted') == 1
    assert calls == ['OCC', 'C[C@H](O)F', 'CCN']


def test_canonicalizer_exception_rolls_back_partial_crem_import(chemistry_db, monkeypatch):
    original = _core.canonicalize_smiles
    failure = RuntimeError('injected canonicalization failure')
    before = _compound_rows(chemistry_db)
    plan = database.crem_generation(chemistry_db, 1)
    records = [_record('CCN', 'first'), _record('CO', 'second')]

    def fail_second(smiles):
        if smiles == 'CO':
            raise failure
        return original(smiles)

    monkeypatch.setattr(_core, 'canonicalize_smiles', fail_second)
    with pytest.raises(RuntimeError) as caught:
        database.import_crem_results(chemistry_db, 1, records, 'exhausted')
    assert caught.value is failure
    assert _compound_rows(chemistry_db) == before
    assert database.crem_generation(chemistry_db, 1) == plan
    with closing(sqlite3.connect(chemistry_db)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM crem_compound_pose').fetchone() == (0,)


@pytest.mark.parametrize('incoming', [None, 'not_smiles'])
@pytest.mark.parametrize('existing_invalid', [False, True])
def test_crem_import_observed_none_identity_behavior(chemistry_db, incoming, existing_invalid):
    # Existing behavior to detect changes, not approval of invalid scientific input.
    if existing_invalid:
        with closing(sqlite3.connect(chemistry_db)) as conn, conn:
            conn.execute("INSERT INTO compound(id,name,smiles,iteration,state) "
                         "VALUES (30,'OLD_BAD','not_smiles',0,'reference')")
    before = _compound_rows(chemistry_db)
    plan = database.crem_generation(chemistry_db, 1)
    records = [_record('CCN', 'first'), _record(incoming, 'bad')]
    if existing_invalid:
        assert database.import_crem_results(chemistry_db, 1, records, 'exhausted') == 1
        assert _compound_rows(chemistry_db)[:3] == before
    else:
        with pytest.raises(sqlite3.IntegrityError, match='NOT NULL constraint failed: compound.smiles'):
            database.import_crem_results(chemistry_db, 1, records, 'exhausted')
        assert _compound_rows(chemistry_db) == before
        assert database.crem_generation(chemistry_db, 1) == plan
