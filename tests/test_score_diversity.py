"""Synthetic selection checks: chemistry/ownership, not affinity validation."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from rdkit import Chem

from graft import _core, database
from graft.chemistry.selection import score_diverse_ids, POLICY


SMILES = ['CCCCCC', 'CCCCCCC', 'c1ccncc1', 'c1ccccc1O',
          'CC(=O)O', 'CCN(CC)CC', 'CCCCCCCC', 'CCCO']


@pytest.mark.parametrize('size,expected', [(3,[1,2]), (4,[1,2]), (5,[1,3]), (6,[1,3]), (8,[1,3])])
def test_top_half_threshold_rounding_and_chemical_diversity(size, expected):
    ranked=list(enumerate(SMILES[:size],1))
    assert score_diverse_ids(ranked,2)==expected
    assert score_diverse_ids(ranked,2)==expected


def test_best_molecule_retained_and_duplicate_fingerprints_tie_by_score_order():
    assert score_diverse_ids([(i,'CC') for i in range(10)],3)==[0,1,2]


def test_invalid_shortlisted_smiles_stops_without_silent_refill():
    with pytest.raises(ValueError,match='CMPID_2'):
        score_diverse_ids([(1,'CC'),(2,'invalid'),(3,'CO'),(4,'CN'),(5,'N'),(6,'O')],2)


@pytest.fixture
def db(tmp_path):
    path=tmp_path/'project.sqlite'
    with database.connect(path) as conn:
        _core.create_sqlite_schema(conn)
        for cid,smiles in enumerate(SMILES,1):
            conn.execute('INSERT INTO compound(id,name,smiles,iteration,state,docking_score) '
                         "VALUES (?,?,?,1,'docked',?)",(cid,f'CMPID_{cid}',smiles,-100+cid))
    return path


def test_atomic_amber_selection_freezes_before_claim_and_never_refills(db):
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(lambda _:database.claim_amber_candidates(db,1,8,2),range(2)))
    assert sorted(claims,key=len)==[[],[1,3]]
    with database.connect(db) as conn:
        policy,ids=conn.execute("SELECT policy,candidates FROM iteration_score_selection WHERE stage='amber_gbsa'").fetchone()
        assert policy==POLICY and json.loads(ids)==[1,3]
        conn.execute("UPDATE compound SET state='failed',failed_stage='amber_gbsa' WHERE id IN (1,3)")
    assert database.claim_amber_candidates(db,1,8,5)==[]
    assert database.amber_completion_reason(db,1,8,5) is None


def test_amber_legacy_started_population_retains_score_only_members(db):
    with database.connect(db) as conn:
        conn.execute('UPDATE compound SET amber_gbsa_score=-10 WHERE id=1')
    assert database.claim_amber_candidates(db,1,8,2)==[2]
    with database.connect(db) as conn:
        assert conn.execute('SELECT policy FROM iteration_score_selection').fetchone()[0]=='legacy_score_order'


@pytest.mark.parametrize('stage', ['amber_gbsa','prime_gbsa','md_gbsa','fep'])
def test_contact_priority_is_preserved_before_score_half_and_maxmin(db,stage):
    # Six contacts: take best three, then diverse two; better-scoring noncontacts lose.
    distances={str(cid):(1.0 if cid<=6 else 3.0) for cid in range(1,9)}
    database.freeze_attraction_policy(db,1,stage,[0,0,0],distances)
    with database.connect(db) as conn:
        ranked=conn.execute('SELECT id FROM compound ORDER BY docking_score,id').fetchall()
        assert [r[0] for r in database._score_diverse_rows(conn,1,stage,ranked,2)]==[1,3]
    # Boundary and non-contact priority are tested by placing the best score outside.
    with database.connect(db) as conn:
        conn.execute('UPDATE iteration_attraction_point SET distances=? WHERE stage=?',
                     (json.dumps({str(cid):(2.0 if cid>=3 else 3.0) for cid in range(1,9)}),stage))
        assert all(r[0]>=3 for r in database._score_diverse_rows(conn,1,stage,ranked,2))


def test_fewer_contacts_are_all_kept_and_remaining_quota_is_diversified(db):
    distances={str(cid):(1.0 if cid==8 else 3.0) for cid in range(1,9)}
    database.freeze_attraction_policy(db,1,'amber_gbsa',[0,0,0],distances)
    assert database.claim_amber_candidates(db,1,8,3)==[8,1,3]


def test_prime_preview_claim_and_readiness_agree_on_diverse_members(db):
    assert [r[0] for r in database.prime_selection_rows(db,1,2)]==[1,3]
    assert database.claim_prime_gbsa_candidates(db,1,2,8)==[1,3]
    with database.connect(db) as conn:
        conn.execute("UPDATE compound SET prime_gbsa_score=-30,state='prime_gbsa_done' WHERE id IN (1,3)")
    assert database.claim_prime_gbsa_candidates(db,1,4,8)==[]
    assert database.iteration_completion_reason(db,1,8,4,1) is None


def test_prime_never_mixes_finite_amber_scores_with_glide_refill(db):
    with database.connect(db) as conn:
        conn.execute('UPDATE compound SET amber_gbsa_score=-10 WHERE id IN (2,4)')
        conn.execute("UPDATE compound SET state='failed',failed_stage='amber_gbsa' WHERE id NOT IN (2,4)")
    assert database.claim_prime_gbsa_candidates(db,1,3,8,8)==[2,4]


def test_md_preview_and_freeze_are_identical_and_saved_selection_survives_changes(db):
    with database.connect(db) as conn:
        conn.execute("UPDATE compound SET prime_gbsa_score=docking_score,state='prime_gbsa_done'")
    preview=database.md_gbsa_candidates(db,1,count=2)
    assert preview['ids']==[1,3]
    settings=dict(ranking_source=preview['ranking_source'],ranking_ids=preview['ids'])
    saved=database.freeze_md_gbsa_selection(db,1,settings,count=2,target_count=8,prime_count=8)
    assert saved['ids']==preview['ids']
    assert database.freeze_md_gbsa_selection(db,1,{},count=5)==saved
    assert database.md_gbsa_candidates(db,1,count=5)['ids']==[1,3]


def test_fep_freezes_source_and_ids_without_reranking_on_restart(db):
    rows=database.fep_candidates(db,1,2,target_count=8)
    assert [r['id'] for r in rows]==[1,3]
    assert {r['ranking_source'] for r in rows}=={'docking'}
    with database.connect(db) as conn:
        conn.execute('UPDATE compound SET prime_gbsa_score=-id')
    assert [r['id'] for r in database.fep_candidates(db,1,5,target_count=8)]==[1,3]


def test_crem_elites_use_score_half_diversity_and_keep_provenance(db):
    with database.connect(db) as conn:
        conn.execute('CREATE TABLE crem_compound_pose(compound_id INTEGER PRIMARY KEY,iteration INTEGER,'
                     'molblock TEXT,history TEXT,properties TEXT)')
        for cid,smiles in enumerate(SMILES,1):
            conn.execute('INSERT INTO crem_compound_pose VALUES (?,1,?,?,?)',
                         (cid,Chem.MolToMolBlock(Chem.MolFromSmiles(smiles)),'{}','{}'))
    source,parents=database.crem_parent_selection(db,1,2)
    assert source=='docking'
    assert [p['compound_id'] for p in parents]==[1,3]
    assert [p['score'] for p in parents]==[-99,-97]


def test_reference_iteration_keeps_original_score_selection(db):
    with database.connect(db) as conn:
        conn.execute('UPDATE compound SET iteration=0')
    assert database.md_gbsa_candidates(db,0,count=2,prime_enabled=False)['ids']==[1,2]


def test_readiness_preview_does_not_create_selection_or_claims(db):
    assert database.prime_gbsa_candidates(db,1,2,8)==[1,3]
    with database.connect(db) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='iteration_score_selection'").fetchone()
        assert conn.execute("SELECT count(*) FROM compound WHERE state!='docked'").fetchone()[0]==0
