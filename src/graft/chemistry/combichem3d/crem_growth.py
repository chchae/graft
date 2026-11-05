"""CReM single-addition plans with fixed-core, branch-budget provenance.

CReM selects chemically compatible fragments. Existing assembly/search retain
UIDs and validate the full joint pose; CReM coordinates are never trusted.
"""
import hashlib
import inspect
import sqlite3
import time
from importlib.metadata import version
from pathlib import Path

from rdkit import Chem

from graft import database
from graft.states import PoseVerdict
from graft.chemistry.combichem3d import history as provenance
from graft.chemistry.combichem3d.assembly import assemble
from graft.chemistry.combichem3d.attachment_policy import growth_available, root_for_site
from graft.chemistry.combichem3d.logging_utils import logger
from graft.chemistry.combichem3d.search import methyl_probe


def validate_database(cfg):
    """Read-only validation; do not initialize or annotate the supplied library."""
    try:
        from crem.crem import grow_mol
    except ImportError as exc:
        raise ValueError('Install the CReM backend with: python -m pip install -e ".[crem]"') from exc
    required={'protected_ids','replace_ids','return_mol','seed','ncores','symmetry_fixes'}
    if not required <= set(inspect.signature(grow_mol).parameters):
        raise ValueError('Installed CReM lacks required grow_mol options; install crem>=0.3.2')
    path=Path(cfg['crem-db'])
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        table=f'radius{cfg["crem-radius"]}'
        if not conn.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone():
            raise ValueError(f'CReM database has no {table} context table')
    return version('crem')


def candidate_sites(parent, history):
    """One logical site per H-bearing heavy atom, covering every active branch."""
    core={row['uid'] for row in history['core']}
    anchors=set(history['attachments'])
    result=[]
    for atom in parent.GetAtoms():
        if atom.GetAtomicNum()<=1 or atom.GetTotalNumHs(includeNeighbors=True)<1:
            continue
        uid=atom.GetProp('_mn_uid')
        if uid in core and uid not in anchors:
            continue
        if growth_available(history,root_for_site(history,uid)):
            result.append(uid)
    return sorted(result)


def replacement_fragment(generated, parent, site):
    """Extract exactly one new single-ended component, checking retained UIDs.

    CReM >=0.3.2 marks inserted atoms with __crem. Fail closed if an older or
    incompatible implementation loses lineage or changes another attachment.
    """
    mol=Chem.RemoveHs(Chem.Mol(generated))
    added={a.GetIdx() for a in mol.GetAtoms() if a.HasProp('__crem') and a.GetBoolProp('__crem')}
    old={a.GetProp('_mn_uid') for a in parent.GetAtoms() if a.GetAtomicNum()>1}
    retained=[a for a in mol.GetAtoms() if a.GetIdx() not in added]
    if not added or any(not a.HasProp('_mn_uid') for a in retained) or {a.GetProp('_mn_uid') for a in retained}!=old or len(retained)!=len(old):
        raise ValueError('CReM did not preserve the parent atom identities/new-atom markers')
    cuts=[b for b in mol.GetBonds() if (b.GetBeginAtomIdx() in added)!=(b.GetEndAtomIdx() in added)]
    if len(cuts)!=1 or cuts[0].GetBondType()!=Chem.BondType.SINGLE:
        raise ValueError('CReM growth must add one single-bonded fragment')
    cut=cuts[0]
    anchor=cut.GetBeginAtom() if cut.GetBeginAtomIdx() not in added else cut.GetEndAtom()
    if anchor.GetProp('_mn_uid')!=site:
        raise ValueError('CReM modified a protected or unselected atom')
    pieces=Chem.GetMolFrags(Chem.FragmentOnBonds(mol,[cut.GetIdx()],dummyLabels=[(0,0)]),asMols=True)
    fragments=[m for m in pieces if sum(a.GetAtomicNum()>1 for a in m.GetAtoms())==len(added)
               and all(a.GetAtomicNum()<=1 or a.HasProp('__crem') and a.GetBoolProp('__crem') for a in m.GetAtoms())]
    if len(fragments)!=1:
        raise ValueError('Cannot identify one connected CReM replacement fragment')
    fragment=fragments[0]
    for atom in fragment.GetAtoms():
        atom.SetAtomMapNum(0)
    return Chem.MolToSmiles(fragment,isomericSmiles=True)


def site_candidates(parent, history, uid, cfg):
    from crem.crem import grow_mol
    seed=int.from_bytes(hashlib.sha256(f'{cfg["seed"]}:{history["lineage_id"]}:{uid}'.encode()).digest()[:4],'big')%2147483646
    molecule=Chem.RemoveHs(Chem.Mol(parent))
    for atom in molecule.GetAtoms():
        if atom.HasProp('__crem'):
            atom.ClearProp('__crem')
    index=next(a.GetIdx() for a in molecule.GetAtoms() if a.GetProp('_mn_uid')==uid)
    protected=[a.GetIdx() for a in molecule.GetAtoms() if a.GetIdx()!=index]
    rows=grow_mol(molecule,cfg['crem-db'],radius=cfg['crem-radius'],
                  min_atoms=cfg['sidechain-min-heavy-atoms'],max_atoms=cfg['sidechain-max-heavy-atoms'],
                  max_replacements=cfg['sidechains-per-site'],min_freq=cfg['crem-min-freq'],
                  replace_ids=[index],protected_ids=protected,symmetry_fixes=True,
                  return_mol=True,ncores=1,seed=seed)
    choices={}
    for row in rows:
        fragment=replacement_fragment(row[-1],molecule,uid)
        expected=provenance.canonical_smiles(row[-1])
        product,_=assemble(parent,history,[uid],[fragment],'crem-validation')
        if provenance.canonical_smiles(product)!=expected:
            raise ValueError('CReM replacement failed exact isomeric reassembly')
        choices.setdefault(fragment,dict(site=uid,fragment=fragment,crem_smiles=expected))
        if len(choices)>=cfg['sidechains-per-site']:
            break
    return [choices[key] for key in sorted(choices)]


def plan_parent(db, job, cfg, checker, deadline):
    """Persist independent probe decisions before requesting each site's fragments."""
    row=database.growth_parent(db,job['parent_pose'])
    parent=Chem.MolFromMolBlock(row['molblock'],removeHs=False)
    history=row['history']
    parent=provenance.restore(parent,history)
    reports=[]
    operations=[]
    sites=candidate_sites(parent,history)
    for uid in sites:
        if database.growth_should_stop(db) or time.time()>=deadline:
            return
        report=row['probes'].get(uid)
        if report is None or report.get('budget_interrupted'):
            seed=int.from_bytes(hashlib.sha256(f'{cfg["seed"]}:probe:{history["lineage_id"]}:{uid}'.encode()).digest()[:4],'big')%2147483646
            result=methyl_probe(parent,history,uid,checker,cfg,seed,deadline)
            report={key:value for key,value in result.items() if key!='pose'}
            report.update(root_attachment=root_for_site(history,uid),gates_enumeration=True,
                          budget_interrupted=time.time()>=deadline)
            database.growth_save_probe(db,job['parent_pose'],uid,report)
        if report.get('budget_interrupted'):
            return
        if report['status']==PoseVerdict.PASS:
            if 'crem_candidates' not in report:
                try:
                    report['crem_candidates']=site_candidates(parent,history,uid,cfg)
                except (ValueError,RuntimeError,sqlite3.Error) as exc:
                    report.update(status=PoseVerdict.UNRESOLVED,reason=str(exc),failed_stage='crem_generation')
                database.growth_save_probe(db,job['parent_pose'],uid,report)
            operations.extend(report.get('crem_candidates',[]))
        reports.append((uid,report))
    plan=dict(kind='crem_single_addition',operations=operations,total=len(operations),
              sites=sites,pools=[],reason='One fragment per child at a methyl-PASS site; fixed original frame')
    database.growth_save_plan(db,job['parent_pose'],plan,reports)
    logger.info('CReM parent_pose=%s cycle=%s eligible_sites=%s probe_PASS=%s operations=%s growth_counts=%s',
                job['parent_pose'],history['iteration']+1,len(sites),
                sum(report['status']==PoseVerdict.PASS for _,report in reports),len(operations),history['growth_counts'])
