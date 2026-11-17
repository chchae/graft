"""CReM on pure node calculators; toy receptors/scores do not validate affinity."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rdkit import Chem

from graft import database
from graft.config import combichem_3d_settings
from graft.chemistry.combichem3d import crem_growth
from graft.chemistry.combichem3d.clashes import BumpChecker, load_receptor
from graft.chemistry.combichem3d.evaluation import Evaluator
from graft.stages import combichem_3d as stage, combichem_compute as compute
from graft.stages import combichem_product as product, combichem_slurm as slurm
from graft.stages import crem_generate, generate, druglike
from test_combichem_crem import project as growth_project
from test_crem_pipeline import project as pipeline_project, _score_generation
from test_combichem_slurm_pipeline import node


def slurm_config(args, tmp_path):
    env=tmp_path/'generate-env.sh'
    env.write_text('export PATH=/home1/conda/envs/graft/bin:$PATH\n')
    path=args.toml_file
    path.write_text(path.read_text()+f'''
execution-backend = "slurm"
remote-env-script = "{env}"
slurm-host = "slurm"
slurm-nodes = 1
slurm-cpus-per-node = 2
slurm-memory-mb = 4096
slurm-time-limit = 600
slurm-poll-seconds = 1.0
coordinator-processes = 1
''')
    return combichem_3d_settings(path)


def test_crem_slurm_configuration_and_scientific_fingerprint(pipeline_project,tmp_path):
    args=pipeline_project
    cfg=slurm_config(args,tmp_path)
    assert cfg['processes']==2 and cfg['coordinator-processes']==1
    assert cfg['generation-backend']=='crem' and cfg['growth-mode']=='fixed-frame'
    cfg=crem_generate.settings(args,1,tmp_path/'run')
    # Materialization is normally done by the integrated stage; the fixture
    # already uses SDF inputs, so source files are suitable fingerprint inputs.
    before=crem_generate._fingerprint(cfg,[])
    Path(cfg['remote-env-script']).write_text('export PATH=/different/bin:$PATH\n')
    assert crem_generate._fingerprint(cfg,[])!=before


def test_crem_slurm_products_match_local_including_exclusions(growth_project,tmp_path,monkeypatch):
    cfg=growth_project
    cfg['max-iteration']=1
    db,first=stage.prepare_project(cfg)
    # Plan locally, then execute the exact saved ordinal range both ways.
    stage.supervise(cfg,db,first)
    row=database.growth_parent(db,1)
    assert row['plan']['kind']=='crem_single_addition' and row['plan']['operations']
    parent=stage.restore_parent(row)
    checker=BumpChecker(load_receptor(cfg['receptor'],cfg),cfg)
    deadline=time.time()+60
    count=min(4,len(row['plan']['operations']))
    evaluator=Evaluator(cfg['reference-smiles'],cfg['filters'])
    initial=[product.evaluate_product(parent,row['history'],row['plan'],i,cfg,checker,evaluator,deadline)
             for i in range(count)]
    excluded=[payload['smiles'] for _,payload in initial if payload]
    assert excluded
    snapshot=tmp_path/'excluded.json'
    snapshot.write_text(json.dumps(excluded))
    cfg.update({'pipeline-generation':True,'excluded-smiles-path':str(snapshot),
                'excluded-smiles-sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest()})
    evaluator=Evaluator(cfg['reference-smiles'],cfg['filters'],excluded_smiles=excluded)
    expected=[product.evaluate_product(parent,row['history'],row['plan'],i,cfg,checker,evaluator,deadline)
              for i in range(count)]
    assert any('already_in_project' in report.get('filter_rejections',[]) for report,_ in expected)
    with node(cfg,tmp_path/'native',monkeypatch):
        remote=slurm.SlurmSearch(socket.gethostname(),cfg)
        try:
            received=[]
            result=remote.exchange(dict(kind='batch',molblock=row['molblock'],history=row['history'],
                plan=row['plan'],cursor=0,stop=count,deadline=deadline),product=received.append)
            assert result=={'batch_done':True}
            assert [(r['report'],r['payload']) for r in received]==expected
        finally:
            remote.close()


def test_calculator_rejects_crem_version_or_changed_exclusion_before_ready(growth_project,tmp_path):
    cfg=growth_project
    cfg['runtime-versions']=stage.versions() | {'crem':'wrong-version'}
    output=io.StringIO()
    with pytest.raises(RuntimeError,match='versions differ'):
        compute.serve(io.StringIO(json.dumps(cfg)+'\n'),output)
    assert output.getvalue()==''
    cfg['runtime-versions']['crem']=crem_growth.validate_database(cfg)
    path=tmp_path/'excluded.json';path.write_text('[]')
    cfg.update({'pipeline-generation':True,'excluded-smiles-path':str(path),'excluded-smiles-sha256':'wrong'})
    with pytest.raises(RuntimeError,match='exclusion snapshot changed'):
        compute.serve(io.StringIO(json.dumps(cfg)+'\n'),output)
    assert output.getvalue()==''


def test_integrated_slurm_two_iterations_preserve_parent_constraints_and_completed_results(
        pipeline_project,tmp_path,monkeypatch):
    args=pipeline_project
    slurm_config(args,tmp_path)
    calls=[]
    @contextmanager
    def allocated(cfg,flags):
        # Initialization/exclusion publication precedes native allocation;
        # project population import must follow verified native shutdown.
        assert database.growth_saved_settings(Path(cfg['out-dir'])/'combichem3d.sqlite')
        cfg['slurm-allocated-hosts']={socket.gethostname():2}
        session=SimpleNamespace(result=None)
        with node(cfg,tmp_path/f"native-{cfg['max-iteration']}",monkeypatch):
            with monkeypatch.context() as patch:
                patch.setattr(stage,'load_library',Mock(side_effect=AssertionError('No SMI library for CReM')))
                patch.setattr(stage,'BumpChecker',Mock(side_effect=AssertionError('No local geometry calculation')))
                patch.setattr(crem_growth,'methyl_probe',Mock(side_effect=AssertionError('Probe must run on node')))
                patch.setattr(product,'evaluate_product',Mock(side_effect=AssertionError('Product must run on node')))
                yield session
            assert session.result['stop_reason']=='exhausted'
            with database.connect(args.db_path) as conn:
                assert conn.execute('SELECT count(*) FROM compound WHERE iteration=?',
                                    (cfg['max-iteration'],)).fetchone()[0]==0
        calls.append(cfg['max-iteration'])
    monkeypatch.setattr(slurm,'allocation',allocated)
    generate._run_stage(args,1)
    druglike._run_stage(args,1)
    ids=_score_generation(args,1)
    generate._run_stage(args,2)
    saved=database.crem_generation(args.db_path,2)
    assert saved['state']=='done' and saved['population_count']>0
    assert [p['compound_id'] for p in saved['plan']['parents']]==list(reversed(ids))
    with database.connect(args.db_path) as conn:
        histories=[json.loads(r[0]) for r in conn.execute('SELECT history FROM crem_compound_pose WHERE iteration=2')]
    assert histories and all(h['constraint_reference']['compound_id'] in ids for h in histories)
    assert calls==[1,2]
    # Completed generation skips allocation and all fresh fingerprint checks.
    generate._run_stage(args,2)
    assert calls==[1,2]


@pytest.mark.parametrize('fault',['missing_completion','native_failure'])
def test_integrated_failure_retains_results_and_retries_without_duplicate_import(
        pipeline_project,tmp_path,monkeypatch,fault):
    args=pipeline_project
    slurm_config(args,tmp_path)
    passes=[]
    @contextmanager
    def allocated(cfg,flags):
        index=len(passes)
        passes.append(index)
        cfg['slurm-allocated-hosts']={socket.gethostname():2}
        session=SimpleNamespace(result=None)
        with node(cfg,tmp_path/f'native-{index}',monkeypatch):
            with monkeypatch.context() as patch:
                if index==0 and fault=='missing_completion':
                    original=slurm.SlurmSearch.exchange
                    def incomplete(self,request,*a,**kw):
                        result=original(self,request,*a,**kw)
                        return {} if request.get('kind')=='batch' else result
                    patch.setattr(slurm.SlurmSearch,'exchange',incomplete)
                yield session
        if index==0 and fault=='native_failure':
            raise RuntimeError('Native allocation failed; results retained')
    monkeypatch.setattr(slurm,'allocation',allocated)
    with pytest.raises(RuntimeError,match='incomplete|allocation failed'):
        generate._run_stage(args,1)
    with database.connect(args.db_path) as conn:
        assert conn.execute('SELECT count(*) FROM compound WHERE iteration=1').fetchone()[0]==0
    db=args.output/'iter01/02-generate/crem/combichem3d.sqlite'
    attempts=database.growth_run_info(db)['attempted']
    assert attempts>0
    generate._run_stage(args,1)
    assert database.crem_generation(args.db_path,1)['state']=='done'
    rows=database.growth_rows(db,'combichem3d_attempt')
    assert len({(r['parent_pose'],r['ordinal']) for r in rows})==len(rows)
    assert len(rows)>=attempts
    with database.connect(args.db_path) as conn:
        count=conn.execute('SELECT count(*) FROM compound WHERE iteration=1').fetchone()[0]
        assert count==database.crem_generation(args.db_path,1)['population_count']>0


@pytest.mark.skipif(os.environ.get('GRAFT_TEST_SLURM')!='1',reason='Explicit real Slurm smoke only')
def test_native_allocation_smoke(pipeline_project,tmp_path):
    args=pipeline_project
    slurm_config(args,tmp_path)
    generate._run_stage(args,1)
    saved=database.crem_generation(args.db_path,1)
    assert saved['state']=='done' and saved['population_count']>0
    root=args.output/'iter01/02-generate/crem/slurm'
    sessions=list(root.glob('run-*'))
    assert len(sessions)==1
    receipt=json.loads((sessions[0]/'completed.json').read_text())
    assert receipt['stop_reason']=='exhausted' and receipt['attempted']>0
    assert receipt['coordinator']==socket.gethostname()
    generate._run_stage(args,1)
    assert len(list(root.glob('run-*')))==1
    print('NATIVE_SLURM_SMOKE='+json.dumps(dict(receipt=receipt,population=saved['population_count'],
        session=str(sessions[0])),sort_keys=True))
