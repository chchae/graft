"""Characterize current package-local JobId JSON publication and failure behavior."""
import json
import os
from pathlib import Path

import pytest

from graft import prime_gbsa_async, ligprep_recovery, glide_recovery


@pytest.fixture(params=[prime_gbsa_async, ligprep_recovery, glide_recovery], ids=['prime', 'ligprep', 'glide'])
def writer(request):
    module = request.param
    if module is glide_recovery:
        return module.save_record, module.record_file
    return module._save, module.job_file


def test_job_record_bytes_and_publication_order(writer, tmp_path, monkeypatch):
    save, job_file = writer
    path = job_file(tmp_path)
    path.write_text('previous durable JobId')
    record = {'job_id': 'saved-123', 'name': '한글', 'command': ['native', '-HOST', 'host'], 'done': False}
    expected = json.dumps(record)
    events = []
    original_write, original_replace = Path.write_text, Path.replace

    def write(target, text, *args, **kwargs):
        events.append(('write', target, text, args, kwargs))
        assert path.read_text() == 'previous durable JobId'
        return original_write(target, text, *args, **kwargs)

    def replace(target, destination):
        events.append(('replace', target, destination))
        assert target.read_text() == expected
        assert destination.read_text() == 'previous durable JobId'
        return original_replace(target, destination)

    def unexpected_fsync(*args):
        pytest.fail('These existing writers do not fsync')

    monkeypatch.setattr(Path, 'write_text', write)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(os, 'fsync', unexpected_fsync)
    save(tmp_path, record)
    assert events == [('write', path.with_suffix('.tmp'), expected, (), {}),
                      ('replace', path.with_suffix('.tmp'), path)]
    assert path.read_bytes() == expected.encode()
    assert not path.with_suffix('.tmp').exists()


@pytest.mark.parametrize('operation', ['write_text', 'replace'])
def test_job_record_io_failure_preserves_saved_identity(writer, tmp_path, monkeypatch, operation):
    save, job_file = writer
    path = job_file(tmp_path)
    path.write_text('saved native JobId')
    failure = OSError('injected publication failure')

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(OSError) as caught:
        save(tmp_path, {'job_id': 'replacement'})
    assert caught.value is failure
    assert path.read_text() == 'saved native JobId'
    assert path.with_suffix('.tmp').exists() == (operation == 'replace')


def test_job_record_serialization_failure_does_not_touch_files(writer, tmp_path):
    save, job_file = writer
    path = job_file(tmp_path)
    path.write_text('saved native JobId')
    with pytest.raises(TypeError):
        save(tmp_path, {'unserializable': object()})
    assert path.read_text() == 'saved native JobId'
    assert not path.with_suffix('.tmp').exists()


def test_job_record_missing_directory_is_not_created(writer, tmp_path):
    save, _ = writer
    directory = tmp_path/'absent'
    with pytest.raises(FileNotFoundError):
        save(directory, {'job_id': 'new'})
    assert not directory.exists()
