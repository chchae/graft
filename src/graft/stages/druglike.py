"""Assess and prioritize the complete generated iteration without exclusions."""
import argparse
import csv
import os
from pathlib import Path
import tempfile

from graft.paths import stage_directory
from graft import _core as c, database
from graft.chemistry.druglike import rank_druglike
from graft.stages.generate import store_iteration_descriptors
from graft.stages._logging import work_started, timed_stage


@timed_stage('Drug likeness worker')
def _run_stage(args, iteration):
    if iteration < 1:
        raise ValueError('Drug likeness requires iteration >= 1')
    database.validate_generation_population(args.db_path, iteration, args.target_count)
    if not database.uses_raw_generation(args.db_path, iteration):
        print(f'Iteration {iteration}: retaining legacy drug likeness/SA selection.')
        return
    if database.generation_readiness_reason(args.db_path, iteration, args.target_count) or database.stage_is_running(args.db_path, 'generate'):
        print(f'Iteration {iteration}: drug likeness waiting for complete generation.')
        return
    work_started(f'Iteration {iteration}: assessing drug likeness of all generated database compounds.')
    store_iteration_descriptors(args, iteration)
    if database.druglike_completion_reason(args.db_path, iteration) is not None:
        selections = rank_druglike(database.druglike_population(args.db_path, iteration))
        database.apply_druglike_selection(args.db_path, iteration, args.target_count,
                                          selections, priority_only=True)
    rows = database.druglike_report(args.db_path, iteration)
    priorities = {row['id']: row['druglike_priority'] for row in
                  rank_druglike(database.druglike_population(args.db_path, iteration))}
    folder = stage_directory(args.output, iteration, 'druglike')
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder, prefix='selection-')
    try:
        with os.fdopen(fd, 'w', newline='') as stream:
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(['id', 'smiles', 'rdk_qed', 'rdk_sa_score', 'rdk_ro5_violations', 'passed', 'reason', 'priority'])
            writer.writerows((*row, priorities[row[0]]) for row in rows)
        os.replace(temporary, folder / 'druglike_selection.csv')
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f'Iteration {iteration}: drug likeness eligible={sum(row[-2] for row in rows)}/{len(rows)}; priority: RO5 ascending, QED descending, SA ascending.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Iteration-wide drug likeness priority')
    parser.add_argument('project_toml', type=Path)
    parser.add_argument('--iteration', type=int, required=True)
    parser.add_argument('--schrodinger', type=Path)
    ns = parser.parse_args(argv)
    args = c.configure_project(ns.project_toml, ns.schrodinger)
    with database.stage_lock(args.db_path, 'druglike'):
        _run_stage(args, ns.iteration)


if __name__ == '__main__':
    main()
