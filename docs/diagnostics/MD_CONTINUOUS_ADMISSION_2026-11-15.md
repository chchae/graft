# Continuous MD GPU admission — 2026-11-15

The user observed 18 completed MD replicas in Prime while only two MDs remained
active out of a 20-slot wave, leaving GPU capacity idle despite queued work.
The controller reconciled/admitted MD only once at worker entry; subsequent
serial CPU extraction, transfer and Prime polling could block another refill.
There was already first-ready Prime submission, but no admission monitor during
those CPU operations.

## Change

`src/graft/md_gbsa.py` retains full startup reconciliation, then runs MD-only
admission in a worker-local thread during CPU processing, with five seconds
between passes. Already verified unstarted entries are not re-queried on every
monitor cycle; startup/recovery still queries all unresolved native owners.
Dynamic ready-task selection also revisits MDs completing behind the CPU cursor.
Each replica is processed at most once in a worker pass. Pure GPU waits still
return to the existing driver polling loop.

Only the main thread performs DB access, guarded Prime work, result validation,
commits and cleanup. Both progress writers use the same checkpoint lock, never
held over SSH/native operations. Shutdown stops further admission and joins an
in-flight native query. Admission errors propagate after the current CPU call;
missing replies preserve the pre-submission durable GPU reservation.

No scientific helpers, dispatch adapter, settings, saved checksums, JobIds,
production DB, native inputs or results were manually changed. All 40 source
files in the refactoring protected-file inventory match the pre-task snapshot.
The current iteration-6 native helper SHA256 and both local frozen helper hashes
remain unchanged. Existing native jobs continue; subsequent driver-launched
MD workers load the controller change, without a native restart.

## Verification

- Before change: both new starvation regressions fail while the 17 existing
  dispatch tests pass (2 failed, 17 passed).
- After change: 204 focused MD/dispatch/local/retry/orchestration tests pass.
- Full unmodified baseline: 2,904 passed, 7 skipped, 0 failures.
- Full candidate: 2,909 passed, 7 skipped, 0 failures.
- Individual existing test node outcomes are identical; five new tests pass.
- Existing 330 multiprocessing warnings and seven unavailable-Torch skips remain.
- Compileall and package/CLI/MD imports pass.

New tests cover local and remote Prime modes with a CPU call deliberately
blocked until the queued MD launches; a newly-ready earlier replica; checkpoint
publication before submission; both progress writers; lost-reply ownership and
error propagation; CPU-error stop/join without launching another MD.

Raw pre/post JUnit XML, logs, commands, input SHA256 inventory and node comparison
are in `/tmp/graft-md-rolling-ce18b2ej/`. Tests use isolated source/project copies
and isolated lock/remote-registry paths, not the production project.

## Limits

Five seconds is a monitor sleep interval, not a start-time SLA. Native queries,
MD output collection, frame preparation, transport, host capacity and Prime pool
reservations still impose latency. No real calculation was submitted, canceled
or restarted for verification; actual throughput improvement is unmeasured.
The already-running Python subprocess keeps its loaded code until its ordinary
exit; the next stage subprocess uses this update.
