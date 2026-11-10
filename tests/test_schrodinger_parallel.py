from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core
from graft.config import prime_gbsa_job_options, schrodinger_cpu_settings, schrodinger_job_options
from graft.config import glide_job_options, ligprep_job_options


@pytest.mark.parametrize('count', [1, 99, 100, 101, 799, 800, 801, 5000, 10001])
@pytest.mark.parametrize('host', ['slurm-cpu:1', 'slurm-cpu:8', 'slurm-cpu:384', 'a:4 b:8', 'slurm-cpu'])
def test_ligprep_subjobs_cap_original_compounds_preserving_host_budget(count, host):
    options = ligprep_job_options(host, count)
    assert options[options.index('-HOST') + 1] == host
    njobs = int(options[options.index('-NJOBS') + 1])
    assert 1 <= njobs <= count
    assert (count + njobs - 1) // njobs <= 100
    if ':' in host:
        slots = sum(int(entry.rsplit(':', 1)[1]) for entry in host.split())
        assert njobs >= min(slots, count)
    else:
        assert njobs == (count + 99) // 100
    assert '-NSTRUCTS' not in options  # Native LigPrep rejects NJOBS + NSTRUCTS together.


@pytest.mark.parametrize('count', [0, -1, True, 1.5, '100'])
def test_ligprep_rejects_invalid_compound_counts(count):
    with pytest.raises(ValueError, match='compound_count'):
        ligprep_job_options('slurm-cpu:8', count)


@pytest.mark.parametrize('durable', [True, False])
@pytest.mark.parametrize('host,cpus,expected_host,njobs', [
    ('slurm-cpu:8', None, 'slurm-cpu:8', 50),
    ('slurm-cpu', None, 'slurm-cpu', 50),
    ('slurm-cpu', 128, 'slurm-cpu:128', 128),
])
def test_actual_ligprep_command_caps_subjobs_for_all_submission_paths(
        tmp_path, monkeypatch, durable, host, cpus, expected_host, njobs):
    from graft import ligprep_recovery
    input_file = tmp_path/'input.smi'
    ids = list(range(1, 5001))
    input_file.write_text(''.join(f'C\tCMPID_{cid}\n' for cid in ids) + '\n')
    calls = []
    def execute(command, cwd):
        calls.append(command)
        (cwd/'ligprep.maegz').write_text('prepared')
    def recover(command, folder, compound_ids, output):
        assert compound_ids == ids
        execute(command, folder)
        return True
    monkeypatch.setattr(_core, 'run_command', execute)
    monkeypatch.setattr(ligprep_recovery, 'run_or_recover', recover)
    assert _core.run_ligprep_once(Path('/suite'), input_file, tmp_path, host, cpus=cpus,
        compound_ids=ids if durable else None) == tmp_path/'ligprep.maegz'
    assert calls[0][-5:] == ['-HOST', expected_host, '-NJOBS', str(njobs), '-WAIT']
    assert '-We,-ph,7.4,-pht,1.0,-ms,2' in calls[0]
    assert calls[0][calls[0].index('-s') + 1] == '1'
    assert '-r' not in calls[0]


@pytest.mark.parametrize('count',[1,49,50,51,99,100,101,5000,10001])
@pytest.mark.parametrize('host',['slurm-cpu:1','slurm-cpu:8','slurm-cpu:128','a:4 b:8','slurm-cpu'])
def test_glide_subjobs_target_50_structures_independent_of_host_slots(count,host):
    options=glide_job_options(host,count)
    assert options[options.index('-HOST')+1]==host
    njobs=int(options[options.index('-NJOBS')+1])
    assert 1 <= njobs <= count
    assert (count+njobs-1)//njobs <= 50
    assert njobs == (count+49)//50


@pytest.mark.parametrize('count',[0,-1,True,1.5,'50'])
def test_invalid_glide_structure_counts(count):
    with pytest.raises(ValueError,match='ligand_count'):
        glide_job_options('slurm-cpu:8',count)


@pytest.mark.parametrize('count,njobs', [(293,6), (387,8), (254,6), (17,1)])
def test_glide_large_slot_allocation_does_not_create_tiny_subjobs(count, njobs):
    assert glide_job_options('slurm-cpu:128', count) == [
        '-HOST', 'slurm-cpu:128', '-NJOBS', str(njobs)]


def test_glide_cpu_override_changes_concurrency_without_changing_batch_size():
    assert glide_job_options('slurm-cpu:384', 293, cpus=8) == [
        '-HOST', 'slurm-cpu:8', '-NJOBS', '6']


def test_glide_submission_splits_large_group_into_more_jobs_than_cpu_slots(tmp_path,monkeypatch):
    from graft import glide_recovery
    calls=[]
    monkeypatch.setattr(glide_recovery,'submit',lambda suite,cmd,folder:calls.append(cmd) or 'job')
    _core.submit_glide(Path('/suite'),tmp_path/'glide.in',tmp_path,'slurm-cpu:8',ligand_count=5000)
    command=calls[0]
    assert command[-5:]==['-HOST','slurm-cpu:8','-NJOBS','100','-OVERWRITE']
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
    (tmp_path / 'input.smi').write_text(''.join(f'C\tCMPID_{i}\n' for i in range(500)))

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
    (tmp_path/'input.smi').write_text(''.join(f'C\tCMPID_{i}\n' for i in range(500)))
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
    result=_core.run_ligprep_once(Path('/new-suite'),tmp_path/'missing-original-input.smi',tmp_path,
                                  'slurm-cpu',cpus=124,compound_ids=[1])
    assert result==(output if child_status=='DONE' else None)
    assert ligprep_recovery.load_job(tmp_path)==saved
    assert len(calls)==(2 if child_status=='DONE' else 1)


def test_slurm_cpu_validation_uses_effective_stage_host():
    settings=schrodinger_cpu_settings({'host':'old-a:4 old-b:4','use-slurm':True,
                                       'ligprep-cpus':124,'glide-cpus':124})
    assert settings['ligprep_cpus']==settings['glide_cpus']==124
    with pytest.raises(ValueError,match='single compute host'):
        schrodinger_cpu_settings({'host':'old:8','use-slurm':True,
                                 'glide-host':'a:4 b:4','glide-cpus':8})
