"""Reference-numbered native names must retain output and restart identity."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from graft import _core, glide_recovery as recovery, schrodinger_guard as guard
from graft.stages import glide


@pytest.mark.parametrize('number,basename', [(1, 'glide_01'), (3, 'glide_03'), (8, 'glide_08'),
                                            (23, 'glide_23'), (100, 'glide_100')])
def test_named_input_survives_launch_archiving_and_controls_result_paths(
        tmp_path, monkeypatch, number, basename):
    folder = tmp_path/f'ref_{number}'
    folder.mkdir()
    (folder/'glide.log').write_text('old terminal log')
    inp = _core.write_constrained_glide_input(folder, Path('grid.zip'), Path('ligands.maegz'),
                                              Path('reference.maegz'), 'CC', '1,2')
    assert inp.name == f'{basename}.in'
    assert 'PRECISION SP' in inp.read_text()
    assert 'CORE_ATOMS 1,2' in inp.read_text()
    assert 'CORECONS_FALLBACK False' in inp.read_text()
    monkeypatch.setattr(guard, 'active_jobs', lambda *a: [])
    def launch(command, **kw):
        assert Path(command[1]) == inp and inp.exists()
        assert recovery.load_record(folder)['job_id'] is None
        assert not (folder/'glide.log').exists()
        assert '-WAIT' not in command
        return SimpleNamespace(stdout='JobId: named-job\n', stderr='')
    monkeypatch.setattr(recovery.subprocess, 'run', launch)
    job = _core.submit_glide(Path('/suite'), inp, folder, 'slurm-cpu:8', ligand_count=51)
    assert job['log_file'] == folder/f'{basename}.log'
    assert job['output_file'] == folder/f'{basename}_lib.maegz'
    assert recovery.output_paths(folder) == (job['log_file'], job['output_file'])
    assert recovery.load_record(folder)['job_id'] == 'named-job'
    assert len(list(folder.glob('previous-*/glide.log'))) == 1
    assert inp.exists()


@pytest.mark.parametrize('basename', ['glide', 'glide_ref_08', 'glide_08'])
def test_saved_command_selects_original_output_names_and_checks_live_children(tmp_path, monkeypatch, basename):
    folder = tmp_path/'ref_8'
    folder.mkdir()
    recovery.save_record(folder, dict(job_id='original', downloaded=False,
                                      command=['/suite/glide', str(folder/f'{basename}.in')]))
    # A newly prepared input or another cached log cannot rename saved work.
    (folder/'glide_08.in').write_text('new input')
    (folder/'glide_ref_08.log').write_text('cached log')
    (folder/'glide.log').write_text('legacy log')
    log, output = recovery.output_paths(folder)
    assert log.name == f'{basename}.log'
    assert output.name == f'{basename}_lib.maegz'
    jobs = [dict(jobId='original', status='DONE'), dict(jobId='child', status='RUNNING')]
    monkeypatch.setattr(recovery, 'job_details', lambda *a: jobs)
    downloads = []
    monkeypatch.setattr(recovery.subprocess, 'run', lambda command, **kw: downloads.append(command))
    assert recovery.group_status('/suite', folder, log, output, []) == 'RUNNING'
    assert downloads == []
    jobs[1]['status'] = 'DONE'
    assert recovery.group_status('/suite', folder, log, output, []) == 'DONE'
    assert downloads == [['/suite/jsc', 'download', '--cwd', 'original']]
    assert recovery.output_paths(folder) == (log, output)


@pytest.mark.parametrize('basename', ['glide_ref_08', 'glide_08'])
def test_lost_record_recovers_named_log_jobid_without_resubmission(tmp_path, monkeypatch, basename):
    folder = tmp_path/'ref_8'
    folder.mkdir()
    log = folder/f'{basename}.log'
    log.write_text('JobId : recovered-native\n')
    output = folder/f'{basename}_lib.maegz'
    assert recovery.output_paths(folder) == (log, output)
    monkeypatch.setattr(recovery, 'job_details',
                        lambda *a: [dict(jobId='recovered-native', status='DONE')])
    calls = []
    monkeypatch.setattr(recovery.subprocess, 'run', lambda command, **kw: calls.append(command))
    assert recovery.group_status('/suite', folder, log, output, []) == 'DONE'
    saved = recovery.load_record(folder)
    assert saved['job_id'] == 'recovered-native' and saved['job_name'] == basename
    assert recovery.output_paths(folder) == (log, output)
    assert calls == [['/suite/jsc', 'download', '--cwd', 'recovered-native']]


def test_collector_refreshes_recovered_native_name_before_import(tmp_path, monkeypatch):
    folder = tmp_path/'ref_8'
    folder.mkdir()
    job = dict(reference_id=8, group_dir=folder, log_file=folder/'glide.log',
               output_file=folder/'glide_lib.maegz')
    monkeypatch.setattr(glide, 'active_jobs', lambda *a: [])
    def status(*a, **kw):
        recovery.save_record(folder, dict(job_id='recovered-native', job_name='glide_ref_08'))
        (folder/'glide_ref_08_lib.maegz').write_text('verified downloaded poses')
        return 'DONE'
    monkeypatch.setattr(glide, 'group_status', status)
    imported = []
    monkeypatch.setattr(glide, 'process_completed_job', lambda iteration, args, job, *rest:
                        imported.append(job['output_file'].read_text()))
    assert glide.collect_finished_groups(1, SimpleNamespace(schrodinger=Path('/suite')),
                                         [job], 'extractor', 'merger', tmp_path) == ([], 1)
    assert imported == ['verified downloaded poses']
    assert job['log_file'].name == 'glide_ref_08.log'
