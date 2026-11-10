"""Read-only completion summaries; native timings are distinct from throughput."""
import json
import math
from pathlib import Path
import re
import statistics

from graft.paths import iteration_directory, stage_in_iteration
from graft import database
from graft.states import CompoundState as State


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def md_pending_summary(args, iteration):
    """Last worker snapshot, counted per molecule rather than frame/replica.

    Calculated includes committed molecules; only a finite SQLite score proves
    DB persistence. MD-only completion remains part of the running ensemble.
    """
    selected = database.md_gbsa_selection(args.db_path, iteration)
    if selected is None:
        return ''
    rows = database.md_gbsa_rows(args.db_path, iteration)
    root = stage_in_iteration(iteration_directory(Path(args.output), iteration), 'md_gbsa')
    path = root/'progress.json'
    progress = _read_json(path) if path.exists() else {}
    counts = dict(queued=0, running=0, calculated=0, db_committed=0, failed=0, unknown=0)
    replicas = selected['settings'].get('replicates')
    valid_replicas = isinstance(replicas, int) and not isinstance(replicas, bool) and replicas > 0
    for cid in selected['ids']:
        row = rows.get(cid)
        if row and row[1] is not None and math.isfinite(row[1]):
            counts['db_committed'] += 1
            counts['calculated'] += 1
            continue
        if row and row[2:] == (State.FAILED, 'md_gbsa'):
            counts['failed'] += 1
            continue
        if row is None or not isinstance(progress, dict) or not valid_replicas:
            counts['unknown'] += 1
            continue
        records = [progress.get(str((cid, r)), {}) for r in range(replicas)]
        if not all(isinstance(v, dict) for v in records):
            counts['unknown'] += 1
        elif all(v.get('status') == 'done' for v in records):
            counts['calculated'] += 1
        elif all(v.get('status') in ('done', 'failed') for v in records):
            counts['failed'] += 1
        elif all(not v or v.get('status') == 'unstarted' for v in records):
            counts['queued'] += 1
        else:
            counts['running'] += 1
    fields = [f'{key}={counts[key]}' for key in ('queued', 'running', 'calculated', 'db_committed')]
    fields.append(f"db_pending={counts['calculated'] - counts['db_committed']}")
    fields.extend(f'{key}={counts[key]}' for key in ('failed', 'unknown') if counts[key])
    return f"compounds={len(selected['ids'])} (" + ', '.join(fields) + ')'


def _native_times(output, iteration, stage, successful):
    """One duration per original compound, from verified completed run records."""
    directory = iteration_directory(Path(output), iteration)
    times = {}
    if stage == 'prime_gbsa':
        root = stage_in_iteration(directory, 'prime_gbsa')
        for path in sorted(root.glob('queue-*.completed.json')):
            record = _read_json(path)
            if not isinstance(record, dict) or record.get('mode') != 'compound_queue_v1':
                continue
            run_dir = record.get('run_dir', '')
            entries = record.get('entries')
            if (not isinstance(run_dir, str) or not re.fullmatch(r'queue-\d+', run_dir)
                    or not isinstance(entries, dict)):
                continue
            for cid in successful - times.keys():
                if entries.get(str(cid)) != 'done':
                    continue
                try:
                    log = (root / run_dir / f'CMPID_{cid}' / 'prime_gbsa.log').read_text()
                except OSError:
                    continue
                matches = re.findall(
                    r'Total Time Elapsed \(hours:minutes:seconds\)\s+(\d+):(\d+):(\d+)', log)
                if len(matches) == 1:
                    h, m, s = map(int, matches[0])
                    times[cid] = h * 3600 + m * 60 + s
    elif stage == 'amber-gbsa':
        # Older remote workers did not record durations; never invent them.
        snapshot = _read_json(stage_in_iteration(directory, 'amber_gbsa') / 'remote_snapshot.json')
        if isinstance(snapshot, dict) and isinstance(snapshot.get('results'), list):
            for result in snapshot.get('results', []):
                if not isinstance(result, dict):
                    continue
                cid, duration = result.get('id'), result.get('elapsed_s')
                if (isinstance(cid, int) and not isinstance(cid, bool) and
                        cid in successful and 'error' not in result and
                        isinstance(duration, (int, float)) and not isinstance(duration, bool)
                        and math.isfinite(duration) and duration >= 0):
                    times[cid] = duration
    return list(times.values())


def completion_summary(args, iteration, stage, elapsed):
    successful, failed = database.stage_summary_results(args.db_path, iteration, stage)
    count = len(successful) + len(failed)
    fields = [f'compounds={count} (ok={len(successful)}, failed={len(failed)})']
    if stage == 'glide':
        timeout_failed = failed & database.glide_timeout_failed_ids(args.db_path, iteration)
        fields.append(f'timeout_failed={len(timeout_failed)}')
    fields.append(f'wall/compound={elapsed / count:.2f}s' if count else 'wall/compound=n/a')
    if stage in {'prime_gbsa', 'amber-gbsa'}:
        values = _native_times(args.output, iteration, stage, successful)
        if values:
            fields.append(f'native job n={len(values)}/{len(successful)} '
                          f'avg/median/min/max={statistics.mean(values):.1f}/'
                          f'{statistics.median(values):.1f}/{min(values):.1f}/{max(values):.1f}s')
        else:
            fields.append('native job time=n/a')
    return '; '.join(fields)
