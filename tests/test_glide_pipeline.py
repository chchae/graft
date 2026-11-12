"""Glide input batching, collector ownership and submission safety regressions."""
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from graft import _core, glide_inputs, glide_recovery, schrodinger_guard
from graft.stages import glide


@pytest.fixture
def structures(monkeypatch):
    first = SimpleNamespace(title=' REF ', label='first')
    refs = [first, SimpleNamespace(title='REF', label='second'),
            SimpleNamespace(title='OTHER', label='other')]
    reads, matches, writes = [], [], []

    class Reader:
        def __init__(self, path):
            reads.append(path)
        def __enter__(self):
            return iter(refs)
        def __exit__(self, *args):
            pass

    class Writer:
        def __init__(self, path):
            self.path = Path(path)
        def __enter__(self):
            return self
        def append(self, st):
            writes.append(st.label)
            self.path.write_text(st.label)
        def __exit__(self, *args):
            pass

    def evaluate(st, smarts, first_match):
        matches.append((st.label, smarts, first_match))
        return [[9, 3, 7], [1, 2, 4]]

    monkeypatch.setitem(sys.modules, 'schrodinger', SimpleNamespace(
        structure=SimpleNamespace(StructureReader=Reader, StructureWriter=Writer),
        adapter=SimpleNamespace(evaluate_smarts=evaluate)))
    return refs, reads, matches, writes


@pytest.mark.parametrize('smarts', [None, 'c1ccccc1'])
@pytest.mark.parametrize('point', [None, [0,0,0]])
def test_batch_uses_one_process_first_reference_and_native_atom_order(
        tmp_path, monkeypatch, structures, smarts, capsys, point):
    refs, reads, matches, writes = structures
    entries = []
    # Two groups may share a reference title; retain the old first-pose rule.
    for refid, name in [(1, 'REF'), (2, 'OTHER'), (3, 'REF')]:
        folder = tmp_path / f'ref_{refid}'
        folder.mkdir()
        entries.append((dict(reference_id=refid, reference_name=name,
                             group_dir=folder, reference_file=folder/'_reference.maegz'),
                        folder/'ligprep_group.maegz'))
    calls = []
    def run(command, cwd):
        calls.append(command)
        monkeypatch.setattr(sys, 'argv', [str(command[1]), str(command[2])])
        runpy.run_path(str(command[1]), run_name='__main__')
    monkeypatch.setattr(_core, 'run_command', run)
    args = SimpleNamespace(schrodinger=Path('/suite'), reference_poses=tmp_path/'refs.maegz',
                           mcs_smarts=smarts, grid=tmp_path/'grid.zip', glide_pose_point=point)
    inputs = glide.prepare_new_inputs(args, entries, tmp_path)
    assert len(calls) == len(reads) == 1
    assert set(inputs) == {1, 2, 3}
    assert writes == ['first', 'other', 'first']
    for path in inputs.values():
        text = path.read_text()
        assert 'PRECISION SP' in text
        assert f'POSES_PER_LIG {10 if point is not None else 1}' in text
        if smarts:
            assert 'CORE_ATOMS 9,3,7' in text
            assert 'CORECONS_FALLBACK False' in text
        else:
            assert 'CORE_ATOMS' not in text
    assert matches == ([(name, smarts, True) for name in writes] if smarts else [])
    assert 'prepare 3 reference inputs=' in capsys.readouterr().out
    timing = [json.loads(line) for line in (tmp_path/'glide_timing.jsonl').read_text().splitlines()]
    assert timing[0]['phase'] == 'prepare_reference_inputs' and timing[0]['seconds'] >= 0


@pytest.mark.parametrize('problem', ['missing_reference', 'unmatched_core'])
def test_invalid_batch_preserves_existing_inputs_before_any_publication(
        tmp_path, monkeypatch, structures, problem):
    refs, reads, matches, writes = structures
    outputs = [tmp_path/'one.maegz', tmp_path/'two.maegz']
    for output in outputs:
        output.write_text('retained')
    if problem == 'unmatched_core':
        import schrodinger
        monkeypatch.setattr(schrodinger.adapter, 'evaluate_smarts',
                            lambda st, *a: [[1, 2]] if st.label == 'first' else [])
    request = dict(references='refs.maegz', smarts='CC', groups=[
        dict(id=1, name='REF', output=str(outputs[0])),
        dict(id=2, name='MISSING' if problem == 'missing_reference' else 'OTHER', output=str(outputs[1]))])
    with pytest.raises(RuntimeError, match='not found|does not match'):
        glide_inputs.prepare(request)
    assert writes == []
    assert [p.read_text() for p in outputs] == ['retained', 'retained']


def test_batch_rejects_stale_or_incomplete_helper_report(tmp_path, monkeypatch):
    result = tmp_path/'new_reference_cores.json'
    result.write_text(json.dumps({'1': '9,3,7'}))
    folder = tmp_path/'ref_1'
    folder.mkdir()
    reference = folder/'_reference.maegz'
    reference.write_text('retained')
    args = SimpleNamespace(schrodinger=Path('/suite'), reference_poses=tmp_path/'refs.maegz',
                           mcs_smarts='CC', grid=tmp_path/'grid.zip')
    entries = [(dict(reference_id=1, reference_name='REF', group_dir=folder,
                     reference_file=reference), folder/'ligprep_group.maegz')]
    def incomplete(*a, **kw):
        assert not result.exists()
        result.write_text('{}')
    monkeypatch.setattr(_core, 'run_command', incomplete)
    with pytest.raises(RuntimeError, match='incomplete group coverage'):
        glide.prepare_new_inputs(args, entries, tmp_path)
    assert not (folder/'glide.in').exists()
    assert reference.read_text() == 'retained'


def test_glide_launch_reuses_only_current_locked_safety_snapshot(tmp_path, monkeypatch):
    queries = []
    def active(suite):
        queries.append(suite)
        return []
    monkeypatch.setattr(schrodinger_guard, 'active_jobs', active)
    monkeypatch.setattr(glide_recovery, 'active_jobs',
                        lambda *a: pytest.fail('Duplicate scan inside guarded submission'))
    def launch(command, **kw):
        assert glide_recovery.load_record(kw['cwd'])['job_id'] is None
        return SimpleNamespace(stdout='JobId: native\n', stderr='')
    monkeypatch.setattr(glide_recovery.subprocess, 'run', launch)
    for name in ('ref_1', 'ref_2'):
        folder = tmp_path/name
        folder.mkdir()
        assert glide_recovery.submit('/suite', ['/suite/glide', 'glide.in'], folder) == 'native'
    assert queries == ['/suite', '/suite']  # Fresh safety query for each launch.
    for name in ('ref_1', 'ref_2'):
        entries = [json.loads(line) for line in (tmp_path/name/'glide_timing.jsonl').read_text().splitlines()]
        assert [entry['phase'] for entry in entries] == ['safety_recovery', 'native_launch']


def test_collector_error_reaches_submitter_and_retains_native_identity(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from threading import Event

    folder = tmp_path/'ref_1'
    folder.mkdir()
    glide_recovery.save_record(folder, dict(job_id='saved-native', downloaded=False))
    before = (folder/'glide_job.json').read_bytes()
    job = dict(group_dir=folder)
    incoming, done, wake = Queue(), Event(), Event()
    incoming.put(job)
    def unavailable(iteration, args, jobs, *rest):
        assert jobs == [job]
        raise OSError('native query unavailable')
    monkeypatch.setattr(glide, 'collect_finished_groups', unavailable)
    args = SimpleNamespace(poll_interval=30)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(glide.collect_during_submissions, 1, args, [], incoming,
                             done, wake, 'extractor', 'merger', tmp_path)
        with pytest.raises(OSError, match='native query unavailable'):
            future.result(timeout=5)
    assert (folder/'glide_job.json').read_bytes() == before
