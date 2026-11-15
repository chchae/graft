"""A changed source never authorizes new work; saved native input hashes can prove recovery."""
import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, database
from graft.chemistry import scaffold
from graft.schrodinger_guard import JobStatusUnavailable
from graft.stages import glide


@pytest.fixture
def case(tmp_path, monkeypatch):
    db = tmp_path/'project.sqlite'
    with database.connect(db) as conn:
        _core.create_sqlite_schema(conn)
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state) VALUES (?,?,'C',0,'reference')", [(11,'REF11'),(12,'REF12')])
        conn.executemany("INSERT INTO compound(id,name,smiles,iteration,state,similar_to) VALUES (?,?,'CC',1,?,?)", [(21,'CMPID_21','glide_running',11),(22,'CMPID_22','glide_running',12),(23,'CMPID_23','ligprepped',11)])
    root = tmp_path/'iter01/06-glide';root.mkdir(parents=True)
    prepared = tmp_path/'iter01/05-ligprep/ligprep.maegz';prepared.parent.mkdir();prepared.write_bytes(b'overwritten source')
    marker = root/'ligprep_input.sha256';marker.write_text(hashlib.sha256(b'original source').hexdigest()+'\n')
    (root/'reference_map.tsv').write_text('id\treference_id\n21\t11\n22\t12\n')
    grid = tmp_path/'grid.zip';grid.write_bytes(b'unchanged grid')
    args = SimpleNamespace(db_path=db,output=tmp_path,grid=grid,schrodinger=Path('/suite'),target_count=2,glide_job_timeout=0)
    ns = SimpleNamespace(iteration=1,poll_interval=1,completion_fraction=.95,tail_timeout=None)
    metadata = {};groups={};events=[]
    for ref,cid in [(11,21),(12,22)]:
        group=root/f'ref_{ref}';group.mkdir();groups[ref]=group
        infile=group/f'glide_{ref}.in';infile.write_text(f'LIGANDFILE {group}/ligprep_group.maegz\n')
        (group/'ligprep_group.maegz').write_bytes(f'original native ligand {cid}'.encode())
        (group/'_reference.maegz').write_bytes(f'original reference {ref}'.encode())
        record=dict(job_id=f'native-{ref}',command=['/suite/glide',str(infile),'-HOST','cpu:1'],downloaded=False)
        (group/'glide_job.json').write_text(json.dumps(record))
        files=[]
        for path in (infile,group/'ligprep_group.maegz',group/'_reference.maegz',grid):
            files.append(dict(name=path.name,hash=base64.b64encode(hashlib.sha256(path.read_bytes()).digest()).decode(),size=str(path.stat().st_size)))
        metadata[record['job_id']]=[dict(jobId=record['job_id'],status='RUNNING',spec=dict(launchParams=dict(launchDirectory=str(group)),taskSpec=dict(input=dict(inputFiles=files))))]
    monkeypatch.setattr(database,'upstream_completion_reason',lambda *a,**kw:None)
    monkeypatch.setattr(database,'crem_glide_references',lambda *a:{})
    monkeypatch.setattr(scaffold,'prepare_iteration_scaffold',lambda *a,**kw:None)
    monkeypatch.setattr(glide,'pose_selection_point',lambda *a:None)
    def forbidden(*a,**kw):pytest.fail('Changed input reached fresh preparation, claims or submission')
    for name in ('assign_reference_compounds','split_ligprep_by_reference','submit_glide'):
        monkeypatch.setattr(_core,name,forbidden)
    for name in ('_claim_reference_groups','prepare_glide_files','prepare_new_inputs','recover_timeout_groups'):
        monkeypatch.setattr(glide,name,forbidden)
    monkeypatch.setattr(glide,'job_details',lambda suite,identity,directory:copy.deepcopy(metadata[identity]))
    monkeypatch.setattr(_core,'create_score_extractor',lambda *a,**kw:events.append('extractor') or 'extractor')
    monkeypatch.setattr(_core,'create_best_pose_merger',lambda *a:events.append('merger') or 'merger')
    def collect(iteration,args,jobs,*unused,**kw):
        events.append(('collect',[j['reference_id'] for j in jobs],[[r[0] for r in j['compounds']] for j in jobs]))
        return jobs,0
    monkeypatch.setattr(glide,'collect_finished_groups',collect)
    snapshot={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    return SimpleNamespace(args=args,ns=ns,root=root,groups=groups,metadata=metadata,events=events,snapshot=snapshot,prepared=prepared,marker=marker,run=lambda:glide._run_stage(args,ns))


def test_changed_source_recovers_only_hash_verified_saved_jobs(case,capsys):
    case.run()
    assert case.events == ['extractor','merger',('collect',[11,12],[[21],[22]])]
    assert 'new submissions blocked' in capsys.readouterr().out
    assert all(p.read_bytes()==data for p,data in case.snapshot.items())
    with database.connect(case.args.db_path) as conn:
        assert conn.execute('SELECT id,state FROM compound WHERE iteration=1 ORDER BY id').fetchall()==[(21,'glide_running'),(22,'glide_running'),(23,'ligprepped')]


@pytest.mark.parametrize('name',['ligprep_group.maegz','_reference.maegz','glide_11.in','grid.zip'])
def test_changed_submitted_input_blocks_collection_without_modifying_marker(case,name):
    path=case.args.grid if name=='grid.zip' else case.groups[11]/name
    path.write_bytes(b'tampered')
    marker=case.marker.read_bytes()
    with pytest.raises(RuntimeError,match='input|checksum|digest'):
        case.run()
    assert case.events==[] and case.marker.read_bytes()==marker


@pytest.mark.parametrize('damage',['missing_hash','bad_base64','short_hash','missing_entry','duplicate_entry','wrong_parent','wrong_directory','missing_command','outside_command','extra_input'])
def test_unverifiable_native_input_contract_fails_closed(case,damage):
    parent=case.metadata['native-11'][0];files=parent['spec']['taskSpec']['input']['inputFiles']
    if damage=='missing_hash':files[0]['hash']=''
    elif damage=='bad_base64':files[0]['hash']='invalid***'
    elif damage=='short_hash':files[0]['hash']=base64.b64encode(b'short').decode()
    elif damage=='missing_entry':files.pop()
    elif damage=='duplicate_entry':files.append(dict(files[0]))
    elif damage=='wrong_parent':parent['jobId']='another-job'
    elif damage=='wrong_directory':parent['spec']['launchParams']['launchDirectory']=str(case.root/'elsewhere')
    elif damage=='extra_input':files.append(dict(name='unverified.dat',hash=files[0]['hash']))
    else:
        path=case.groups[11]/'glide_job.json';record=json.loads(path.read_text())
        record['command']=[] if damage=='missing_command' else ['/suite/glide','/elsewhere/changed.in']
        path.write_text(json.dumps(record))
    with pytest.raises(RuntimeError):case.run()
    assert case.events==[]


def test_native_query_uncertainty_preserves_group_but_does_not_block_verified_peer(case,monkeypatch,capsys):
    def query(suite,identity,directory):
        if identity=='native-11':raise JobStatusUnavailable('temporary info failure')
        return copy.deepcopy(case.metadata[identity])
    monkeypatch.setattr(glide,'job_details',query)
    case.run()
    assert case.events==['extractor','merger',('collect',[12],[[22]])]
    assert 'temporary info failure' in capsys.readouterr().out
    assert (case.groups[11]/'glide_job.json').read_bytes()==case.snapshot[case.groups[11]/'glide_job.json']


def test_new_group_without_saved_identity_remains_pending(case):
    (case.groups[11]/'glide_job.json').unlink()
    case.run()
    assert case.events==['extractor','merger',('collect',[12],[[22]])]


def test_legacy_timeout_members_are_enrolled_before_slow_native_verification(case,monkeypatch):
    path=case.groups[11]/'glide_job.json';record=json.loads(path.read_text());record['timeout_requested_at']=1;path.write_text(json.dumps(record))
    with database.connect(case.args.db_path) as conn:conn.execute("UPDATE compound SET state='failed',failed_stage='glide' WHERE id=21")
    query=glide.job_details
    def inspect(suite,identity,directory):
        with database.connect(case.args.db_path) as conn:
            assert conn.execute('SELECT reference_id,job_id FROM glide_timeout_collection').fetchall()==[(11,'native-11')]
        return query(suite,identity,directory)
    monkeypatch.setattr(glide,'job_details',inspect)
    case.run()
    assert case.events==['extractor','merger',('collect',[11,12],[[21],[22]])]


def test_verified_completed_peer_publishes_before_committing_while_other_job_stays_pending(case,monkeypatch):
    # The real collector is preserved before this fixture's replacement via the module snapshot below.
    monkeypatch.setattr(glide,'collect_finished_groups',REAL_COLLECT)
    monkeypatch.setattr(glide,'active_jobs',lambda *a:[])
    monkeypatch.setattr(glide,'group_status',lambda suite,directory,*a,**kw:'DONE' if directory.name=='ref_11' else 'RUNNING')
    def extract(suite,helper,output,directory):
        scores=directory/'scores.tsv';scores.write_text('id\tdocking_score\n21\t-9.25\n')
        best=directory/'best_poses.maegz';best.write_text('matching original pose')
        return scores,best
    def publish(args,root,helper):
        with database.connect(args.db_path) as conn:assert conn.execute('SELECT docking_score FROM compound WHERE id=21').fetchone()==(None,)
        (root/'best_poses.maegz').write_text('matching original pose')
    monkeypatch.setattr(_core,'extract_scores_and_best_poses',extract)
    monkeypatch.setattr(glide,'merge_current_best',publish)
    (case.groups[11]/'glide_11_lib.maegz').write_text('verified native output')
    case.run()
    with database.connect(case.args.db_path) as conn:
        assert conn.execute('SELECT id,state,docking_score FROM compound WHERE iteration=1 ORDER BY id').fetchall()==[(21,'docked',-9.25),(22,'glide_running',None),(23,'ligprepped',None)]
    assert case.marker.read_bytes()==case.snapshot[case.marker]


REAL_COLLECT=glide.collect_finished_groups


def test_input_change_during_native_download_blocks_result_publication(case,monkeypatch):
    monkeypatch.setattr(glide,'collect_finished_groups',REAL_COLLECT)
    monkeypatch.setattr(glide,'active_jobs',lambda *a:[])
    def status(suite,directory,*args,**kwargs):
        (directory/'ligprep_group.maegz').write_bytes(b'changed during native reconciliation')
        return 'DONE'
    monkeypatch.setattr(glide,'group_status',status)
    monkeypatch.setattr(_core,'extract_scores_and_best_poses',lambda *a:pytest.fail('Published after native input change'))
    with pytest.raises(RuntimeError,match='checksum'):
        case.run()
    with database.connect(case.args.db_path) as conn:
        assert conn.execute('SELECT docking_score,state FROM compound WHERE id=21').fetchone()==(None,'glide_running')
    assert case.marker.read_bytes()==case.snapshot[case.marker]


def test_timeout_without_native_identity_cannot_clear_the_completion_gate(case):
    path=case.groups[11]/'glide_job.json'
    record=json.loads(path.read_text());record.update(job_id=None,timeout_requested_at=1)
    path.write_text(json.dumps(record))
    with database.connect(case.args.db_path) as conn:
        conn.execute("UPDATE compound SET state='failed',failed_stage='glide' WHERE id=21")
    with pytest.raises(RuntimeError,match='identity|JobId'):
        case.run()
    assert case.events==[]


def test_empty_frozen_mapping_cannot_establish_safe_recovery(case):
    (case.root/'reference_map.tsv').write_text('id\treference_id\n')
    with pytest.raises(RuntimeError,match='mapping'):
        case.run()
    assert case.events==[]
