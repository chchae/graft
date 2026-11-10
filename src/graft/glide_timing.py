"""Persist operational Glide durations independently of console verbosity."""
import json
from pathlib import Path
from threading import Lock
import time

_lock = Lock()


def record(directory, phase, seconds):
    entry = dict(time=time.time(), phase=phase, seconds=max(0, seconds))
    # A single collector and submitter may log to the same directory.
    with _lock:
        with (Path(directory) / 'glide_timing.jsonl').open('a') as stream:
            stream.write(json.dumps(entry) + '\n')
