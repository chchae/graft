from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core
from graft.config import prime_gbsa_job_options, schrodinger_cpu_settings, schrodinger_job_options
from graft.config import glide_job_options


@pytest.mark.parametrize('count',[1,49,50,51,99,100,101,5000,10001])
@pytest.mark.parametrize('host',['slurm-cpu:1','slurm-cpu:8','slurm-cpu:128','a:4 b:8','slurm-cpu'])
def test_glide_subjobs_cap_prepared_structures_without_increasing_host_budget(count,host):
    options=glide_job_options(host,count)
    assert options[options.index('-HOST')+1]==host
    njobs=int(options[options.index('-NJOBS')+1])
    assert 1 <= njobs <= count
    assert (count+njobs-1)//njobs <= 100
    if host=='slurm-cpu:1' or host=='slurm-cpu':
        assert njobs == (count+99)//100
    if ':' in host:
        slots=sum(int(entry.rsplit(':',1)[1]) for entry in host.split())
        assert njobs >= min(slots,count)


@pytest.mark.parametrize('count',[0,-1,True,1.5,'50'])
def test_invalid_glide_structure_counts(count):
    with pytest.raises(ValueError,match='ligand_count'):
        glide_job_options('slurm-cpu:8',count)


def test_glide_submission_splits_large_group_into_more_jobs_than_cpu_slots(tmp_path,monkeypatch):
    from graft import glide_recovery
    calls=[]
    monkeypatch.setattr(glide_recovery,'submit',lambda suite,cmd,folder:calls.append(cmd) or 'job')
    _core.submit_glide(Path('/suite'),tmp_path/'glide.in',tmp_path,'slurm-cpu:8',ligand_count=5000)
    command=calls[0]
    assert command[-5:]==['-HOST','slurm-cpu:8','-NJOBS','50','-OVERWRITE']
    assert '-WAIT' not in command


@pytest.mark.parametrize("stage", ["ligprep", "glide", "prime_gbsa"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "8"])
def test_invalid_cpu_settings(stage, value):
    with pytest.raises(ValueError, match=f"{stage.replace('_', '-')}-cpus"):
        schrodinger_cpu_settings({"host": "compute:128", f"{stage.replace('_', '-')}-cpus": value})


def test_config_loads_cpu_overrides(tmp_path):
    from test_project_config import PROJECT_TOML
    path = tmp_path / "project.toml"
    path.write_text(PROJECT_TOML + '\nligprep-cpus = 8\nglide-cpus = 16\nprime-gbsa-cpus = 4\n')
    settings = _core.load_project_toml(path)
    assert [settings[f"{s}_cpus"] for s in ("ligprep", "glide", "prime_gbsa")] == [8, 16, 4]
    assert schrodinger_cpu_settings({"host": "localhost"}) == {
        "ligprep_cpus": None, "glide_cpus": None, "prime_gbsa_cpus": None,
    }


@pytest.mark.parametrize("host,expected", [
    ("t41-cpu:16", "t41-cpu:7"),
    ("t41-cpu:128", "t41-cpu:7"),
    ("localhost", "localhost:7"),
    ("a:4 b:8", "a:4 b:3"),
])
@pytest.mark.parametrize("ligand_count", [1, 7, 8, 200])
def test_prime_gbsa_queues_each_ligand_with_fixed_host_slots(host, expected, ligand_count):
    assert prime_gbsa_job_options(host, ligand_count) == ["-HOST", expected, "-NJOBS", str(ligand_count)]


def test_host_defaults_and_multiple_hosts():
    assert schrodinger_job_options("localhost", split_jobs=True) == ["-HOST", "localhost"]
    assert schrodinger_job_options("a:4 b:8", split_jobs=True) == [
        "-HOST", "a:4 b:8", "-NJOBS", "12",
    ]
    with pytest.raises(ValueError, match="single"):
        schrodinger_cpu_settings({"host": "a:4 b:8", "glide-cpus": 2})


@pytest.mark.parametrize("cpus,host,njobs", [(None, "compute:128", "128"), (8, "compute:8", "8")])
def test_ligprep_splits_jobs_preserving_scientific_options(monkeypatch, tmp_path, cpus, host, njobs):
    calls = []

    def run(command, cwd):
        calls.append(command)
        (cwd / "ligprep.maegz").write_text("prepared")

    monkeypatch.setattr(_core, "run_command", run)
    _core.run_ligprep_once(Path("/suite"), tmp_path / "input.smi", tmp_path,
                           "compute:128", cpus=cpus)
    command = calls[0]
    assert command[-5:] == ["-HOST", host, "-NJOBS", njobs, "-WAIT"]
    assert "-We,-ph,7.4,-pht,1.0,-ms,2" in command
    assert command[command.index("-s") + 1] == "1"
    assert "-r" not in command


@pytest.mark.parametrize("source_host,cpus,host,njobs", [
    ("compute:128", None, "compute:128", "128"),
    ("slurm-cpu", 124, "slurm-cpu:124", "124"),
    ("compute:128", 8, "compute:8", "8"),
    ("a:4 b:8", None, "a:4 b:8", "12"),
    ("localhost", None, "localhost", None),
])
def test_glide_splits_reference_groups_and_remains_asynchronous(monkeypatch, tmp_path, source_host, cpus, host, njobs):
    from graft import schrodinger_guard
    monkeypatch.setattr(schrodinger_guard, "active_jobs", lambda *a: [])
    from graft import glide_recovery
    monkeypatch.setattr(glide_recovery, "active_jobs", lambda *a: [])
    (tmp_path / "dock.in").write_text("grid")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="JobId: test-glide\n", stderr="")

    monkeypatch.setattr(_core.subprocess, "run", run)
    _core.submit_glide(Path("/suite"), tmp_path / "dock.in", tmp_path,
                       source_host, cpus=cpus)
    if njobs:
        assert calls[0][-5:] == ["-HOST", host, "-NJOBS", njobs, "-OVERWRITE"]
    else:
        assert calls[0][-3:] == ["-HOST", host, "-OVERWRITE"]
        assert "-NJOBS" not in calls[0]
    assert "-WAIT" not in calls[0]


def test_ligprep_native_slurm_command_keeps_scientific_defaults(monkeypatch,tmp_path):
    calls=[]
    def run(command,cwd):
        calls.append(command)
        (cwd/'ligprep.maegz').write_text('prepared')
    monkeypatch.setattr(_core,'run_command',run)
    _core.run_ligprep_once(Path('/suite'),tmp_path/'input.smi',tmp_path,'slurm-cpu',cpus=124)
    command=calls[0]
    assert command[-5:]==['-HOST','slurm-cpu:124','-NJOBS','124','-WAIT']
    assert '-We,-ph,7.4,-pht,1.0,-ms,2' in command
    assert command[command.index('-s')+1]=='1'
    assert '-r' not in command


@pytest.mark.parametrize('child_status',['RUNNING','DONE'])
def test_ligprep_recovers_original_host_after_enabling_slurm(tmp_path,monkeypatch,child_status):
    import json
    from graft import ligprep_recovery
    old=['/old-suite/ligprep','-HOST','t41-cpu:8','-NJOBS','8','-WAIT']
    saved=dict(command=old,compound_ids=[1],job_id='original')
    ligprep_recovery._save(tmp_path,saved)
    output=tmp_path/'ligprep.maegz'
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        assert command[0]=='/old-suite/jsc'
        if command[1]=='info':
            return SimpleNamespace(stdout='\n'.join(json.dumps(j) for j in [
                dict(jobId='original',status='DONE'),dict(jobId='child',status=child_status)]))
        assert command[1]=='download'
        output.write_text('downloaded from original JobId')
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(ligprep_recovery.subprocess,'run',run)
    monkeypatch.setattr(ligprep_recovery.subprocess,'Popen',lambda *a,**k:pytest.fail('resubmitted pinned LigPrep'))
    result=ligprep_recovery.run_or_recover(['/new-suite/ligprep','-HOST','slurm-cpu:124'],tmp_path,[1],output)
    assert result==(child_status=='DONE')
    assert ligprep_recovery.load_job(tmp_path)==saved
    assert len(calls)==(2 if child_status=='DONE' else 1)


def test_slurm_cpu_validation_uses_effective_stage_host():
    settings=schrodinger_cpu_settings({'host':'old-a:4 old-b:4','use-slurm':True,
                                       'ligprep-cpus':124,'glide-cpus':124})
    assert settings['ligprep_cpus']==settings['glide_cpus']==124
    with pytest.raises(ValueError,match='single compute host'):
        schrodinger_cpu_settings({'host':'old:8','use-slurm':True,
                                 'glide-host':'a:4 b:4','glide-cpus':8})
